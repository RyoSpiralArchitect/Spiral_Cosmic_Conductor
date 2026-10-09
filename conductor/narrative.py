# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 Ryo Higa / SpiralReality
"""Narrative Kernel-inspired chronology: a faithful projection, not an invented story."""
from __future__ import annotations

from typing import Any


PHASES = {
    "created": "begin", "dispatched": "delegation", "route_selected": "selection",
    "patch_committed": "proposal", "patch_rejected": "boundary",
    "verified": "accepted", "verification_failed": "contradiction",
    "worker_failed": "failure", "dispatch_refused": "refusal",
    "run_started": "begin", "stopped": "termination",
}


def narrative_timeline(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build a reversible, hash-linked event index without adding model interpretation."""
    items: list[dict[str, Any]] = []
    for e in events:
        phase = PHASES.get(e["kind"])
        if phase is None:
            continue
        p = e["payload"]
        items.append({"seq": e["seq"], "phase": phase, "event": e["kind"],
                      "actor": e["actor"], "claim_id": p.get("claim_id"),
                      "event_sha256": e["hash"],
                      "evidence_level": "uninterpreted_event_projection"})
    return items
