# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 Ryo Higa / SpiralReality. All rights reserved.
"""One transactional commit authority; independent workers have scoped proposals.

The SQLite file is a local trust boundary, NOT a sandbox or distributed consensus.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .types import (Commit, ContractError, Dispatch, Task, canonical, digest,
                    empty_claim, equal_scalar, parse_patch, source_or_compute)


class Ledger:
    def __init__(self, path: str | Path, task: Task):
        self.task = Task.from_dict(task.to_dict())
        self.path = str(path)
        if self.path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.db = sqlite3.connect(self.path, isolation_level=None, timeout=5.0, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS claims(id TEXT PRIMARY KEY, body TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS grants(
            token TEXT PRIMARY KEY, worker TEXT NOT NULL, role TEXT NOT NULL,
            reads TEXT NOT NULL, writes TEXT NOT NULL, sources TEXT NOT NULL,
            allowed TEXT NOT NULL, expires REAL NOT NULL, used INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS events(
            seq INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, actor TEXT NOT NULL,
            payload TEXT NOT NULL, ts REAL NOT NULL, prev TEXT NOT NULL, hash TEXT NOT NULL);
        """)
        try:
            with self._transaction():
                old = self.db.execute("SELECT value FROM meta WHERE key='task_hash'").fetchone()
                if old and old[0] != self.task.fingerprint:
                    raise ContractError("Existing ledger belongs to a different task")
                if not old:
                    self.db.execute("INSERT INTO meta VALUES('task_hash',?)", (self.task.fingerprint,))
                    self.db.execute("INSERT INTO meta VALUES('task',?)", (canonical(self.task.to_dict()),))
                    for slot in self.task.slots:
                        self._save(empty_claim(slot))
                    self._event("created", "runtime", {"task_hash": self.task.fingerprint, "version": "0.2.0"})
            self.audit()
        except BaseException:
            self.db.close()
            raise

    @contextmanager
    def _transaction(self) -> Iterator[None]:
        with self._lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                yield
                self.db.execute("COMMIT")
            except BaseException:
                self.db.execute("ROLLBACK")
                raise

    def close(self) -> None:
        self.db.close()

    def __enter__(self) -> Ledger:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    @contextmanager
    def controller_lease(self, lifetime_s: float) -> Iterator[None]:
        owner = uuid.uuid4().hex
        with self._transaction():
            row = self.db.execute("SELECT value FROM meta WHERE key='controller_lease'").fetchone()
            old = json.loads(row[0]) if row else {}
            if old.get("until", 0) > time.time():
                raise ContractError("Another controller owns this ledger")
            value = {"owner": owner, "until": time.time() + lifetime_s}
            self.db.execute("INSERT OR REPLACE INTO meta VALUES('controller_lease',?)", (canonical(value),))
        try:
            yield
        finally:
            with self._transaction():
                row = self.db.execute("SELECT value FROM meta WHERE key='controller_lease'").fetchone()
                if row and json.loads(row[0]).get("owner") == owner:
                    self.db.execute("DELETE FROM meta WHERE key='controller_lease'")

    def _save(self, claim: dict[str, Any]) -> None:
        self.db.execute("INSERT OR REPLACE INTO claims VALUES(?,?)", (claim["id"], canonical(claim)))

    def snapshot(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            return {r["id"]: json.loads(r["body"]) for r in self.db.execute("SELECT id,body FROM claims ORDER BY id")}

    def _event(self, kind: str, actor: str, payload: dict[str, Any]) -> dict[str, Any]:
        row = self.db.execute("SELECT seq,hash FROM events ORDER BY seq DESC LIMIT 1").fetchone()
        prev = row["hash"] if row else "0" * 64
        seq = row["seq"] + 1 if row else 1
        ts = time.time()
        record = {"seq": seq, "kind": kind, "actor": actor, "payload": payload, "ts": ts, "prev": prev}
        record["hash"] = digest(record)
        self.db.execute("INSERT INTO events(seq,kind,actor,payload,ts,prev,hash) VALUES(?,?,?,?,?,?,?)",
                        (seq, kind, actor, canonical(payload), ts, prev, record["hash"]))
        return record

    def note(self, kind: str, payload: dict[str, Any], actor: str = "runtime") -> None:
        # Only trusted controller code calls this method. Never pass a model-supplied kind/payload here.
        if "changes" in payload or kind == "created":
            raise ContractError("Reserved audit fields")
        with self._transaction():
            self._event(kind, actor, payload)

    def events(self) -> list[dict[str, Any]]:
        with self._lock:
            return [{"seq": r["seq"], "kind": r["kind"], "actor": r["actor"],
                     "payload": json.loads(r["payload"]), "ts": r["ts"], "prev": r["prev"], "hash": r["hash"]}
                    for r in self.db.execute("SELECT * FROM events ORDER BY seq")]

    def issue(self, worker: str, role: str, writes: set[str], ttl_s: float = 120.0) -> Dispatch:
        if role not in {"proposer", "repairer", "critic"} or not writes or not writes <= self.task.by_id.keys():
            raise ContractError("Unknown role or target scope")
        if not isinstance(worker, str) or not worker or ttl_s <= 0:
            raise ContractError("Invalid grant")
        allowed = ["request_backtrack"] if role == "critic" else ["add_claim", "revise_claim", "attach_evidence"]
        read_ids = self.task.closure(writes)
        source_ids = {self.task.by_id[c].source for c in read_ids if self.task.by_id[c].source}
        token = uuid.uuid4().hex
        with self._transaction():
            state = self.snapshot()
            reads = {cid: state[cid]["rev"] for cid in sorted(read_ids)}
            self.db.execute("INSERT INTO grants(token,worker,role,reads,writes,sources,allowed,expires) VALUES(?,?,?,?,?,?,?,?)",
                            (token, worker, role, canonical(reads), canonical(sorted(writes)),
                             canonical(sorted(source_ids)), canonical(allowed), time.time() + ttl_s))
            view = {"goal": self.task.goal, "task_id": self.task.id, "role": role,
                    "write_ids": sorted(writes), "allowed_ops": allowed,
                    "slots": [s.to_dict() for s in self.task.slots if s.id in read_ids],
                    "claims": {cid: state[cid] for cid in sorted(read_ids)},
                    "sources": {sid: self.task.sources[sid] for sid in sorted(source_ids)}}
            feedback = {}
            history = self.events()
            for cid in sorted(writes):
                for event in reversed(history):
                    kind, payload = event["kind"], event["payload"]
                    # A later committed proposal or verification supersedes old feedback.
                    if ((kind == "patch_committed" and any(op["claim_id"] == cid for op in payload["ops"]))
                            or (kind == "verified" and payload["claim_id"] == cid)):
                        break
                    if kind == "patch_rejected" and cid in payload.get("write_ids", []):
                        feedback[cid] = {"kind": kind, "reason": payload["reason"], "failed_claim_id": cid}
                        break
                    if kind == "verification_failed" and cid in payload["invalidated"]:
                        feedback[cid] = {"kind": kind, "reason": payload["reason"],
                                         "failed_claim_id": payload["claim_id"]}
                        break
            if feedback:
                view["repair_feedback"] = feedback
            self._event("dispatched", worker, {"role": role, "read_ids": sorted(read_ids),
                                             "write_ids": sorted(writes), "view_bytes": len(canonical(view).encode())})
        return Dispatch(token, json.loads(canonical(view)), worker, role)

    def _invalidate(self, state: dict[str, dict[str, Any]], root: str, reason: str,
                    include_root: bool = True) -> list[str]:
        affected = {root}
        for slot in self.task.slots:
            if any(d in affected for d in slot.deps):
                affected.add(slot.id)
        changed = []
        for slot in self.task.slots:
            cid = slot.id
            if cid not in affected or (cid == root and not include_root):
                continue
            claim = state[cid]
            if claim["status"] in {"missing", "invalidated"}:
                continue
            claim.update(status="invalidated", verification=None,
                         note=reason if cid == root else f"Dependency invalidated: {root}")
            claim["rev"] += 1
            changed.append(cid)
        return changed

    def apply(self, ticket: str, raw_patch: Any) -> Commit:
        # Malformed requests still consume the one-shot grant and are auditable.
        try:
            ops = parse_patch(raw_patch)
            parse_error = None
        except (ContractError, ValueError, TypeError, OverflowError, RecursionError):
            ops, parse_error = [], "invalid_patch_schema"
        with self._transaction():
            grant = self.db.execute("SELECT * FROM grants WHERE token=?", (ticket,)).fetchone()
            actor = grant["worker"] if grant else "unknown"

            def reject(reason: str) -> Commit:
                # Only host-authorized targets are logged; never echo arbitrary patch data.
                scope = json.loads(grant["writes"]) if grant else []
                self._event("patch_rejected", actor, {"reason": reason, "write_ids": scope})
                return Commit(False, reason)

            if not grant:
                return reject("unknown_grant")
            if grant["used"]:
                return reject("replayed_grant")
            self.db.execute("UPDATE grants SET used=1 WHERE token=?", (ticket,))
            if time.time() > grant["expires"]:
                return reject("expired_grant")
            if parse_error:
                return reject(parse_error)
            state = self.snapshot()
            reads = json.loads(grant["reads"])
            if any(state[cid]["rev"] != rev for cid, rev in reads.items()):
                return reject("stale_read_set")
            writes, sources = set(json.loads(grant["writes"])), set(json.loads(grant["sources"]))
            allowed = set(json.loads(grant["allowed"]))
            original = json.loads(canonical(state))
            invalidated: set[str] = set()
            for op in ops:
                cid, action = op["claim_id"], op["op"]
                if cid not in writes or action not in allowed:
                    return reject("out_of_scope")
                if not set(op["evidence_ids"]) <= sources:
                    return reject("unknown_or_unseen_evidence")
                claim, slot = state[cid], self.task.by_id[cid]
                if action == "request_backtrack":
                    if not set(op["evidence_ids"]) <= set(slot.evidence_ids):
                        return reject("evidence_outside_target_scope")
                    if claim["status"] in {"missing", "invalidated"}:
                        return reject("nothing_to_challenge")
                    # A critic can request re-verification, never confer truth or edit values.
                    claim.update(status="challenged", verification=None, note=op["reason"])
                    claim["rev"] += 1
                    invalidated.update(self._invalidate(state, cid, "Parent challenged", include_root=False))
                    continue
                if action == "add_claim" and claim["status"] != "missing":
                    return reject("already_exists_use_revise")
                if action in {"revise_claim", "attach_evidence"} and claim["status"] == "missing":
                    return reject("claim_does_not_exist")
                refs = (sorted(set(claim["evidence_ids"] + op["evidence_ids"]))
                        if action == "attach_evidence" else op["evidence_ids"])
                if not set(refs) <= set(slot.evidence_ids):
                    return reject("evidence_outside_target_scope")
                if slot.kind == "fact" and slot.source not in refs:
                    return reject("missing_source_reference")
                if any(state[d]["status"] not in {"candidate", "verified"} for d in slot.deps):
                    return reject("dependency_unavailable")
                invalidated.update(self._invalidate(state, cid, "Parent revised", include_root=False))
                claim["rev"] += 1
                claim["value_rev"] += 1
                if action != "attach_evidence":
                    claim["value"] = op["value"]
                claim["evidence_ids"] = refs
                claim.update(status="candidate", proposer=actor, verification=None, note=op["reason"],
                             basis={d: state[d]["value_rev"] for d in slot.deps})
            changes = [state[s.id] for s in self.task.slots if state[s.id] != original[s.id]]
            for claim in changes:
                self._save(claim)
            self._event("patch_committed", actor, {"role": grant["role"], "ops": ops,
                                                   "invalidated": sorted(invalidated), "changes": changes})
            return Commit(True, "committed", tuple(c["id"] for c in changes), tuple(sorted(invalidated)))

    def verify(self, cid: str) -> Commit:
        """Trusted finite-domain checker, not a general truth oracle.

        Facts are checked against the supplied fixture. Derived claims are checked
        against verified dependencies. Sources themselves are not authenticated.
        """
        if cid not in self.task.by_id:
            raise ContractError("Unknown claim")
        with self._transaction():
            state = self.snapshot()
            claim, slot = state[cid], self.task.by_id[cid]
            if claim["status"] not in {"candidate", "challenged"}:
                return Commit(False, "not_pending")
            if any(state[d]["status"] != "verified" for d in slot.deps):
                return Commit(False, "dependencies_not_verified")
            if any(claim["basis"].get(d) != state[d]["value_rev"] for d in slot.deps):
                reason = "stale_dependency_basis"
            else:
                try:
                    expected = source_or_compute(self.task, slot, {d: state[d]["value"] for d in slot.deps})
                    reason = "value_mismatch" if not equal_scalar(claim["value"], expected) else "verified"
                    if slot.kind == "fact" and slot.source not in claim["evidence_ids"]:
                        reason = "missing_source_reference"
                except ContractError:
                    reason = "invalid_computation"
            if reason != "verified":
                invalidated = self._invalidate(state, cid, f"Checker: {reason}")
                changes = [state[x] for x in invalidated]
                for c in changes:
                    self._save(c)
                self._event("verification_failed", "closed-world-checker", {
                    "claim_id": cid, "reason": reason, "invalidated": invalidated, "changes": changes})
                return Commit(False, reason, tuple(invalidated), tuple(invalidated))
            claim["rev"] += 1
            claim.update(status="verified", note="Verified within supplied task only",
                         verification={"method": "closed-world-v1", "task_hash": self.task.fingerprint,
                                       "basis": dict(claim["basis"])})
            self._save(claim)
            self._event("verified", "closed-world-checker", {"claim_id": cid, "changes": [claim]})
            return Commit(True, "verified", (cid,))

    def audit(self) -> dict[str, Any]:
        """Replay events and compare current state. No external timestamp/signature guarantee."""
        with self._transaction():
            events, live = self.events(), self.snapshot()
        replay = {s.id: empty_claim(s) for s in self.task.slots}
        prev = "0" * 64
        if not events or events[0]["kind"] != "created" or events[0]["payload"].get("task_hash") != self.task.fingerprint:
            raise ContractError("Invalid audit genesis")
        for i, event in enumerate(events, 1):
            body = {k: v for k, v in event.items() if k != "hash"}
            if event["seq"] != i or event["prev"] != prev or digest(body) != event["hash"]:
                raise ContractError("Audit chain mismatch")
            for claim in event["payload"].get("changes", []):
                if claim["id"] not in replay:
                    raise ContractError("Unknown replay claim")
                replay[claim["id"]] = claim
            prev = event["hash"]
        if replay != live:
            raise ContractError("Ledger differs from event replay")
        return {"events": len(events), "head_sha256": prev, "state_replay_matches": True}
