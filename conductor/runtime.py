# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 Ryo Higa / SpiralReality. All rights reserved.
"""Event-driven, budgeted controller. The runtime commits; workers only propose."""
from __future__ import annotations

import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .adapters import AdapterError, Worker
from .control import BudgetRouter
from .multipolar import MultipolarBroker
from .ledger import Ledger
from .types import ContractError, canonical


@dataclass(frozen=True)
class Limits:
    max_calls: int = 24
    max_cost_units: int = 40
    max_reserved_output_tokens: int = 24576
    deadline_s: float = 120.0
    call_timeout_s: float = 30.0
    max_attempts_per_claim: int = 3

    def __post_init__(self) -> None:
        for value in (self.max_calls, self.max_cost_units, self.max_reserved_output_tokens, self.max_attempts_per_claim):
            if type(value) is not int or value <= 0:
                raise ContractError("Budgets must be positive integers")
        if not (0 < self.deadline_s <= 3600 and 0 < self.call_timeout_s <= 600):
            raise ContractError("Invalid deadline or timeout")


class Conductor:
    def __init__(self, ledger: Ledger, fast: Worker, careful: Worker, limits: Limits | None = None,
                 speculation: bool = True, router: BudgetRouter | None = None,
                 broker: MultipolarBroker | None = None):
        self.ledger, self.fast, self.careful = ledger, fast, careful
        self.limits = limits or Limits()
        self.speculation = speculation
        self.router = router or BudgetRouter()
        self.broker = broker

    def _usage(self) -> dict[str, int]:
        events = self.ledger.events()
        calls = [e["payload"] for e in events if e["kind"] == "call_reserved"]
        return {"calls": len(calls), "cost_units": sum(x["cost_units"] for x in calls),
                "reserved_output_tokens": sum(x["max_output_tokens"] for x in calls)}

    def run(self) -> dict[str, Any]:
        with self.ledger.controller_lease(self.limits.deadline_s + self.limits.call_timeout_s + 60):
            return self._run_owned()

    def _run_owned(self) -> dict[str, Any]:
        started = time.monotonic()
        attempts: dict[str, int] = {}
        for e in self.ledger.events():
            if e["kind"] == "call_reserved":
                cid = e["payload"]["claim_id"]
                attempts[cid] = attempts.get(cid, 0) + 1
        stop_reason = "no_progress"
        self.ledger.note("run_started", {"limits": asdict(self.limits), "speculation": self.speculation})
        active_ids = self.ledger.task.closure(set(self.ledger.task.targets))
        plan = [s for s in self.ledger.task.slots if s.id in active_ids]
        while True:
            elapsed = time.monotonic() - started
            state = self.ledger.snapshot()
            if all(state[s.id]["status"] == "verified" for s in plan):
                stop_reason = "verified_targets"
                break
            if elapsed >= self.limits.deadline_s:
                stop_reason = "deadline"
                break
            # Candidates may be speculated downstream, but never rendered as verified.
            eligible = [s for s in plan if state[s.id]["status"] in {"missing", "invalidated"}
                        and all(state[d]["status"] in ({"candidate", "verified"} if self.speculation else {"verified"}) for d in s.deps)]
            pending = [s for s in plan if state[s.id]["status"] in {"candidate", "challenged"}
                       and all(state[d]["status"] == "verified" for d in s.deps)]
            # Prioritize deterministic checks when speculation is disabled or no new work is possible.
            if pending and (not self.speculation or not eligible):
                self.ledger.verify(pending[0].id)
                continue
            if not eligible:
                stop_reason = "dependency_deadlock"
                break
            slot = eligible[0]
            if attempts.get(slot.id, 0) >= self.limits.max_attempts_per_claim:
                stop_reason = "attempt_limit"
                break
            used = self._usage()
            route = self.router.choose(
                claim_id=slot.id, attempts=attempts.get(slot.id, 0),
                invalidated=state[slot.id]["status"] == "invalidated",
                available_cost_units=self.limits.max_cost_units - used["cost_units"],
                fast_cost_units=self.fast.cost_units,
                careful_cost_units=self.careful.cost_units,
            )
            if route.label == "none":
                if pending:
                    self.ledger.verify(pending[0].id)
                    continue
                stop_reason = "budget_exhausted"
                break
            worker, role = ((self.fast, route.role) if route.label == "fast"
                            else (self.careful, route.role))
            if self.broker and not self.broker.authorize_dispatch(
                    worker.name, read_ids=self.ledger.task.closure({slot.id}), write_ids={slot.id}):
                self.ledger.note("dispatch_refused", {"claim_id": slot.id, "reason": "broker_scope_denied"})
                stop_reason = "policy_refused"
                break
            would_exceed = (used["calls"] + 1 > self.limits.max_calls
                            or used["cost_units"] + worker.cost_units > self.limits.max_cost_units
                            or used["reserved_output_tokens"] + worker.max_output_tokens > self.limits.max_reserved_output_tokens)
            if would_exceed:
                # Free deterministic verification can still salvage already-proposed work.
                if pending:
                    self.ledger.verify(pending[0].id)
                    continue
                stop_reason = "budget_exhausted"
                break
            remaining = self.limits.deadline_s - (time.monotonic() - started)
            if remaining <= 0:
                stop_reason = "deadline"
                break
            # Route is host-controlled, observable, and carries no trust in model output.
            self.ledger.note("route_selected", {**route.to_dict(), "claim_id": slot.id})
            # Charge/reserve BEFORE dispatch. Failures and interrupted calls are not free retries.
            self.ledger.note("call_reserved", {"claim_id": slot.id, "role": role,
                             "cost_units": worker.cost_units, "max_output_tokens": worker.max_output_tokens}, worker.name)
            attempts[slot.id] = attempts.get(slot.id, 0) + 1
            dispatch = self.ledger.issue(worker.name, role, {slot.id}, ttl_s=remaining + 1)
            call_start = time.monotonic()
            try:
                reply = worker.propose(dispatch, timeout_s=min(self.limits.call_timeout_s, remaining))
                latency_ms = (time.monotonic() - call_start) * 1000
                self.ledger.note("call_returned", {"claim_id": slot.id, "latency_ms": latency_ms,
                                                  "usage": asdict(reply.usage)}, worker.name)
                if time.monotonic() - started > self.limits.deadline_s:
                    self.ledger.note("late_reply_discarded", {"claim_id": slot.id}, worker.name)
                    stop_reason = "deadline"
                    break
                if reply.usage.completion_tokens is not None and reply.usage.completion_tokens > worker.max_output_tokens:
                    self.ledger.note("provider_output_overrun", {"claim_id": slot.id}, worker.name)
                    self.ledger.apply(dispatch.ticket, {})
                    stop_reason = "provider_output_overrun"
                    break
                self.ledger.apply(dispatch.ticket, reply.patch)
            except (AdapterError, ContractError) as exc:
                self.ledger.note("worker_failed", {"claim_id": slot.id, "error_type": type(exc).__name__}, worker.name)
                # Reject/consume grant without logging the failed model payload.
                self.ledger.apply(dispatch.ticket, {})
        self.ledger.note("stopped", {"reason": stop_reason, "wall_ms": (time.monotonic() - started) * 1000})
        return self.report(stop_reason)

    def report(self, stop_reason: str = "inspected") -> dict[str, Any]:
        state, events = self.ledger.snapshot(), self.ledger.events()
        needed = self.ledger.task.closure(set(self.ledger.task.targets))
        complete = all(state[c]["status"] == "verified" for c in needed)
        results = {c: state[c]["value"] for c in self.ledger.task.targets if state[c]["status"] == "verified"}
        usage = self._usage()
        returned = [e["payload"] for e in events if e["kind"] == "call_returned"]
        usage["request_bytes"] = sum(e["usage"]["request_bytes"] for e in returned)
        usage["response_bytes"] = sum(e["usage"]["response_bytes"] for e in returned)
        token_reports = [e["usage"] for e in returned]
        token_totals = {k: (sum(u[k] for u in token_reports) if token_reports and all(u[k] is not None for u in token_reports) else None)
                        for k in ("prompt_tokens", "completion_tokens")}
        failed_checks = [e["payload"] for e in events if e["kind"] == "verification_failed"]
        routes = {e["actor"] for e in events if e["kind"] == "call_reserved"}
        return {"runtime": "Spiral Conductor Runtime", "version": "0.2.0", "task_id": self.ledger.task.id,
                "evidence_level": "closed-world fixture consistency only", "complete": complete,
                "stop_reason": stop_reason, "results": results,
                "pending": [c for c in sorted(needed) if state[c]["status"] != "verified"],
                "usage": {**usage, **token_totals}, "workers_used": sorted(routes),
                "verification_failures": len(failed_checks),
                "invalidated_claim_events": sum(len(e["invalidated"]) for e in failed_checks),
                "audit": self.ledger.audit(), "claims": state}


def render_answer(report: dict[str, Any]) -> str:
    """Non-invasive presentation layer. No model may embellish verified values."""
    lines = ["Spiral Conductor Runtime v0.2.0", f"Task: {report['task_id']}",
             f"State: {'VERIFIED (within supplied fixture)' if report['complete'] else 'INCOMPLETE'}",
             f"Stop: {report['stop_reason']}"]
    for key, value in report["results"].items():
        lines.append(f"  {key}: {canonical(value)}")
    if report["pending"]:
        lines.append("Pending: " + ", ".join(report["pending"]))
    lines.extend([f"Worker calls: {report['usage']['calls']}",
                  f"Verification failures: {report['verification_failures']}",
                  f"Dependency invalidations: {report['invalidated_claim_events']}",
                  f"Audit replay: {report['audit']['state_replay_matches']}",
                  "No general reasoning-performance or security-certification claim."])
    return "\n".join(lines)


def export_run(ledger: Ledger, report: dict[str, Any], out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / "task.json").write_text(json_pretty(ledger.task.to_dict()), encoding="utf-8")
    (out / "report.json").write_text(json_pretty(report), encoding="utf-8")
    (out / "answer.txt").write_text(render_answer(report) + "\n", encoding="utf-8")
    with (out / "events.jsonl").open("w", encoding="utf-8") as f:
        for e in ledger.events():
            f.write(canonical(e) + "\n")
    trace = ["# Execution trace", "", "Only proposals, state changes and check results; no hidden reasoning logs.", "",
             "| Event | Actor | Action |", "|---|---|---|"]
    for e in ledger.events():
        p = e["payload"]
        brief = p.get("claim_id", p.get("reason", ", ".join(p.get("write_ids", []))))
        if e["kind"] == "patch_committed":
            brief = "; ".join(f"{x['op']}({x['claim_id']})={canonical(x['value'])}" for x in p["ops"])
        if e["kind"] == "verification_failed":
            brief += " -> invalidate: " + ", ".join(p["invalidated"])
        cells = [str(e["seq"]), e["actor"], e["kind"] + (": " + brief if brief else "")]
        trace.append("| " + " | ".join(c.replace("|", "\\|").replace("\n", " ") for c in cells) + " |")
    (out / "TRACE.md").write_text("\n".join(trace) + "\n", encoding="utf-8")


def json_pretty(value: Any) -> str:
    import json
    return json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
