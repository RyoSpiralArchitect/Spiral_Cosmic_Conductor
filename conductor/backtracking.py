# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 Ryo Higa / SpiralReality
"""EBRT-inspired *structural* backward credit from a verifier event.

This is not the separate EBRT project's differentiable credit assignment.
Only dependency-graph invalidations and repairs are represented here.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .types import ContractError, Task


@dataclass(frozen=True)
class StructuralCredit:
    event_seq: int
    event_hash: str
    root_claim: str
    affected: tuple[str, ...]
    reason: str
    mechanism: str = "dependency_dag_invalidations_no_gradients"

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "affected": list(self.affected)}


def derive_structural_credit(task: Task, events: list[dict[str, Any]]) -> list[StructuralCredit]:
    by_id = task.by_id
    credits = []
    for e in events:
        if e["kind"] != "verification_failed":
            continue
        root, affected = e["payload"]["claim_id"], tuple(e["payload"]["invalidated"])
        if root not in by_id or root not in affected or not set(affected) <= by_id.keys():
            raise ContractError("Invalid credit event root or affected claims")
        # Ground the expected set in declared dependencies, never in worker text.
        descendants = {root}
        for slot in task.slots:
            if any(d in descendants for d in slot.deps):
                descendants.add(slot.id)
        if not set(affected) <= descendants:
            raise ContractError("Unrelated claim was invalidated")
        credits.append(StructuralCredit(e["seq"], e["hash"], root, affected,
                                        e["payload"]["reason"]))
    return credits
