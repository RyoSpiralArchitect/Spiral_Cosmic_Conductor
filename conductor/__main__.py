# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 Ryo Higa / SpiralReality. All rights reserved.
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .adapters import ScriptedWorker, workers_from_config
from .cosmic import run_cosmic, verify_bundle
from .ledger import Ledger
from .runtime import Conductor, Limits, export_run, render_answer
from .scenarios import capacity_task
from .types import ContractError, Task, strict_json


def main() -> int:
    ap = argparse.ArgumentParser(description="Local shared-state / scoped-authority reasoning prototype")
    sub = ap.add_subparsers(dest="command", required=True)
    for command in ("demo", "run", "cosmic-demo", "cosmic-run"):
        p = sub.add_parser(command)
        p.add_argument("--out", type=Path, default=Path("runs") / command)
        p.add_argument("--resume", action="store_true")
        p.add_argument("--no-speculation", action="store_true")
        p.add_argument("--max-calls", type=int, default=24)
        p.add_argument("--cost-units", type=int, default=40)
        p.add_argument("--output-token-budget", type=int, default=24576)
        p.add_argument("--deadline", type=float, default=120)
        if command in {"run", "cosmic-run"}:
            p.add_argument("--task", type=Path, required=True)
            p.add_argument("--models", type=Path, required=True)
    p = sub.add_parser("audit")
    p.add_argument("run_dir", type=Path)
    args = ap.parse_args()
    try:
        if args.command == "audit":
            task = Task.from_dict(strict_json((args.run_dir / "task.json").read_text(encoding="utf-8")))
            if not (args.run_dir / "ledger.sqlite").is_file():
                raise ContractError("Ledger not found")
            with Ledger(args.run_dir / "ledger.sqlite", task) as ledger:
                audit = ledger.audit()
            if (args.run_dir / "artifact_manifest.json").exists():
                audit["artifact_manifest"] = verify_bundle(args.run_dir)
            print(json.dumps(audit, indent=2))
            return 0
        if args.command in {"demo", "cosmic-demo"}:
            task = capacity_task()
            fast = ScriptedWorker("scripted-fast", cost_units=1, corrupt={"workers"})
            careful = ScriptedWorker("scripted-careful", cost_units=3)
            print("DEMO: scripted workers and a deterministic checker; no language model is running.")
        else:
            task = Task.from_dict(strict_json(args.task.read_text(encoding="utf-8")))
            fast, careful = workers_from_config(strict_json(args.models.read_text(encoding="utf-8")))
            print("LOCAL MODEL RUN: data is sent only to explicitly configured loopback servers.")
        if args.out.exists() and any(args.out.iterdir()) and not args.resume:
            raise ContractError("Output directory is not empty; choose a new directory or use --resume")
        limits = Limits(max_calls=args.max_calls, max_cost_units=args.cost_units,
                        max_reserved_output_tokens=args.output_token_budget, deadline_s=args.deadline)
        if args.command.startswith("cosmic-"):
            result = run_cosmic(task, fast, careful, args.out, limits=limits,
                                speculation=not args.no_speculation, resume=args.resume)
            report = json.loads(result.report_path.read_text(encoding="utf-8"))
            print(render_answer(report))
            print(f"Cosmic receipt: {result.receipt_path}")
            print(f"Capsules: {result.capsules}, credit events: {result.structural_credit_events}")
            return 0 if result.complete else 2
        with Ledger(args.out / "ledger.sqlite", task) as ledger:
            report = Conductor(ledger, fast, careful, limits, speculation=not args.no_speculation).run()
            export_run(ledger, report, args.out)
        print(render_answer(report))
        print(f"Run files: {args.out}")
        return 0 if report["complete"] else 2
    except (ContractError, OSError, ValueError, TypeError, KeyError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
