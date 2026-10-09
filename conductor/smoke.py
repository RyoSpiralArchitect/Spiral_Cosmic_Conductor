# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 Ryo Higa / SpiralReality. All rights reserved.
"""Fixed synthetic mechanics panel. NOT a language-model benchmark."""
from __future__ import annotations

import argparse
from pathlib import Path

from .adapters import ScriptedWorker
from .ledger import Ledger
from .runtime import Conductor, json_pretty
from .scenarios import capacity_task


def panel() -> dict:
    rows = []
    for case in range(20):
        workers = 2 + case % 5
        per_worker = 40 + 5 * (case % 4)
        demand = workers * per_worker + (-10 if case % 2 == 0 else 10)
        task = capacity_task(workers, per_worker, demand)
        for mode, fault, speculation in [("scripted_clean", False, True),
                                          ("scripted_fault_speculative", True, True),
                                          ("scripted_fault_guarded", True, False)]:
            fast = ScriptedWorker("scripted-fast", corrupt={"workers"} if fault else set())
            careful = ScriptedWorker("scripted-careful", cost_units=3)
            with Ledger(":memory:", task) as ledger:
                out = Conductor(ledger, fast, careful, speculation=speculation).run()
                site_calls = sum(e["kind"] == "call_reserved" and e["payload"].get("claim_id") == "site_name" for e in ledger.events())
                checks = {"target_matches_fixture": out["results"].get("can_deliver") == (workers * per_worker >= demand),
                          "all_targets_verified": out["complete"],
                          "independent_branch_called_once": site_calls == 1,
                          "audit_replays": out["audit"]["state_replay_matches"]}
                rows.append({"case": case, "mode": mode, "calls": out["usage"]["calls"],
                             "invalidated_claim_events": out["invalidated_claim_events"], "checks": checks})
    return {"label": "Synthetic runtime-mechanics panel, not LLM quality evaluation",
            "task_cases": 20, "conditions": 3, "runs": len(rows),
            "all_checks_passed": all(all(r["checks"].values()) for r in rows), "rows": rows}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=Path("runs/mechanics.json"))
    args = ap.parse_args()
    result = panel()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json_pretty(result), encoding="utf-8")
    print(f"{result['runs']} scripted runs; invariant checks passed: {result['all_checks_passed']}")
    print("Not a model-quality, speedup, energy, or statistical-significance result.")
    raise SystemExit(0 if result["all_checks_passed"] else 1)
