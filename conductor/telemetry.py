# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 Ryo Higa / SpiralReality
"""A2E-inspired observation counters; never pretend counts are joules or quality."""
from __future__ import annotations

from typing import Any


def extract_telemetry(events: list[dict[str, Any]], report: dict[str, Any]) -> dict[str, Any]:
    routed = [e["payload"] for e in events if e["kind"] == "route_selected"]
    return {
        "kind": "event_derived_telemetry_v1",
        "calls": report["usage"]["calls"],
        "relative_cost_units": report["usage"]["cost_units"],
        "request_bytes": report["usage"]["request_bytes"],
        "response_bytes": report["usage"]["response_bytes"],
        "prompt_tokens_reported": report["usage"]["prompt_tokens"],
        "completion_tokens_reported": report["usage"]["completion_tokens"],
        "verification_failures": report["verification_failures"],
        "dependency_invalidations": report["invalidated_claim_events"],
        "routed_calls": len(routed),
        "routes": routed,
        "measured_energy_joules": None,
        "meaning_density_score": None,
        "latency_ms_per_request": [e["payload"]["latency_ms"] for e in events if e["kind"] == "call_returned"],
        "caveat": "No energy measurement or validated semantic-quality metric",
    }
