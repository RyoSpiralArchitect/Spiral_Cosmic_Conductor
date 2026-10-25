# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 Ryo Higa / SpiralReality
"""Source-conductor pilot. Simulation by default; live transport is explicit."""
from __future__ import annotations

import argparse
from copy import deepcopy
from dataclasses import asdict
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import urllib.error
import zipfile

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from conductor.adapters import AdapterError
from conductor.source_alignment import SourcePolicy, candidate_units
from conductor.text_conductor import TextConductor
from conductor.text_workflow import open_text_memory
from conductor.types import ContractError, canonical, strict_json
from experiments.provider_pilot import CallBudget, PROVIDERS, ProviderWorker, load_zshrc_literals, token_count
from experiments.text_memory_pilot import append, sha, wire_body, write_json

FIXTURE = REPO / "tests/fixtures/source_alignment_tool_shelf.json"
MAX_TOKENS = 8192


def envelope(request, provider):
    wire_schema = deepcopy(request.payload["schema"])
    # Gemini expands bounded outer arrays of objects containing bounded arrays
    # into too many decoder states. Keep nested reference bounds and ID enums;
    # the host still requires every source/candidate row exactly once.
    if provider == "gemini":
        for field in wire_schema["properties"].values():
            if field.get("type") == "array":
                field.pop("minItems", None)
                field.pop("maxItems", None)
    result = {"model": PROVIDERS[provider].model, "stream": False, "temperature": 0,
              "messages": [{"role": "system", "content": request.payload["instructions"]},
                           {"role": "user", "content": canonical(request.payload["input"])}],
              "response_format": {"type": "json_schema", "json_schema": {
                  "name": request.payload["role"], "strict": True, "schema": wire_schema}}}
    result["max_completion_tokens" if provider == "openai" else "max_tokens"] = MAX_TOKENS
    if provider == "openai":
        result["store"] = False
    else:
        result["reasoning_effort"] = "none"
    return result


def transport_failure(exc):
    """Keep safe HTTP diagnostics, never exception text or provider bodies."""
    result = {"status": "transport_failed", "error_type": type(exc).__name__}
    if isinstance(exc, urllib.error.HTTPError):
        result["http_status"] = exc.code
        result["provider_error_kind"] = "http_error"
        try:
            body = exc.read(16_384).lower()
            if b"schema" in body and b"too many states" in body:
                result["provider_error_kind"] = "schema_complexity"
        except Exception:
            pass  # Missing diagnostic data must not hide the original failure.
        finally:
            exc.close()
    return result


def simulated(request, fixture, scenario):
    if request.payload["role"] == "source_auditor":
        value = fixture["before_assessment"] if request.payload["round"] == 0 or scenario == "unresolved" else fixture["after_assessment"]
    else:
        value = strict_json(canonical(fixture["repair"]))
        if scenario == "unresolved":
            value["units"] = [{"id": u["id"], "text": u["text"]} for u in candidate_units(fixture["candidate"])]
    return canonical({"model": "SIMULATED", "choices": [{"finish_reason": "stop", "message": {"content": canonical(value)}}], "usage": {}}).encode()


def verify_manifest(folder):
    saved = strict_json((folder / "manifest.json").read_text())["sha256"]
    for name, expected in saved.items():
        path = (folder / name).resolve()
        if not path.is_relative_to(folder.resolve()) or sha(path.read_bytes()) != expected:
            raise ContractError("Source capture integrity mismatch")
    return saved


def run(out: Path, mode="simulated", *, scenario="repair", replay=None, checkpoint=None, policy=None, transport=None):
    if mode not in {"simulated", "live", "replay"} or scenario not in {"repair", "unresolved"}:
        raise ContractError("Invalid source pilot mode")
    if transport is not None and mode != "simulated":
        raise ContractError("Injected transport is simulation-only")
    if out.exists() and (not out.is_dir() or any(out.iterdir())):
        raise ContractError("Use a new or empty output directory")
    source_paths = [*sorted((REPO / "conductor").glob("*.py")), REPO / "experiments/provider_pilot.py",
                    REPO / "experiments/text_memory_pilot.py", Path(__file__).resolve()]
    hashes = {str(p.relative_to(REPO)): sha(p.read_bytes()) for p in source_paths}
    secrets = [os.environ[p.key_env].encode() for p in PROVIDERS.values() if os.environ.get(p.key_env)]
    if mode == "live" and any(not os.environ.get(PROVIDERS[p].key_env) for p in ("gemini", "openai")):
        raise ContractError("Missing source pilot credential")
    recorded, replay_ticks = {}, None
    if mode == "replay":
        if replay is None or checkpoint is not None:
            raise ContractError("Replay needs its own sealed capture")
        sealed = verify_manifest(replay)
        if "timing.json" not in sealed:
            raise ContractError("Replay needs sealed timing data")
        plan = strict_json((replay / "plan.json").read_text())
        if plan["source_sha256"] != hashes:
            raise ContractError("Replay requires the captured implementation")
        config = dict(plan["policy"])
        config["protected_source_ids"] = tuple(config["protected_source_ids"])
        policy = SourcePolicy(**config)
        replay_ticks = strict_json((replay / "timing.json").read_text())["monotonic_ticks"]
        if (not isinstance(replay_ticks, list) or len(replay_ticks) > 2 * policy.max_calls
                or any(type(t) not in (int, float) or not math.isfinite(t) for t in replay_ticks)
                or replay_ticks != sorted(replay_ticks)):
            raise ContractError("Invalid captured timing data")
        fixture = strict_json((replay / "fixture.json").read_text())
        scenario = plan["scenario"]
        for line in (replay / "exchanges.jsonl").read_text().splitlines():
            row = strict_json(line)
            if row["request_slot"] in recorded:
                raise ContractError("Duplicate captured source request")
            recorded[row["request_slot"]] = row
    else:
        policy = policy or SourcePolicy()
        fixture = strict_json(FIXTURE.read_text())
    if checkpoint is not None:
        sealed = verify_manifest(checkpoint.parent)
        if any(str((checkpoint / name).relative_to(checkpoint.parent)) not in sealed
               for name in ("memory.sqlite", "memo.md", "revision.md", "trace.json", "audit.json")):
            raise ContractError("Checkpoint artifacts must be sealed")
    if any(key in canonical(fixture).encode() for key in secrets):
        raise ContractError("Credential-like fixture refused")
    out.mkdir(parents=True, exist_ok=True)
    db = out / "memory.sqlite"
    if mode == "replay":
        shutil.copyfile(replay / "seed.sqlite", db)
    else:
        with TextConductor(db, "source_pilot", policy) as conductor:
            if checkpoint is None:
                conductor.begin(fixture["memo"], fixture["candidate"], origin={"kind": "captured_synthetic_fixture",
                                "fixture_sha256": sha(FIXTURE.read_bytes()), "manual_assessments_are_not_model_input": True})
            else:
                with open_text_memory(checkpoint / "memory.sqlite", checkpoint.name) as previous:
                    docs = {a["ref"]["id"]: a for a in previous.snapshot("controller", {"memo", "revision"})["artifacts"]}
                    if any(key in canonical(docs).encode() for key in secrets):
                        raise ContractError("Credential-like checkpoint refused")
                    if mode == "simulated" and (docs["memo"]["text"] != fixture["memo"] or docs["revision"]["text"] != fixture["candidate"]):
                        raise ContractError("Simulation annotations require the matching captured candidate")
                    conductor.begin_from_text_memory(previous)
    shutil.copyfile(db, out / "seed.sqlite")
    write_json(out / "fixture.json", fixture)
    plan = {"mode": mode, "scenario": scenario, "source_sha256": hashes, "policy": asdict(policy),
            "seed_sha256": sha((out / "seed.sqlite").read_bytes()), "max_output_tokens": MAX_TOKENS,
            "automatic_transport_retries": 0, "repo_commit": subprocess.check_output(["git", "-C", str(REPO), "rev-parse", "HEAD"], text=True).strip(),
            "providers": {role: asdict(PROVIDERS[provider]) for role, provider in (("source_auditor", "gemini"), ("repairer", "openai"))},
            "simulation_claim": "Manual assessment/repair fixture tests routing only; no automated semantic-detection claim."}
    write_json(out / "plan.json", plan)
    with zipfile.ZipFile(out / "source.zip", "w", zipfile.ZIP_DEFLATED) as archive:
        for path in source_paths:
            archive.write(path, str(path.relative_to(REPO)))
    exchanges, matched, ticks = [], [], []

    def clock():
        if replay_ticks is None:
            value = time.monotonic()
        else:
            if len(ticks) >= len(replay_ticks):
                raise ContractError("Replay timing exhausted")
            value = replay_ticks[len(ticks)]
        ticks.append(value)
        return value

    def worker(request):
        provider = "gemini" if request.payload["role"] == "source_auditor" else "openai"
        payload = envelope(request, provider)
        body = wire_body(payload)
        if len(body) > 128_000 or any(key in body for key in secrets):
            raise ContractError("Request storage guard")
        name = request.slot
        (out / (name + ".request.json")).write_bytes(body)
        row = {"request_slot": name, "role": request.payload["role"], "round": request.payload["round"],
               "request_sha256": sha(body), "provider": provider, "status": "reserved", "usage": {"prompt_tokens": None, "completion_tokens": None}}
        try:
            if mode == "replay":
                saved = recorded[name]
                if saved["request_sha256"] != sha(body) or (replay / (name + ".request.json")).read_bytes() != body:
                    raise ContractError("Replay request mismatch")
                matched.append(name)
                response_path = replay / (name + ".response.json")
                if not response_path.exists():
                    row.update(status="transport_failed", error_type=saved["error_type"])
                    for key in ("http_status", "provider_error_kind"):
                        if key in saved:
                            row[key] = saved[key]
                    raise AdapterError("Captured transport failure")
                raw = response_path.read_bytes()
            elif transport is not None:
                try:
                    raw = transport(request, payload, body)
                except Exception as exc:
                    row.update(transport_failure(exc))
                    raise AdapterError("Transport failed") from None
            elif mode == "live":
                live = ProviderWorker(provider, "careful", "live", CallBudget(policy.max_calls), lambda _: None)
                try:
                    raw = live._transport(payload, body, request.timeout_s)
                except Exception as exc:
                    row.update(transport_failure(exc))
                    raise AdapterError("Transport failed") from None
            else:
                raw = simulated(request, fixture, scenario)
            if len(raw) > 1_000_000 or any(key in raw for key in secrets):
                raise ContractError("Response storage guard")
            (out / (name + ".response.json")).write_bytes(raw)
            row["response_sha256"] = sha(raw)
            result = strict_json(raw.decode())
            if len(result["choices"]) != 1:
                raise AdapterError("Unexpected response count")
            choice = result["choices"][0]
            message, usage = choice["message"], result.get("usage") or {}
            row.update(usage={k: token_count(usage, k) for k in row["usage"]}, finish_reason=choice.get("finish_reason"))
            if (choice.get("finish_reason") != "stop" or message.get("tool_calls") or message.get("function_call") or message.get("refusal")
                    or not isinstance(message.get("content"), str) or (row["usage"]["completion_tokens"] or 0) > MAX_TOKENS):
                raise AdapterError("Incomplete or refused source response")
            row["status"] = "returned"
            return message["content"]
        except BaseException as exc:
            if row["status"] != "transport_failed":
                row.update(status="failed", error_type=type(exc).__name__)
            raise
        finally:
            exchanges.append(row)
            append(out / "exchanges.jsonl", row)

    report = {"state": "aborted", "semantic_status": "unassessed"}
    try:
        with TextConductor(db, "source_pilot", policy) as conductor:
            try:
                report = conductor.run(worker, clock=clock)
            finally:
                report = conductor.report()
                write_json(out / "report.json", report)
                write_json(out / "events.json", conductor.memory.events())
                for slot in conductor.memory.audit()["slots_present"]:
                    artifact = conductor.memory.history("controller", slot)[-1]
                    suffix = ".md" if slot == "memo" or slot.startswith("candidate_") else ".json"
                    (out / (slot + suffix)).write_text(artifact["text"], encoding="utf-8")
        if mode == "replay":
            if ticks != replay_ticks:
                raise ContractError("Replay timing differs")
            if set(matched) != set(recorded) or exchanges != list(recorded.values()):
                raise ContractError("Replay exchanges differ")
            if report != strict_json((replay / "report.json").read_text()):
                raise ContractError("Replay public outcome differs")
        return report
    finally:
        write_json(out / "timing.json", {"monotonic_ticks": ticks})
        write_json(out / "summary.json", {"mode": mode, "live_api_calls": len(exchanges) if mode == "live" else 0,
                   "call_attempts": len(exchanges), "matched_replay_calls": matched,
                   "known_usage": {k: sum(r["usage"][k] or 0 for r in exchanges) for k in ("prompt_tokens", "completion_tokens")},
                   "unknown_usage_calls": sum(any(v is None for v in r["usage"].values()) for r in exchanges),
                   "state": report["state"], "semantic_status": "unassessed"})
        write_json(out / "manifest.json", {"claim": "local_integrity_only", "sha256": {
            str(p.relative_to(out)): sha(p.read_bytes()) for p in sorted(out.rglob("*")) if p.is_file() and p != out / "manifest.json"}})


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--mode", choices=("simulated", "live", "replay"), default="simulated")
    parser.add_argument("--scenario", choices=("repair", "unresolved"), default="repair")
    parser.add_argument("--checkpoint", type=Path)
    parser.add_argument("--replay", type=Path)
    parser.add_argument("--credentials-from-zshrc", action="store_true")
    args = parser.parse_args()
    try:
        if args.credentials_from_zshrc:
            if args.mode != "live":
                raise ContractError("Credential loading is live-only")
            load_zshrc_literals(Path.home() / ".zshrc")
        report = run(args.out, args.mode, scenario=args.scenario, replay=args.replay, checkpoint=args.checkpoint)
        print(canonical({k: v for k, v in report.items() if k != "result_text"}))
        sys.exit(0 if report["state"] == "released_candidate" else 1)
    except Exception as exc:
        print(canonical({"status": "failed", "error_type": type(exc).__name__}))
        sys.exit(1)
