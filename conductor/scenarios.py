# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 Ryo Higa / SpiralReality. All rights reserved.
"""Synthetic tasks; source documents and answers are generated test fixtures."""
from __future__ import annotations

from .types import Task


def capacity_task(workers: int = 4, per_worker: int = 75, demand: int = 240) -> Task:
    return Task.from_dict({
        "id": "capacity-check", "goal": "Can the named site meet demand with the current staffing? Use the current source, not the archived roster.",
        "sources": {
            "current": {"title": "Current fixture roster", "text": f"Current workers: {workers}. Each worker processes {per_worker} items. Requested items: {demand}.",
                        "facts": {"workers": workers, "per_worker": per_worker, "demand": demand}},
            "site": {"title": "Independent site record", "text": "Site name is Beppu.", "facts": {"site_name": "Beppu"}},
            "archived": {"title": "Archived fixture roster, not authoritative for this task", "text": f"Previous workers: {workers - 1}.",
                         "facts": {"workers": workers - 1}}
        },
        "slots": [
            {"id": "workers", "kind": "fact", "source": "current", "field": "workers"},
            {"id": "per_worker", "kind": "fact", "source": "current", "field": "per_worker"},
            {"id": "demand", "kind": "fact", "source": "current", "field": "demand"},
            {"id": "site_name", "kind": "fact", "source": "site", "field": "site_name"},
            {"id": "capacity", "kind": "mul", "deps": ["workers", "per_worker"]},
            {"id": "can_deliver", "kind": "ge", "deps": ["capacity", "demand"]}
        ], "targets": ["can_deliver", "site_name"]})
