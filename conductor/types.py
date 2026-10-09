# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 Ryo Higa / SpiralReality. All rights reserved.
"""Small, deliberately closed schemas. Model outputs never contain authority."""
from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from typing import Any

VERSION = "0.2.0"
ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,79}$")
MAX_INT = 10**12
KINDS = {"fact", "add", "sub", "mul", "ge", "le", "eq", "and"}


class ContractError(ValueError):
    pass


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def strict_json(text: str) -> Any:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key, value in items:
            if key in out:
                raise ContractError("Duplicate JSON key")
            out[key] = value
        return out

    def reject_constant(_: str) -> None:
        raise ContractError("Non-finite JSON number")

    try:
        return json.loads(text, object_pairs_hook=pairs, parse_constant=reject_constant)
    except (ValueError, TypeError, RecursionError) as exc:
        raise ContractError("Invalid strict JSON") from exc


def check_id(value: Any) -> str:
    if not isinstance(value, str) or ID_RE.fullmatch(value) is None:
        raise ContractError("Invalid identifier")
    return value


def check_scalar(value: Any) -> Any:
    # No implicit bool/int or int/float equivalence in claims.
    if type(value) is bool:
        return value
    if type(value) is int and abs(value) <= MAX_INT:
        return value
    if isinstance(value, str) and len(value) <= 1000:
        return value
    raise ContractError("Claim values must be bool, bounded int, or short string")


def equal_scalar(left: Any, right: Any) -> bool:
    return type(left) is type(right) and left == right


def exact_keys(value: Any, required: set[str], optional: set[str] | None = None) -> dict[str, Any]:
    if not isinstance(value, dict) or not required <= value.keys():
        raise ContractError("Missing required fields")
    if set(value) - required - (optional or set()):
        raise ContractError("Unexpected fields")
    return value


@dataclass(frozen=True)
class Slot:
    id: str
    kind: str
    deps: tuple[str, ...] = ()
    source: str | None = None
    field: str | None = None

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Slot:
        exact_keys(raw, {"id", "kind"}, {"deps", "source", "field"})
        sid, kind = check_id(raw["id"]), raw["kind"]
        if kind not in KINDS:
            raise ContractError("Unknown slot operation")
        deps = raw.get("deps", [])
        if not isinstance(deps, list) or len(deps) != len(set(deps)):
            raise ContractError("Dependencies must be a unique list")
        deps = tuple(check_id(x) for x in deps)
        source, field = raw.get("source"), raw.get("field")
        if kind == "fact":
            if deps or source is None or field is None:
                raise ContractError("Fact needs source and field, not dependencies")
            check_id(source)
            check_id(field)
        elif len(deps) != 2 or source is not None or field is not None:
            raise ContractError("Derived operations require exactly two dependencies")
        return cls(sid, kind, deps, source, field)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"id": self.id, "kind": self.kind, "deps": list(self.deps)}
        if self.kind == "fact":
            out.update(source=self.source, field=self.field)
        return out


@dataclass(frozen=True)
class Task:
    id: str
    goal: str
    slots: tuple[Slot, ...]
    sources: dict[str, dict[str, Any]]
    targets: tuple[str, ...]

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Task:
        exact_keys(raw, {"id", "goal", "slots", "sources", "targets"})
        check_id(raw["id"])
        if not isinstance(raw["goal"], str) or not 1 <= len(raw["goal"]) <= 4000:
            raise ContractError("Invalid goal")
        if not isinstance(raw["slots"], list) or not 1 <= len(raw["slots"]) <= 128:
            raise ContractError("Expected 1..128 slots")
        slots = tuple(Slot.from_dict(s) for s in raw["slots"])
        ids = [s.id for s in slots]
        if len(ids) != len(set(ids)):
            raise ContractError("Duplicate slot id")
        sources = strict_json(canonical(raw["sources"]))  # own the nested data
        if not isinstance(sources, dict) or not 1 <= len(sources) <= 128:
            raise ContractError("Invalid sources")
        for source_id, source in sources.items():
            check_id(source_id)
            exact_keys(source, {"title", "text", "facts"})
            if any(not isinstance(source[k], str) or len(source[k]) > 8000 for k in ("title", "text")):
                raise ContractError("Invalid source text")
            if not isinstance(source["facts"], dict):
                raise ContractError("Invalid source facts")
            for field, value in source["facts"].items():
                check_id(field)
                check_scalar(value)
        seen: set[str] = set()
        for slot in slots:
            # An explicit topological plan avoids unbounded planner recursion in v0.1.
            if not set(slot.deps) <= seen:
                raise ContractError("Slots must be in dependency order, with no cycles")
            if slot.kind == "fact":
                if slot.source not in sources or slot.field not in sources[slot.source]["facts"]:
                    raise ContractError("Unknown source or field")
            seen.add(slot.id)
        targets_raw = raw["targets"]
        if not isinstance(targets_raw, list) or not targets_raw:
            raise ContractError("No targets")
        targets = tuple(check_id(t) for t in targets_raw)
        if not set(targets) <= seen or len(targets) != len(set(targets)):
            raise ContractError("Invalid targets")
        task = cls(raw["id"], raw["goal"], slots, sources, targets)
        # Validate declared arithmetic without trusting workers.
        expected: dict[str, Any] = {}
        for slot in slots:
            expected[slot.id] = source_or_compute(task, slot, expected)
        return task

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "goal": self.goal, "slots": [s.to_dict() for s in self.slots],
                "sources": self.sources, "targets": list(self.targets)}

    @property
    def by_id(self) -> dict[str, Slot]:
        return {s.id: s for s in self.slots}

    @property
    def fingerprint(self) -> str:
        return digest(self.to_dict())

    def closure(self, ids: set[str]) -> set[str]:
        todo = list(ids)
        out = set(ids)
        while todo:
            for dep in self.by_id[todo.pop()].deps:
                if dep not in out:
                    out.add(dep)
                    todo.append(dep)
        return out


def compute(kind: str, values: list[Any]) -> Any:
    if len(values) != 2:
        raise ContractError("Binary operation requires two values")
    a, b = values
    if kind == "eq":
        return equal_scalar(a, b)
    if kind == "and":
        if type(a) is not bool or type(b) is not bool:
            raise ContractError("and requires bools")
        return a and b
    if type(a) is not int or type(b) is not int:
        raise ContractError("Numeric operation requires integers")
    funcs = {"add": lambda: a + b, "sub": lambda: a - b, "mul": lambda: a * b,
             "ge": lambda: a >= b, "le": lambda: a <= b}
    if kind not in funcs:
        raise ContractError("Unsupported derived operation")
    return check_scalar(funcs[kind]())


def source_or_compute(task: Task, slot: Slot, values: dict[str, Any]) -> Any:
    if slot.kind == "fact":
        return task.sources[slot.source]["facts"][slot.field]
    return compute(slot.kind, [values[d] for d in slot.deps])


def empty_claim(slot: Slot) -> dict[str, Any]:
    return {"id": slot.id, "rev": 0, "value_rev": 0, "status": "missing", "value": None,
            "deps": list(slot.deps), "basis": {}, "evidence_ids": [], "proposer": None,
            "verification": None, "note": ""}


PATCH_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["ops"],
    "properties": {"ops": {"type": "array", "minItems": 1, "maxItems": 8, "items": {
        "type": "object", "additionalProperties": False,
        "required": ["op", "claim_id", "value", "evidence_ids", "reason"],
        "properties": {
            "op": {"type": "string", "enum": ["add_claim", "revise_claim", "attach_evidence", "request_backtrack"]},
            "claim_id": {"type": "string"},
            "value": {"type": ["integer", "boolean", "string", "null"]},
            "evidence_ids": {"type": "array", "items": {"type": "string"}},
            "reason": {"type": "string", "maxLength": 500}
        }}}}
}


def parse_patch(raw: Any) -> list[dict[str, Any]]:
    if isinstance(raw, str):
        if len(raw.encode("utf-8")) > 32000:
            raise ContractError("Patch too large")
        raw = strict_json(raw)
    if len(canonical(raw).encode("utf-8")) > 32000:
        raise ContractError("Patch too large")
    exact_keys(raw, {"ops"})
    if not isinstance(raw["ops"], list) or not 1 <= len(raw["ops"]) <= 8:
        raise ContractError("Expected 1..8 operations")
    seen: set[str] = set()
    for op in raw["ops"]:
        exact_keys(op, {"op", "claim_id", "value", "evidence_ids", "reason"})
        cid = check_id(op["claim_id"])
        if cid in seen:
            raise ContractError("One operation per claim per transaction")
        seen.add(cid)
        if op["op"] not in {"add_claim", "revise_claim", "attach_evidence", "request_backtrack"}:
            raise ContractError("Unknown operation")
        if op["op"] in {"add_claim", "revise_claim"}:
            check_scalar(op["value"])
        elif op["value"] is not None:
            raise ContractError("Non-writing operations require null value")
        refs = op["evidence_ids"]
        if not isinstance(refs, list) or len(refs) > 8 or any(not isinstance(r, str) for r in refs):
            raise ContractError("Invalid evidence list")
        if len(refs) != len(set(refs)):
            raise ContractError("Duplicate evidence reference")
        for ref in refs:
            check_id(ref)
        if not isinstance(op["reason"], str) or len(op["reason"]) > 500:
            raise ContractError("Invalid brief reason")
    return strict_json(canonical(raw["ops"]))


@dataclass(frozen=True)
class Dispatch:
    ticket: str
    view: dict[str, Any]
    worker: str
    role: str


@dataclass(frozen=True)
class Commit:
    accepted: bool
    reason: str
    changed: tuple[str, ...] = ()
    invalidated: tuple[str, ...] = ()


@dataclass(frozen=True)
class Usage:
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    request_bytes: int = 0
    response_bytes: int = 0


@dataclass(frozen=True)
class Reply:
    patch: dict[str, Any]
    usage: Usage = Usage()
