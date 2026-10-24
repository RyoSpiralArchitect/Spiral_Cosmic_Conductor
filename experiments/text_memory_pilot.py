# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 Ryo Higa / SpiralReality
"""Bounded text-memory pilot: simulation first; at most six live attempts.

Each role reopens the same episode database. No prior answer or task-specific
teacher annotations are loaded. Failed stages stop that episode, without retry.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.error
import zipfile

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from conductor.adapters import AdapterError
from conductor.text_workflow import (abstract_teacher, accept, documents, instructions, model_input,
                                     open_text_memory, response_schema, seed, view)
from conductor.types import ContractError, canonical, check_id, exact_keys, strict_json
from experiments.provider_pilot import CallBudget, PROVIDERS, ProviderWorker, load_zshrc_literals, token_count

STAGES = (("proposer", "mistral"), ("critic", "gemini"), ("reviser", "openai"))
MAX_TOKENS, TIMEOUT_S, MAX_CALLS = 3072, 60, 6
DEFAULT_CASES = REPO / "examples/text-memory-cases.json"


def sha(data):
    return hashlib.sha256(data).hexdigest()


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def append(path, value):
    with path.open("a", encoding="utf-8") as f:
        f.write(canonical(value) + "\n")
        f.flush()
        os.fsync(f.fileno())


def read_cases(path):
    cases = strict_json(path.read_text(encoding="utf-8"))
    if not isinstance(cases, list) or not 1 <= len(cases) <= 2:
        raise ContractError("Expected one or two explicitly supplied cases")
    seen = set()
    for case in cases:
        exact_keys(case, {"id", "memo"})
        check_id(case["id"])
        if case["id"] in seen or not isinstance(case["memo"], str) or not 1 <= len(case["memo"]) <= 16_000:
            raise ContractError("Invalid case")
        seen.add(case["id"])
    return cases


def envelope(stage, provider, snapshot):
    payload = {"model": PROVIDERS[provider].model, "stream": False, "temperature": 0,
               "messages": [{"role": "system", "content": instructions(stage)},
                            {"role": "user", "content": canonical(model_input(snapshot))}],
               "response_format": {"type": "json_schema", "json_schema": {
                   "name": "text_" + stage, "strict": True, "schema": response_schema(stage)}}}
    payload["max_completion_tokens" if provider == "openai" else "max_tokens"] = MAX_TOKENS
    if provider == "openai":
        payload["store"] = False
    if provider == "gemini":
        payload["reasoning_effort"] = "none"
    return payload


def simulated(stage, snapshot):
    """Deliberately trivial mechanism-only replies, never scored as generations."""
    if stage == "proposer":
        value = {"text": "SIMULATED: 経路確認用の初稿。原条件を保持する。"}
    elif stage == "critic":
        value = {"findings": [{"id": f"F{i:02d}", "principle_id": p["id"], "source_ids": ["M01"], "draft_ids": ["D01"],
                              "elements": [{"polarity": "neutral", "change": "+", "description": "模擬の注釈。"}],
                              "recommendation": "経路の確認だけを行う。"}
                             for i, p in enumerate(abstract_teacher()["principles"], 1)]}
    else:
        review = strict_json(documents(snapshot)["review"]["text"])
        value = {"sentences": [{"id": "R01", "text": "SIMULATED: 経路確認用の改稿。"}],
                 "decisions": [{"finding_id": f["id"], "disposition": "hold" if i == 5 else "adopt",
                                "reason": "模擬の追跡であり意味的な改善を主張しない。",
                                "output_ids": [] if i == 5 else ["R01"]} for i, f in enumerate(review["findings"])]}
    return canonical({"model": "SIMULATED", "choices": [{"finish_reason": "stop", "message": {"content": canonical(value)}}], "usage": {}}).encode()


class CapturedFailure(Exception):
    def __init__(self, error_type):
        self.error_type = error_type


def run(out, mode="simulated", *, cases_path=DEFAULT_CASES, replay=None, transport=None):
    if mode not in {"simulated", "live", "replay"} or (transport is not None and mode != "simulated"):
        raise ContractError("Invalid experiment mode")
    if out.exists() and (not out.is_dir() or any(out.iterdir())):
        raise ContractError("Use a new or empty output directory")
    cases = read_cases(cases_path)
    source_paths = [*sorted((REPO / "conductor").glob("*.py")), REPO / "experiments/provider_pilot.py", Path(__file__).resolve()]
    source_hashes = {str(p.relative_to(REPO)): sha(p.read_bytes()) for p in source_paths}
    teacher_text = canonical(abstract_teacher())
    secrets = [os.environ[p.key_env].encode() for p in PROVIDERS.values() if os.environ.get(p.key_env)]
    if any(key in canonical(cases).encode() or key in teacher_text.encode() for key in secrets):
        raise ContractError("Credential-like input refused")
    if mode == "live" and any(not os.environ.get(p.key_env) for p in PROVIDERS.values()):
        raise ContractError("Missing provider credential")
    captures = {}
    if mode == "replay":
        if replay is None:
            raise ContractError("Replay capture required")
        recorded_plan = strict_json((replay / "plan.json").read_text())
        if (recorded_plan["source_sha256"] != source_hashes or recorded_plan["cases_sha256"] != sha(canonical(cases).encode())
                or recorded_plan["teacher_sha256"] != sha(teacher_text.encode())):
            raise ContractError("Replay requires frozen source, teacher, and cases")
        manifest = strict_json((replay / "manifest.json").read_text())
        for name, expected in manifest["sha256"].items():
            path = (replay / name).resolve()
            if not path.is_relative_to(replay.resolve()) or sha(path.read_bytes()) != expected:
                raise ContractError("Capture integrity mismatch")
        for line in (replay / "exchanges.jsonl").read_text().splitlines():
            row = strict_json(line)
            key = (row["case"], row["stage"])
            if key in captures:
                raise ContractError("Duplicate captured stage")
            captures[key] = row
    out.mkdir(parents=True, exist_ok=True)
    write_json(out / "cases.json", cases)
    (out / "teacher.json").write_text(teacher_text, encoding="utf-8")
    plan = {"mode": mode, "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "source_sha256": source_hashes, "cases_sha256": sha(canonical(cases).encode()),
            "teacher_sha256": sha(teacher_text.encode()),
            "repo_commit": subprocess.check_output(["git", "-C", str(REPO), "rev-parse", "HEAD"], text=True).strip(),
            "stages": list(STAGES), "providers": {k: asdict(p) for k, p in PROVIDERS.items()},
            "max_calls": min(MAX_CALLS, 3 * len(cases)), "max_output_tokens_per_call": MAX_TOKENS,
            "call_timeout_s": TIMEOUT_S, "transport_retries": 0,
            "scope": "Mechanism pilot of abstract guidance and public state together; not a causal ablation or generalization benchmark.",
            "semantic_status": "unassessed", "weight_updates": False}
    write_json(out / "plan.json", plan)
    with zipfile.ZipFile(out / "source.zip", "w", zipfile.ZIP_DEFLATED) as archive:
        for path in source_paths:
            archive.write(path, str(path.relative_to(REPO)))
    budget, rows, case_results, matched = CallBudget(plan["max_calls"]), [], [], []
    aborted = True
    try:
        for case in cases:
            case_dir = out / case["id"]
            case_dir.mkdir()
            db_path = case_dir / "memory.sqlite"
            with open_text_memory(db_path, case["id"]) as memory:
                seed(memory, case["memo"])
            case_result = {"id": case["id"], "status": "incomplete", "completed_stages": []}
            case_results.append(case_result)
            try:
                for stage, provider in STAGES:
                    # Reopen per role; do not hold a database transaction across HTTP.
                    with open_text_memory(db_path, case["id"]) as memory:
                        snapshot = view(memory, stage)
                    payload = envelope(stage, provider, snapshot)
                    body = canonical(payload).encode()
                    if len(body) > 128_000 or any(key in body for key in secrets):
                        raise ContractError("Request storage/size guard")
                    worker = ProviderWorker(provider, "fast", "live", budget, lambda _: None) if mode == "live" else None
                    budget.reserve()
                    row = {"case": case["id"], "stage": stage, "provider": provider, "mode": mode, "call_index": budget.used,
                           "memory_revision": snapshot["revision"], "request_sha256": sha(body),
                           "model_requested": PROVIDERS[provider].model, "status": "reserved",
                           "usage": {"prompt_tokens": None, "completion_tokens": None}}
                    append(out / "reservations.jsonl", row)
                    (case_dir / (stage + ".request.json")).write_bytes(body)
                    write_json(case_dir / (stage + ".view.json"), snapshot)
                    started = time.monotonic()
                    try:
                        if transport is not None:
                            raw = transport(case, stage, snapshot, payload, body, TIMEOUT_S)
                        elif mode == "live":
                            raw = worker._transport(payload, body, TIMEOUT_S)
                        elif mode == "replay":
                            capture = captures[(case["id"], stage)]
                            previous_body = (replay / case["id"] / (stage + ".request.json")).read_bytes()
                            if previous_body != body or capture["request_sha256"] != sha(body):
                                raise ContractError("Replay request mismatch")
                            matched.append({"case": case["id"], "stage": stage})
                            response_path = replay / case["id"] / (stage + ".response.json")
                            if not response_path.exists():
                                raise CapturedFailure(capture["error_type"])
                            raw = response_path.read_bytes()
                        else:
                            raw = simulated(stage, snapshot)
                        if len(raw) > 1_000_000 or any(key in raw for key in secrets):
                            raise ContractError("Response storage/size guard")
                        (case_dir / (stage + ".response.json")).write_bytes(raw)
                        row["response_sha256"] = sha(raw)
                        result = strict_json(raw.decode())
                        choices, usage = result["choices"], result.get("usage") or {}
                        if not isinstance(choices, list) or len(choices) != 1 or not isinstance(usage, dict):
                            raise ContractError("Invalid response envelope")
                        choice, message = choices[0], choices[0]["message"]
                        content = message.get("content")
                        row.update(model_returned=result.get("model"), finish_reason=choice.get("finish_reason"),
                                   usage={k: token_count(usage, k) for k in ("prompt_tokens", "completion_tokens")})
                        if not isinstance(content, str) or any(key in content.encode() for key in secrets):
                            raise ContractError("Invalid response content")
                        row["raw_content"] = content
                        if (choice.get("finish_reason") != "stop" or message.get("tool_calls") or message.get("function_call")
                                or message.get("refusal") or (row["usage"]["completion_tokens"] or 0) > MAX_TOKENS):
                            raise AdapterError("Incomplete or refused generation")
                        with open_text_memory(db_path, case["id"]) as memory:
                            committed = accept(memory, stage, content, expected_revision=snapshot["revision"])
                            row["event_head_sha256"] = memory.audit()["event_head_sha256"]
                        row.update(status="committed", published=[a["ref"] for a in committed], semantic_status="unassessed")
                        case_result["completed_stages"].append(stage)
                    except BaseException as exc:
                        row.update(status="failed", error_type=exc.error_type if isinstance(exc, CapturedFailure) else type(exc).__name__)
                        if isinstance(exc, urllib.error.HTTPError):
                            row["http_status"] = exc.code
                        if isinstance(exc, CapturedFailure) and "http_status" in capture:
                            row["http_status"] = capture["http_status"]
                        if not isinstance(exc, Exception):
                            raise
                    finally:
                        row["latency_ms"] = round((time.monotonic() - started) * 1000, 3)
                        rows.append(row)
                        append(out / "exchanges.jsonl", row)
                        print(case["id"], stage, row["status"], flush=True)
                    if mode == "replay":
                        capture = captures[(case["id"], stage)]
                        for name in ("status", "error_type", "http_status", "response_sha256", "event_head_sha256", "usage"):
                            if capture.get(name) != row.get(name):
                                raise ContractError("Replay outcome mismatch")
                    if row["status"] == "failed":
                        case_result["status"] = "failed"
                        break
                else:
                    case_result["status"] = "completed"
            finally:
                with open_text_memory(db_path, case["id"]) as memory:
                    audit = memory.audit()
                    case_result["audit"] = audit
                    write_json(case_dir / "audit.json", audit)
                    write_json(case_dir / "events.json", memory.events())
                    for artifact_id in audit["slots_present"]:
                        artifact = memory.history("controller", artifact_id)[-1]
                        suffix = ".json" if artifact_id in {"principles", "review", "trace"} else ".md"
                        (case_dir / (artifact_id + suffix)).write_text(artifact["text"], encoding="utf-8")
            write_json(case_dir / "summary.json", case_result)
        if mode == "replay" and len(matched) != len(captures):
            raise ContractError("Replay did not consume the full capture")
        aborted = False
    finally:
        summary = {"status": "aborted" if aborted else ("completed" if all(c["status"] == "completed" for c in case_results) else "completed_with_failures"),
                   "mode": mode, "calls": budget.used, "live_api_calls": budget.used if mode == "live" else 0,
                   "committed_calls": sum(r["status"] == "committed" for r in rows),
                   "failed_calls": sum(r["status"] == "failed" for r in rows), "cases": case_results, "retries": 0,
                   "known_usage": {k: sum(r["usage"][k] or 0 for r in rows) for k in ("prompt_tokens", "completion_tokens")},
                   "unknown_usage_calls": sum(any(v is None for v in r["usage"].values()) for r in rows),
                   "matched_replay_calls": matched, "semantic_status": "unassessed"}
        write_json(out / "summary.json", summary)
        write_json(out / "manifest.json", {"claim": "local_integrity_only", "sha256": {
            str(p.relative_to(out)): sha(p.read_bytes()) for p in sorted(out.rglob("*")) if p.is_file() and p != out / "manifest.json"}})
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("simulated", "live", "replay"), default="simulated")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--replay", type=Path)
    parser.add_argument("--credentials-from-zshrc", action="store_true")
    args = parser.parse_args()
    try:
        if args.credentials_from_zshrc:
            if args.mode != "live":
                raise ContractError("Credential loading is live-only")
            load_zshrc_literals(Path.home() / ".zshrc")
        result = run(args.out, args.mode, cases_path=args.cases, replay=args.replay)
        print(canonical(result))
        sys.exit(0 if result["status"] == "completed" else 1)
    except Exception as exc:
        print(canonical({"status": "failed", "error_type": type(exc).__name__}))
        sys.exit(1)
