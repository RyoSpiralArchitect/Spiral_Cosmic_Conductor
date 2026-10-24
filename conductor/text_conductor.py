# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 Ryo Higa / SpiralReality
"""Persistent source-check -> bounded repair -> recheck -> release/hold control.

Append this conductor to a text workflow before rendering a candidate as its
result. It freezes a source snapshot; it does not turn model judgment into proof.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import time

from .multipolar import Agent, MultipolarBroker
from .shared_memory import ArtifactRef, MemoryLimits, Publication, SharedMemory, text_hash
from .source_alignment import (AUDITOR_INSTRUCTIONS, REPAIR_INSTRUCTIONS, SourcePolicy,
                               assess, candidate_units, repair, schema, source_units)
from .text_workflow import documents, resolve_span
from .types import ContractError, canonical, digest, strict_json


@dataclass(frozen=True)
class SourceRequest:
    slot: str
    expected_revision: int
    payload: dict
    timeout_s: float


class TextConductor:
    """Own source-based release, independent of the proposal's earlier critic.

    Every call is durably reserved first. An unresolved reservation blocks
    redispatch after a crash. Distinct round slots avoid cyclic repair ancestry.
    The caller's worker must honor timeout_s; late synchronous replies are held.
    """

    def __init__(self, path: Path, episode: str, policy: SourcePolicy | None = None,
                 limits: MemoryLimits | None = None):
        self.policy = policy or SourcePolicy()
        rounds = range(self.policy.max_repairs + 1)
        candidates = {f"candidate_{r}" for r in rounds}
        checks = {f"check_{r}" for r in rounds}
        traces = {f"repair_trace_{r}" for r in rounds if r}
        requests = {f"request_{n}" for n in range(self.policy.max_calls)}
        slots = {"memo": "source", "policy": "guidance", "origin": "trace", "outcome": "trace"}
        slots.update({s: "revision" for s in candidates})
        slots.update({s: "review" for s in checks})
        slots.update({s: "trace" for s in traces | requests})
        common = {"memo", "policy"} | candidates | requests
        broker = MultipolarBroker([
            Agent("controller", frozenset(slots), frozenset({"memo", "policy", "origin", "candidate_0", "outcome"} | requests)),
            Agent("source_auditor", frozenset(common), frozenset(checks)),
            Agent("repairer", frozenset(common | checks), frozenset((candidates - {"candidate_0"}) | traces)),
        ])
        self.memory = SharedMemory(path, episode, broker, slots, limits)
        try:
            if self.memory.audit()["event_count"]:
                self._base()
        except BaseException:
            self.memory.close()
            raise

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.memory.close()

    def begin(self, memo: str, candidate: str, *, origin: dict | None = None):
        candidate_units(candidate)
        policy = self.policy.freeze(memo)
        origin = origin or {"kind": "explicit_host_input"}
        texts = {"memo": memo, "policy": canonical(policy), "origin": canonical(origin), "candidate_0": candidate}
        refs = {k: ArtifactRef(self.memory.episode, k, 1, text_hash(v)) for k, v in texts.items()}
        return self.memory.commit("controller", [Publication("memo", memo), Publication("policy", texts["policy"], (refs["memo"],)),
                                  Publication("origin", texts["origin"]),
                                  Publication("candidate_0", candidate, (refs["memo"], refs["policy"], refs["origin"]))], expected_revision=0)

    def begin_from_text_memory(self, previous: SharedMemory):
        """Import a fresh, structurally linked revision; never mutate its episode."""
        snapshot = previous.snapshot("controller", {"memo", "revision", "trace"})
        docs = documents(snapshot)
        trace = strict_json(docs["trace"]["text"])
        if trace["revision"] != docs["revision"]["ref"]:
            raise ContractError("Imported trace names a different revision")
        units = candidate_units(docs["revision"]["text"])
        if len(units) != len(trace["output_spans"]):
            raise ContractError("Imported revision unitization mismatch")
        for i, unit in enumerate(units, 1):
            span = trace["output_spans"][f"R{i:02d}"]
            if resolve_span(previous, "controller", span, trace["references"]) != unit["text"]:
                raise ContractError("Imported revision span mismatch")
        audit = previous.audit()
        if audit["event_count"] != snapshot["revision"]:
            raise ContractError("Upstream changed during source import")
        return self.begin(docs["memo"]["text"], docs["revision"]["text"], origin={
            "kind": "frozen_text_memory_import", "artifacts": {k: v["ref"] for k, v in docs.items()},
            "semantic_status": "unassessed", "upstream_event_head": audit["event_head_sha256"]})

    def _base(self):
        docs = documents(self.memory.snapshot("controller", {"memo", "policy", "origin", "candidate_0"}))
        if any(d["ref"]["version"] != 1 for d in docs.values()):
            raise ContractError("Source-check inputs are frozen; start a new episode")
        if strict_json(docs["policy"]["text"]) != self.policy.freeze(docs["memo"]["text"]):
            raise ContractError("Source policy cannot change on resume")
        return docs

    def _artifact(self, name):
        return documents(self.memory.snapshot("controller", {name}))[name]

    def _requests(self):
        names = self.memory.audit()["slots_present"]
        return [self._artifact(f"request_{i}") for i in range(self.policy.max_calls) if f"request_{i}" in names]

    def _pending(self):
        requests = self._requests()
        if requests:
            meta = strict_json(requests[-1]["text"])
            if meta["target"] not in self.memory.audit()["slots_present"]:
                return requests[-1]
        return None

    def _finish(self, state, reason, round_index, *, check=None, pending=None):
        names = {"memo", "policy", f"candidate_{round_index}"}
        if check:
            names.add(check)
        if pending:
            names.add(pending)
        snapshot = self.memory.snapshot("controller", names)
        docs = documents(snapshot)
        assessment = strict_json(docs[check]["text"]) if check else None
        outcome = {"state": state, "reason": reason, "round": round_index,
                   "candidate": docs[f"candidate_{round_index}"]["ref"], "source": docs["memo"]["ref"],
                   "assessment": docs[check]["ref"] if check else None,
                   "blocker_ids": assessment["blocker_ids"] if assessment else [],
                   "calls_used": len(self._requests()), "semantic_status": "unassessed",
                   "claim": "Source comparison is a model assessment, not semantic proof."}
        self.memory.commit("controller", [Publication("outcome", canonical(outcome), tuple(ArtifactRef.from_dict(a["ref"]) for a in docs.values()))],
                           expected_revision=snapshot["revision"])

    def _next(self):
        self._base()
        slots = set(self.memory.audit()["slots_present"])
        if "outcome" in slots:
            self._artifact("outcome")  # Check freshness even for terminal results.
            return None
        if self._pending():
            raise ContractError("An unresolved reservation blocks redispatch; explicitly hold it")
        for r in range(self.policy.max_repairs + 1):
            check = f"check_{r}"
            if check not in slots:
                step = ("source_auditor", r)
                break
            assessment = strict_json(self._artifact(check)["text"])
            if not assessment["blocker_ids"]:
                self._finish("released_candidate", "source_check_passed", r, check=check)
                return None
            if r == self.policy.max_repairs:
                self._finish("held", "repair_limit", r, check=check)
                return None
            if f"candidate_{r + 1}" not in slots:
                step = ("repairer", r + 1)
                break
        if len(self._requests()) >= self.policy.max_calls:
            self._finish("held", "call_budget", r, check=check if check in slots else None)
            return None
        return step

    def _payload(self, role, r):
        candidate_slot = f"candidate_{r if role == 'source_auditor' else r - 1}"
        names = {"memo", "policy", candidate_slot}
        if role == "repairer":
            names.add(f"check_{r - 1}")
        snapshot = self.memory.snapshot(role, names)
        docs = documents(snapshot)
        memo, candidate = docs["memo"]["text"], docs[candidate_slot]["text"]
        packet = {"source": {"ref": docs["memo"]["ref"], "units": source_units(memo)},
                  "candidate": {"ref": docs[candidate_slot]["ref"], "units": candidate_units(candidate)},
                  "policy": strict_json(docs["policy"]["text"])}
        blockers = []
        if role == "repairer":
            packet["assessment"] = strict_json(docs[f"check_{r - 1}"]["text"])
            blockers = packet["assessment"]["blocker_ids"]
        payload = {"role": role, "round": r,
                   "instructions": AUDITOR_INSTRUCTIONS if role == "source_auditor" else REPAIR_INSTRUCTIONS,
                   "input": packet, "schema": schema(role, memo, candidate, blockers)}
        return snapshot, payload

    def request(self) -> SourceRequest | None:
        step = self._next()
        if step is None:
            return None
        role, r = step
        snapshot, payload = self._payload(role, r)
        slot = f"request_{len(self._requests())}"
        meta = {"role": role, "round": r, "payload_sha256": digest(payload),
                "expected_reply_revision": snapshot["revision"] + 1,
                "target": f"check_{r}" if role == "source_auditor" else f"candidate_{r}"}
        self.memory.commit("controller", [Publication(slot, canonical(meta), tuple(ArtifactRef.from_dict(a["ref"]) for a in snapshot["artifacts"]))],
                           expected_revision=snapshot["revision"])
        return SourceRequest(slot, meta["expected_reply_revision"], payload, self.policy.call_timeout_s)

    def submit(self, request: SourceRequest, content: str):
        self._base()
        if "outcome" in self.memory.audit()["slots_present"]:
            raise ContractError("Source-check episode already ended")
        reservation = self._pending()
        if not reservation or reservation["ref"]["id"] != request.slot:
            raise ContractError("Unknown or consumed source request")
        meta = strict_json(reservation["text"])
        if (meta["expected_reply_revision"] != request.expected_revision
                or self.memory.audit()["event_count"] != request.expected_revision):
            raise ContractError("Stale source-check reply")
        snapshot, payload = self._payload(meta["role"], meta["round"])
        if digest(payload) != meta["payload_sha256"] or digest(request.payload) != meta["payload_sha256"]:
            raise ContractError("Source request payload changed")
        if not isinstance(content, str) or len(content.encode()) > 64_000:
            raise ContractError("Source response byte limit")
        response = strict_json(content)
        docs = documents(snapshot)
        parents = tuple(ArtifactRef.from_dict(a["ref"]) for a in snapshot["artifacts"]) + (ArtifactRef.from_dict(reservation["ref"]),)
        r = meta["round"]
        if meta["role"] == "source_auditor":
            assessment = assess(response, docs["memo"]["text"], docs[f"candidate_{r}"]["text"], strict_json(docs["policy"]["text"]))
            assessment.update(source=docs["memo"]["ref"], candidate=docs[f"candidate_{r}"]["ref"], policy=docs["policy"]["ref"])
            publications = [Publication(f"check_{r}", canonical(assessment), parents)]
        else:
            assessment = strict_json(docs[f"check_{r - 1}"]["text"])
            text, resolutions = repair(response, assessment["blocker_ids"])
            ref = ArtifactRef(self.memory.episode, f"candidate_{r}", 1, text_hash(text))
            trace = {"assessment": docs[f"check_{r - 1}"]["ref"], "candidate": ref.to_dict(),
                     "resolutions": resolutions, "effect_status": "unassessed", "requires_source_recheck": True}
            publications = [Publication(f"candidate_{r}", text, parents),
                            Publication(f"repair_trace_{r}", canonical(trace), (*parents, ref))]
        self.memory.commit(meta["role"], publications, expected_revision=request.expected_revision)

    def hold_pending(self, reason="interrupted"):
        """Explicitly close an interrupted/failed call; never silently retry it."""
        pending = self._pending()
        if pending is None or "outcome" in self.memory.audit()["slots_present"]:
            raise ContractError("No active source request to hold")
        meta = strict_json(pending["text"])
        r = meta["round"] if meta["role"] == "source_auditor" else meta["round"] - 1
        check = f"check_{r}" if f"check_{r}" in self.memory.audit()["slots_present"] else None
        self._finish("held", reason, r, check=check, pending=pending["ref"]["id"])

    def run(self, worker):
        """worker(request) returns JSON text. Reservations survive interruptions."""
        while True:
            request = self.request()
            if request is None:
                return self.report()
            started = time.monotonic()
            try:
                content = worker(request)
                if time.monotonic() - started > request.timeout_s:
                    self.hold_pending("late_reply")
                    return self.report()
                self.submit(request, content)
            except BaseException as exc:
                if self._pending():
                    self.hold_pending("worker_failed:" + type(exc).__name__)
                if not isinstance(exc, Exception):
                    raise
                return self.report()

    def report(self):
        self._base()
        slots = self.memory.audit()["slots_present"]
        if "outcome" not in slots:
            return {"state": "in_flight" if self._pending() else "pending", "calls_used": len(self._requests()),
                    "semantic_status": "unassessed", "audit": self.memory.audit()}
        outcome = strict_json(self._artifact("outcome")["text"])
        result = {**outcome, "audit": self.memory.audit(), "result_text": None}
        if outcome["state"] == "released_candidate":
            result["result_text"] = self._artifact(outcome["candidate"]["id"])["text"]
        return result
