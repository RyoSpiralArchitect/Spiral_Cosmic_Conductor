# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 Ryo Higa / SpiralReality
"""Small durable public memory for one episode, separate from verified claims.

Only explicit artifacts enter this store. Views never read broker private
contexts. SQLite serializes host commits; model output cannot alter the contract.
This is an in-process policy and integrity mechanism, not a security sandbox.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
from pathlib import Path
import re
import sqlite3
from typing import Any

from .multipolar import Agent, MultipolarBroker
from .types import ContractError, canonical, check_id, digest, exact_keys, strict_json

KINDS = {"source", "guidance", "proposal", "review", "revision", "trace"}


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class MemoryLimits:
    max_events: int | None = 64
    max_artifact_chars: int | None = 16_000
    max_storage_bytes: int | None = 1_000_000
    max_view_chars: int | None = 32_000

    def __post_init__(self) -> None:
        for value in asdict(self).values():
            if value is not None and (type(value) is not int or value <= 0):
                raise ContractError("Invalid memory limit")

    @classmethod
    def unbounded(cls):
        return cls(**{name: None for name in cls.__dataclass_fields__})


def exceeds(value, maximum):
    return maximum is not None and value > maximum


@dataclass(frozen=True)
class ArtifactRef:
    episode: str
    id: str
    version: int
    sha256: str

    def __post_init__(self) -> None:
        check_id(self.episode)
        check_id(self.id)
        if type(self.version) is not int or self.version < 1:
            raise ContractError("Invalid artifact version")
        if not isinstance(self.sha256, str) or re.fullmatch(r"[0-9a-f]{64}", self.sha256) is None:
            raise ContractError("Invalid artifact digest")

    @classmethod
    def from_dict(cls, raw: Any) -> ArtifactRef:
        exact_keys(raw, {"episode", "id", "version", "sha256"})
        return cls(**raw)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Publication:
    id: str
    text: str
    parents: tuple[ArtifactRef, ...] = ()


class SharedMemory:
    """Bounded append-only history, optimistic revisions, and scoped snapshots.

    A commit may atomically publish a revision and its trace. Parents must be
    current and transitively fresh at commit time. Replacing an input retains
    old history but makes descendants unavailable for fresh worker dispatch.
    """

    def __init__(self, path: Path, episode: str, broker: MultipolarBroker,
                 slots: dict[str, str], limits: MemoryLimits | None = None, *,
                 families: dict[str, str] | None = None, metadata: dict | None = None):
        check_id(episode)
        self.families = dict(families or {})
        if not slots or set(slots) & set(self.families):
            raise ContractError("Invalid public artifact slots")
        for name, kind in {**slots, **self.families}.items():
            check_id(name)
            if kind not in KINDS:
                raise ContractError("Invalid public artifact kind")
        if any(not name.endswith("_") for name in self.families):
            raise ContractError("Numbered slot families need an underscore suffix")
        if not broker.agents:
            raise ContractError("Memory needs registered agents")
        self.episode, self.broker = episode, broker
        self.slots, self.agents = dict(slots), dict(broker.agents)
        self.limits = limits or MemoryLimits()
        for a in self.agents.values():
            if not (a.readable | a.writable) <= self.slots.keys() | self.families.keys():
                raise ContractError("Agent scope outside memory contract")
        self.contract = {"schema": "public_memory.v1", "episode": episode, "slots": self.slots,
                         "limits": asdict(self.limits), "agents": {
                             k: {"readable": sorted(a.readable), "writable": sorted(a.writable)}
                             for k, a in sorted(self.agents.items())}}
        if self.families:
            self.contract.update(schema="public_memory.v2", families=self.families)
        if metadata is not None:
            self.contract["metadata"] = strict_json(canonical(metadata))
        self._db = sqlite3.connect(path, timeout=5, isolation_level=None)
        try:
            self._db.execute("BEGIN IMMEDIATE")
            tables = {r[0] for r in self._db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if tables and tables != {"memory_contract", "memory_events"}:
                raise ContractError("Unrelated database")
            if not tables:
                self._db.execute("CREATE TABLE memory_contract (id INTEGER PRIMARY KEY CHECK(id=1), body TEXT NOT NULL)")
                self._db.execute("CREATE TABLE memory_events (seq INTEGER PRIMARY KEY, body TEXT NOT NULL)")
                self._db.execute("INSERT INTO memory_contract VALUES (1, ?)", (canonical(self.contract),))
            self._load()
            self._db.execute("COMMIT")
        except BaseException:
            self._db.execute("ROLLBACK")
            self._db.close()
            raise

    def __enter__(self) -> SharedMemory:
        return self

    def __exit__(self, *args) -> None:
        self.close()

    def close(self) -> None:
        self._db.close()

    def _authorized(self, actor: str, reads: set[str], writes: set[str]) -> None:
        check_id(actor)
        agent = self.agents.get(actor)
        if (agent is None or not {self._scope(s) for s in reads} <= agent.readable
                or not {self._scope(s) for s in writes} <= agent.writable):
            raise ContractError("Public memory scope denied")

    def _scope(self, name):
        check_id(name)
        if name in self.slots:
            return name
        matches = [prefix for prefix in self.families
                   if re.fullmatch(re.escape(prefix) + r"(?:0|[1-9][0-9]*)", name)]
        if len(matches) != 1:
            raise ContractError("Unknown or ambiguous public slot")
        return matches[0]

    def _kind(self, name):
        scope = self._scope(name)
        return self.slots[scope] if scope in self.slots else self.families[scope]

    @staticmethod
    def _fresh(artifact: dict[str, Any], heads: dict[str, Any], history: dict[ArtifactRef, Any]) -> bool:
        # Parents always precede their child in the append-only log, so no cycles.
        pending = [ArtifactRef.from_dict(p) for p in artifact["parents"]]
        seen: set[ArtifactRef] = set()
        while pending:
            parent = pending.pop()
            if parent in seen:
                continue
            seen.add(parent)
            if parent.id not in heads or heads[parent.id]["ref"] != parent.to_dict():
                return False
            pending.extend(ArtifactRef.from_dict(p) for p in history[parent]["parents"])
        return True

    def _apply(self, actor: str, artifacts: Any, heads: dict, history: dict) -> None:
        if not isinstance(artifacts, list) or not artifacts:
            raise ContractError("Expected publications")
        ids: set[str] = set()
        for artifact in artifacts:
            exact_keys(artifact, {"ref", "kind", "text", "parents", "author", "semantic_status"})
            ref = ArtifactRef.from_dict(artifact["ref"])
            text = artifact["text"]
            if (ref.episode != self.episode or ref.id in ids
                    or artifact["kind"] != self._kind(ref.id) or artifact["author"] != actor
                    or artifact["semantic_status"] != "unassessed"
                    or not isinstance(text, str) or not text.strip() or exceeds(len(text), self.limits.max_artifact_chars)
                    or ref.sha256 != text_hash(text)):
                raise ContractError("Invalid publication")
            expected = heads[ref.id]["ref"]["version"] + 1 if ref.id in heads else 1
            if ref.version != expected:
                raise ContractError("Nonsequential artifact version")
            parents = artifact["parents"]
            if not isinstance(parents, list):
                raise ContractError("Invalid parent references")
            parsed = [ArtifactRef.from_dict(p) for p in parents]
            if len(set(parsed)) != len(parsed):
                raise ContractError("Duplicate parent reference")
            self._authorized(actor, {p.id for p in parsed}, {ref.id})
            for parent in parsed:
                if (parent.id == ref.id or parent.episode != self.episode or parent not in history
                        or heads[parent.id]["ref"] != parent.to_dict()
                        or not self._fresh(history[parent], heads, history)):
                    raise ContractError("Missing or stale artifact parent")
            ids.add(ref.id)
            heads[ref.id] = artifact
            history[ref] = artifact
        # Later publications in this same batch must not invalidate an earlier
        # child or create a new artifact whose ancestry is already stale.
        if any(not self._fresh(a, heads, history) for a in artifacts):
            raise ContractError("Publication batch creates stale ancestry")

    def _load(self) -> tuple[list[dict], dict, dict]:
        saved = self._db.execute("SELECT body FROM memory_contract WHERE id=1").fetchone()
        if saved is None or saved[0] != canonical(self.contract):
            raise ContractError("Episode or memory contract mismatch")
        count, size = self._db.execute("SELECT count(*), coalesce(sum(length(cast(body AS BLOB))),0) FROM memory_events").fetchone()
        if exceeds(count, self.limits.max_events) or exceeds(size, self.limits.max_storage_bytes):
            raise ContractError("Memory storage budget exceeded")
        events, heads, history = [], {}, {}
        for index, (seq, body) in enumerate(self._db.execute("SELECT seq,body FROM memory_events ORDER BY seq"), 1):
            event = strict_json(body)
            exact_keys(event, {"seq", "actor", "previous_sha256", "artifacts", "sha256"})
            unsigned = {k: v for k, v in event.items() if k != "sha256"}
            if (seq != index or type(event["seq"]) is not int or event["seq"] != index
                    or event["previous_sha256"] != (events[-1]["sha256"] if events else "0" * 64)
                    or event["sha256"] != digest(unsigned)):
                raise ContractError("Memory event integrity mismatch")
            self._apply(event["actor"], event["artifacts"], heads, history)
            events.append(event)
        return events, heads, history

    def commit(self, actor: str, publications: list[Publication], *, expected_revision: int) -> list[dict]:
        if type(expected_revision) is not int or expected_revision < 0:
            raise ContractError("Invalid expected revision")
        if not isinstance(publications, list) or not publications:
            raise ContractError("Expected publications")
        self._db.execute("BEGIN IMMEDIATE")
        try:
            events, heads, history = self._load()
            if len(events) != expected_revision:
                raise ContractError("Stale memory snapshot")
            if exceeds(len(events) + 1, self.limits.max_events):
                raise ContractError("Memory event budget exceeded")
            reads = {ref.id for p in publications for ref in p.parents}
            writes = {p.id for p in publications}
            self._authorized(actor, reads, writes)
            if not self.broker.authorize_dispatch(actor, read_ids={self._scope(s) for s in reads},
                                                  write_ids={self._scope(s) for s in writes}):
                raise ContractError("Broker refused publication")
            artifacts = []
            for p in publications:
                check_id(p.id)
                if not isinstance(p.text, str):
                    raise ContractError("Unknown publication slot or invalid text")
                version = heads[p.id]["ref"]["version"] + 1 if p.id in heads else 1
                ref = ArtifactRef(self.episode, p.id, version, text_hash(p.text))
                artifacts.append({"ref": ref.to_dict(), "text": p.text, "kind": self._kind(p.id),
                                  "parents": [v.to_dict() for v in p.parents], "author": actor,
                                  "semantic_status": "unassessed"})
            # Validation uses detached projections; a failed batch leaves no rows.
            self._apply(actor, artifacts, heads, history)
            event = {"seq": len(events) + 1, "actor": actor,
                     "previous_sha256": events[-1]["sha256"] if events else "0" * 64, "artifacts": artifacts}
            event["sha256"] = digest(event)
            body = canonical(event)
            saved_size = self._db.execute("SELECT coalesce(sum(length(cast(body AS BLOB))),0) FROM memory_events").fetchone()[0]
            size = saved_size + len(body.encode())
            if exceeds(size, self.limits.max_storage_bytes):
                raise ContractError("Memory storage budget exceeded")
            self._db.execute("INSERT INTO memory_events VALUES (?, ?)", (event["seq"], body))
            self._db.execute("COMMIT")
            return strict_json(canonical(artifacts))
        except BaseException:
            self._db.execute("ROLLBACK")
            raise

    def snapshot(self, actor: str, read_ids: set[str], *, allow_stale: bool = False) -> dict[str, Any]:
        self._authorized(actor, set(read_ids), set())
        events, heads, history = self._load()
        if not set(read_ids) <= heads.keys():
            raise ContractError("Requested public artifact missing")
        artifacts = []
        for name in sorted(read_ids):
            artifact = heads[name]
            fresh = self._fresh(artifact, heads, history)
            if not fresh and not allow_stale:
                raise ContractError("Stale artifact cannot enter fresh dispatch")
            artifacts.append({**artifact, "fresh": fresh})
        view = {"episode": self.episode, "revision": len(events), "artifacts": artifacts,
                "private_context_exported": False, "semantic_status": "unassessed"}
        encoded = canonical(view)
        if exceeds(len(encoded), self.limits.max_view_chars):
            raise ContractError("Memory view budget exceeded; select fewer artifacts")
        return strict_json(encoded)

    def history(self, actor: str, artifact_id: str) -> list[dict]:
        self._authorized(actor, {artifact_id}, set())
        _, _, history = self._load()
        return strict_json(canonical([a for ref, a in history.items() if ref.id == artifact_id]))

    def audit(self) -> dict[str, Any]:
        events, heads, history = self._load()
        return {"event_count": len(events), "artifact_versions": len(history), "slots_present": sorted(heads),
                "event_head_sha256": events[-1]["sha256"] if events else "0" * 64,
                "structural_integrity": True, "semantic_status": "unassessed",
                "private_context_exported": False}

    def events(self) -> list[dict]:
        return self._load()[0]
