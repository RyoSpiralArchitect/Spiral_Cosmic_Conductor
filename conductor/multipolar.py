# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 Ryo Higa / SpiralReality
"""Bounded agent-to-agent semantic projections, not a shared private mind.

A capsule records a scoped public projection. An agent's private context is never
implicitly merged into the controller or another agent. Refusal is an outcome.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from .types import ContractError, check_id, check_scalar, digest


@dataclass(frozen=True)
class Agent:
    name: str
    readable: frozenset[str]
    writable: frozenset[str]

    def __post_init__(self) -> None:
        check_id(self.name)
        for cid in self.readable | self.writable:
            check_id(cid)


@dataclass(frozen=True)
class MeaningCapsule:
    uid: str
    sender: str
    receiver: str
    claim_id: str
    value: bool | int | str | None
    state: str  # candidate / verified / refused / invalidated
    evidence_ids: tuple[str, ...]
    audit_hash: str
    note: str = ""

    def __post_init__(self) -> None:
        for v in (self.uid, self.sender, self.receiver, self.claim_id):
            check_id(v)
        if self.state not in {"candidate", "verified", "refused", "invalidated"}:
            raise ContractError("Invalid capsule state")
        if self.value is not None:
            check_scalar(self.value)
        if len(self.evidence_ids) > 8:
            raise ContractError("Too many capsule evidence references")
        for ref in self.evidence_ids:
            check_id(ref)
        if len(self.note) > 300 or len(self.audit_hash) != 64:
            raise ContractError("Invalid capsule metadata")

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "evidence_ids": list(self.evidence_ids)}


@dataclass(frozen=True)
class Delivery:
    uid: str
    accepted: bool
    reason: str


@dataclass
class Conflict:
    id: str
    claim_id: str
    event_hash: str
    reason: str
    state: str = "unresolved"
    resolved_by: str | None = None


class MultipolarBroker:
    """Explicit routing permissions and preserved disagreement.

    This is an in-process research policy boundary, *not* cryptographic isolation.
    """
    def __init__(self, agents: list[Agent]):
        if len({a.name for a in agents}) != len(agents):
            raise ContractError("Duplicate agent")
        self.agents = {a.name: a for a in agents}
        self._private: dict[str, list[dict[str, Any]]] = {a.name: [] for a in agents}
        self.delivered: list[MeaningCapsule] = []
        self.refusals: list[Delivery] = []
        self.conflicts: dict[str, Conflict] = {}

    def remember_private(self, owner: str, fact: dict[str, Any]) -> None:
        if owner not in self.agents:
            raise ContractError("Unregistered private-context owner")
        # Copy to detach caller mutation. Do not export private context.
        from .types import canonical, strict_json
        self._private[owner].append(strict_json(canonical(fact)))

    def private_context(self, owner: str, requester: str) -> list[dict[str, Any]]:
        if owner != requester or owner not in self.agents:
            raise ContractError("Private context cannot be read by another agent")
        from .types import canonical, strict_json
        return strict_json(canonical(self._private[owner]))

    def authorize_dispatch(self, agent: str, *, read_ids: set[str], write_ids: set[str]) -> bool:
        """Additional runtime policy check; Ledger issues the authoritative grant."""
        known = self.agents.get(agent)
        allowed = (known is not None and bool(write_ids)
                   and read_ids <= known.readable and write_ids <= known.writable)
        if not allowed:
            self.refusals.append(Delivery("dispatch", False, "scope_denied"))
        return allowed

    def deliver(self, capsule: MeaningCapsule) -> Delivery:
        src, dst = self.agents.get(capsule.sender), self.agents.get(capsule.receiver)
        reason = "accepted"
        if src is None or dst is None:
            reason = "unknown_agent"
        elif capsule.claim_id not in src.writable or capsule.claim_id not in dst.readable:
            reason = "scope_denied"
        elif capsule.state == "verified" and capsule.sender != "controller":
            reason = "verified_requires_controller"
        elif capsule.state == "refused":
            reason = "refused_by_sender"
        result = Delivery(capsule.uid, reason == "accepted", reason)
        if result.accepted:
            self.delivered.append(capsule)
        else:
            self.refusals.append(result)
        return result

    def record_conflict(self, claim_id: str, event_hash: str, reason: str) -> Conflict:
        check_id(claim_id)
        if len(event_hash) != 64:
            raise ContractError("Invalid event hash")
        key = "conflict_" + digest([claim_id, event_hash])[:24]
        conflict = Conflict(key, claim_id, event_hash, reason)
        self.conflicts[key] = conflict
        return conflict

    def resolve_conflict(self, conflict_id: str, verified_event_hash: str) -> None:
        if conflict_id not in self.conflicts or len(verified_event_hash) != 64:
            raise ContractError("Invalid conflict resolution")
        c = self.conflicts[conflict_id]
        c.state, c.resolved_by = "resolved_by_verified_claim", verified_event_hash

    def export(self) -> dict[str, Any]:
        """Export only public projections; never serialize the private memory."""
        return {
            "agents": sorted(self.agents),
            "capsules": [c.to_dict() for c in self.delivered],
            "refusals": [asdict(r) for r in self.refusals],
            "conflicts": [asdict(c) for c in self.conflicts.values()],
            "private_context_exported": False,
            "security_status": "policy_boundary_not_cryptographic_isolation",
        }
