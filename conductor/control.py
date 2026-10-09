# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 Ryo Higa / SpiralReality
"""Tri-Phase-inspired deterministic compute routing (not the original engine)."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .types import ContractError


@dataclass(frozen=True)
class Route:
    role: str
    label: str
    reason: str
    estimated_cost_units: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class BudgetRouter:
    """Policy consumes explicit attempt/cost counts, never an invented meaning KPI."""
    def choose(self, *, claim_id: str, attempts: int, invalidated: bool,
               available_cost_units: int, fast_cost_units: int,
               careful_cost_units: int) -> Route:
        if attempts < 0 or available_cost_units < 0:
            raise ContractError("Invalid route counters")
        if attempts == 0 and not invalidated:
            if fast_cost_units <= available_cost_units:
                return Route("proposer", "fast", "fresh_cheap_route", fast_cost_units)
            return Route("stop", "none", "insufficient_fast_budget", 0)
        if careful_cost_units <= available_cost_units:
            return Route("repairer", "careful", "validation_failure_or_retry", careful_cost_units)
        return Route("stop", "none", "insufficient_repair_budget", 0)
