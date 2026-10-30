# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 Ryo Higa / SpiralReality
"""One configured route for faithful and emergent transmission; no implicit retry."""
from __future__ import annotations

import argparse
from copy import deepcopy
from dataclasses import asdict
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time
import zipfile

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from conductor.adapters import AdapterError
from conductor.shared_memory import MemoryLimits, exceeds
from conductor.source_alignment import SourcePolicy
from conductor.text_conductor import TextConductor
from conductor.text_workflow import (TextLimits, abstract_teacher, accept, documents, instructions,
                                     model_input, open_text_memory, resolve_span, response_schema, seed, segments, view)
from conductor.types import ContractError, canonical, check_id, exact_keys, strict_json
from experiments.provider_pilot import CallBudget, PROVIDERS, ProviderWorker, load_zshrc_literals, token_count
from experiments.source_alignment_pilot import transport_failure, verify_manifest
from experiments.text_memory_pilot import append, sha, wire_body, write_json

DEFAULT_CONFIG = REPO / "examples/transmission-comparison.json"
TEXT_STAGES = ("proposer", "critic", "reviser")


def validate_config(config):
    exact_keys(config, {"schema", "source_memo", "routing", "generation", "memory_limits", "text_limits", "source_policy", "cases"})
    if config["schema"] != "transmission_comparison.v1" or not isinstance(config["source_memo"], str) or not config["source_memo"].strip():
        raise ContractError("Invalid comparison source")
    exact_keys(config["routing"], set(TEXT_STAGES) | {"source_auditor", "repairer"})
    if any(p not in PROVIDERS for p in config["routing"].values()):
        raise ContractError("Unknown configured provider")
    generation = config["generation"]
    exact_keys(generation, {"temperature", "max_output_tokens", "timeout_s", "max_response_bytes"})
    for name in ("max_output_tokens", "max_response_bytes"):
        value = generation[name]
        if value is not None and (type(value) is not int or value <= 0):
            raise ContractError("Invalid generation limit")
    for name in ("temperature", "timeout_s"):
        value = generation[name]
        if value is None and name == "timeout_s":
            continue
        if type(value) not in (int, float) or not math.isfinite(value) or value < 0 or (name == "timeout_s" and value == 0):
            raise ContractError("Invalid generation setting")
    MemoryLimits(**config["memory_limits"])
    TextLimits(**config["text_limits"])
    seen = set()
    if not isinstance(config["cases"], list) or not config["cases"]:
        raise ContractError("No comparison cases")
    for case in config["cases"]:
        exact_keys(case, {"id", "intent", "instruction", "source_mode", "protected_source_ids"})
        check_id(case["id"])
        if case["id"] in seen or not all(isinstance(case[k], str) and case[k].strip() for k in ("intent", "instruction")):
            raise ContractError("Invalid comparison case")
        seen.add(case["id"])
        policy_for(config, case).freeze(config["source_memo"])


def policy_for(config, case):
    return SourcePolicy(**{**config["source_policy"], "mode": case["source_mode"],
                           "protected_source_ids": tuple(case["protected_source_ids"])})


def envelope(provider, stage, input_packet, instruction, schema, generation):
    wire_schema = deepcopy(schema)
    if provider == "gemini":
        for field in wire_schema["properties"].values():
            if field.get("type") == "array":
                field.pop("minItems", None)
                field.pop("maxItems", None)
    payload = {"model": PROVIDERS[provider].model, "stream": False, "temperature": generation["temperature"],
               "messages": [{"role": "system", "content": instruction}, {"role": "user", "content": canonical(input_packet)}],
               "response_format": {"type": "json_schema", "json_schema": {"name": stage, "strict": True, "schema": wire_schema}}}
    if generation["max_output_tokens"] is not None:
        payload["max_completion_tokens" if provider == "openai" else "max_tokens"] = generation["max_output_tokens"]
    if provider == "openai":
        payload["store"] = False
    if provider == "gemini":
        payload["reasoning_effort"] = "none"
    return payload


def simulate(stage, packet):
    """Mechanical fixtures only. Never used as live prompts or teacher labels."""
    if stage == "source_auditor":
        src, dst = packet["source"]["units"], packet["candidate"]["units"]
        value = {"sources": [{"id": s["id"], "status": "preserved", "candidate_ids": [dst[i % len(dst)]["id"]],
                               "reason": "模擬の受け渡し経路を確認する。" * 8} for i, s in enumerate(src)],
                 "candidates": [{"id": c["id"], "origin": "source", "source_ids": [src[i % len(src)]["id"]],
                                  "compatibility": "compatible", "reason": "模擬の照合であり意味の正しさを主張しない。"} for i, c in enumerate(dst)]}
    else:
        docs = packet["documents"]
        src = docs["memo"]["content"]["sentences"]
        if stage == "proposer":
            value = {"text": "\n".join(s["text"] for s in src)}
        elif stage == "critic":
            value = {"findings": [{"id": f"F{i:02d}", "principle_id": p["id"],
                                    "source_ids": [src[(i - 1) % len(src)]["id"]],
                                    "draft_ids": [f"D{(i - 1) % len(src) + 1:02d}"],
                                    "elements": [{"polarity": "neutral", "change": "null", "description": "模擬の受け渡し。"}],
                                    "recommendation": "経路だけを確認する。"} for i, p in enumerate(docs["principles"]["content"]["principles"], 1)]}
        elif stage == "reviser":
            value = {"sentences": [{"id": f"R{i:02d}", "text": s["text"]} for i, s in enumerate(src, 1)],
                     "decisions": [{"finding_id": f["id"], "disposition": "adopt", "reason": "模擬の追跡。",
                                    "output_ids": ["R01"]} for f in docs["review"]["content"]["findings"]]}
        else:
            raise ContractError("No simulated repair was configured")
    return canonical({"model": "SIMULATED", "choices": [{"finish_reason": "stop", "message": {"content": canonical(value)}}], "usage": {}}).encode()


class ExchangeLog:
    def __init__(self, out, mode, config, replay=None, transport=None, stop_file=None):
        self.out, self.mode, self.config, self.replay = out, mode, config, replay
        self.transport, self.stop_file, self.rows = transport, stop_file, []
        self.saved = [strict_json(line) for line in (replay / "exchanges.jsonl").read_text().splitlines()] if replay else []
        self.secrets = [os.environ[p.key_env].encode() for p in PROVIDERS.values() if os.environ.get(p.key_env)]

    def call(self, case, stage, packet, instruction, schema, timeout, round_index=None):
        if self.stop_file is not None and self.stop_file.exists():
            raise KeyboardInterrupt()
        provider = self.config["routing"][stage]
        payload = envelope(provider, stage, packet, instruction, schema, self.config["generation"])
        body = wire_body(payload)
        if any(secret in body for secret in self.secrets):
            raise ContractError("Credential-like request refused")
        index = len(self.rows)
        name = f"exchange_{index:04d}"
        (self.out / (name + ".request.json")).write_bytes(body)
        row = {"index": index, "case": case, "stage": stage, "round": round_index, "provider": provider,
               "model_requested": PROVIDERS[provider].model, "request_sha256": sha(body), "status": "reserved",
               "api_attempted": False, "usage": {"prompt_tokens": None, "completion_tokens": None}}
        append(self.out / "reservations.jsonl", row)
        try:
            if self.mode == "replay":
                saved = self.saved[index]
                row["api_attempted"] = saved["api_attempted"]
                if saved["request_sha256"] != sha(body) or (self.replay / (name + ".request.json")).read_bytes() != body:
                    raise ContractError("Comparison replay request mismatch")
                path = self.replay / (name + ".response.json")
                if not path.exists():
                    row.update({k: saved[k] for k in ("status", "error_type", "http_status", "provider_error_kind") if k in saved})
                    raise AdapterError("Captured transport failure")
                raw = path.read_bytes()
            elif self.transport is not None:
                try:
                    raw = self.transport(case, stage, packet, payload)
                except Exception as exc:
                    row.update(transport_failure(exc))
                    raise AdapterError("Transport failed") from None
            elif self.mode == "live":
                row["api_attempted"] = True
                append(self.out / "api_attempts.jsonl", {"index": index, "case": case, "stage": stage})
                worker = ProviderWorker(provider, "careful", "live", CallBudget(None), lambda _: None)
                try:
                    raw = worker._transport(payload, body, timeout, max_response_bytes=self.config["generation"]["max_response_bytes"])
                except Exception as exc:
                    row.update(transport_failure(exc))
                    raise AdapterError("Transport failed") from None
            else:
                raw = simulate(stage, packet)
            if any(secret in raw for secret in self.secrets):
                raise ContractError("Credential-like response refused")
            (self.out / (name + ".response.json")).write_bytes(raw)
            row["response_sha256"] = sha(raw)
            result = strict_json(raw.decode())
            if not isinstance(result["choices"], list) or len(result["choices"]) != 1:
                raise AdapterError("Unexpected response envelope")
            choice = result["choices"][0]
            message, usage = choice["message"], result.get("usage") or {}
            row.update(model_returned=result.get("model"), finish_reason=choice.get("finish_reason"),
                       usage={key: token_count(usage, key) for key in row["usage"]})
            if (choice.get("finish_reason") != "stop" or message.get("tool_calls") or message.get("function_call")
                    or message.get("refusal") or not isinstance(message.get("content"), str)
                    or exceeds(row["usage"]["completion_tokens"] or 0, self.config["generation"]["max_output_tokens"])):
                raise AdapterError("Incomplete or refused provider response")
            row["status"] = "returned"
            return message["content"]
        except BaseException as exc:
            if row["status"] != "transport_failed":
                row.update(status="failed", error_type=type(exc).__name__)
            raise
        finally:
            self.rows.append(row)
            append(self.out / "exchanges.jsonl", row)
            print(case, stage, row["status"], flush=True)


def export_memory(memory, folder):
    write_json(folder / "events.json", memory.events())
    write_json(folder / "audit.json", memory.audit())
    for name in memory.audit()["slots_present"]:
        artifact = memory.history("controller", name)[-1]
        suffix = ".md" if name in {"memo", "draft", "revision"} or name.startswith("candidate_") else ".json"
        (folder / (name + suffix)).write_text(artifact["text"], encoding="utf-8")


def observe(memory, controller):
    """Declared span links and model judgments; no inferred semantic score."""
    review = strict_json(memory.history("controller", "review")[-1]["text"])
    trace = strict_json(memory.history("controller", "trace")[-1]["text"])
    decisions = {d["finding_id"]: d for d in trace["decisions"]}
    for finding in review["findings"]:
        for span in finding["source_spans"] + finding["draft_spans"]:
            resolve_span(memory, "controller", span, review["references"])
    for span in trace["output_spans"].values():
        resolve_span(memory, "controller", span, trace["references"])
    checks = [strict_json(controller.memory.history("controller", slot)[-1]["text"])
              for slot in controller.memory.audit()["slots_present"] if slot.startswith("check_")]
    checks.sort(key=lambda c: int(c["candidate"]["id"].split("_")[-1]))
    rows = []
    memo = memory.history("controller", "memo")[-1]["text"]
    for unit in segments(memo, "M"):
        linked = [f for f in review["findings"] if unit["id"] in f["source_ids"]]
        rows.append({"source_id": unit["id"], "source_text": unit["text"],
                     "declared_findings": [f["id"] for f in linked],
                     "declared_draft_ids": sorted({sid for f in linked for sid in f["draft_ids"]}),
                     "declared_revision_ids": sorted({sid for f in linked for sid in decisions[f["id"]]["output_ids"]}),
                     "checks": [{"candidate": c["candidate"], **next(s for s in c["sources"] if s["id"] == unit["id"])} for c in checks]})
    return {"claim": "Declared correspondences and model judgments only; an unreferenced unit is not proven omitted.",
            "source_rows": rows, "component_order": review["component_order"],
            "component_totals": [sum(f["vector"][i] for f in review["findings"]) for i in range(len(review["component_order"]))],
            "dispositions": {value: sum(d["disposition"] == value for d in trace["decisions"]) for value in ("adopt", "reject", "hold")},
            "stage_chars": {slot: len(memory.history("controller", slot)[-1]["text"]) for slot in ("memo", "draft", "revision")},
            "source_checks": checks, "spans_resolved": True}


def run(out, mode="simulated", *, config_path=DEFAULT_CONFIG, replay=None, transport=None, stop_file=None):
    if mode not in {"simulated", "live", "replay"} or (transport is not None and mode != "simulated"):
        raise ContractError("Invalid comparison mode")
    if out.exists() and (not out.is_dir() or any(out.iterdir())):
        raise ContractError("Use a new output directory")
    sources = [*sorted((REPO / "conductor").glob("*.py")), *sorted((REPO / "experiments").glob("*.py"))]
    hashes = {str(p.relative_to(REPO)): sha(p.read_bytes()) for p in sources}
    saved_ticks = None
    if mode == "replay":
        if replay is None:
            raise ContractError("Replay capture required")
        manifest = verify_manifest(replay)
        if not {"config.json", "plan.json", "timing.json", "exchanges.jsonl", "results.json"} <= manifest.keys():
            raise ContractError("Incomplete comparison capture")
        if strict_json((replay / "plan.json").read_text())["source_sha256"] != hashes:
            raise ContractError("Replay needs the captured implementation")
        config = strict_json((replay / "config.json").read_text())
        saved_ticks = strict_json((replay / "timing.json").read_text())
    else:
        config = strict_json(config_path.read_text())
    validate_config(config)
    memory_limits, text_limits = MemoryLimits(**config["memory_limits"]), TextLimits(**config["text_limits"])
    if mode == "live" and any(not os.environ.get(PROVIDERS[p].key_env) for p in set(config["routing"].values())):
        raise ContractError("Missing comparison credential")
    secrets = [os.environ[p.key_env].encode() for p in PROVIDERS.values() if os.environ.get(p.key_env)]
    if any(s in canonical(config).encode() for s in secrets):
        raise ContractError("Credential-like configuration refused")
    out.mkdir(parents=True, exist_ok=True)
    write_json(out / "config.json", config)
    write_json(out / "plan.json", {"source_sha256": hashes, "mode": mode, "automatic_retries": 0,
               "repo_commit": subprocess.check_output(["git", "-C", str(REPO), "rev-parse", "HEAD"], text=True).strip(),
               "routing": config["routing"], "providers": {p: asdict(PROVIDERS[p]) for p in set(config["routing"].values())},
               "claim": "Same route, source and provider settings; case intent and source policy differ. One pair is not a causal estimate."})
    with zipfile.ZipFile(out / "source.zip", "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sources:
            archive.write(path, str(path.relative_to(REPO)))
    log = ExchangeLog(out, mode, config, replay, transport, stop_file)
    results, ticks, aborted = [], {}, True
    try:
        for case in config["cases"]:
            cid = case["id"]
            folder = out / cid
            folder.mkdir()
            ticks[cid] = []
            policy = policy_for(config, case)
            if saved_ticks is not None:
                values = saved_ticks[cid]
                if not isinstance(values, list) or any(type(v) not in (int, float) or not math.isfinite(v) for v in values) or values != sorted(values):
                    raise ContractError("Invalid replay clock")
            def clock():
                value = time.monotonic() if saved_ticks is None else saved_ticks[cid][len(ticks[cid])]
                ticks[cid].append(value)
                return value
            result = {"id": cid, "intent": case["intent"], "status": "incomplete", "completed_stages": []}
            results.append(result)
            with open_text_memory(folder / "memory.sqlite", cid, memory_limits, text_limits=text_limits, task_brief=True) as memory:
                seed(memory, config["source_memo"], task_brief={"intent": case["intent"], "instruction": case["instruction"],
                                                              "source_policy": policy.freeze(config["source_memo"])})
                try:
                    for stage in TEXT_STAGES:
                        snapshot = view(memory, stage)
                        content = log.call(cid, stage, model_input(snapshot), instructions(stage, snapshot),
                                           response_schema(stage, snapshot), config["generation"]["timeout_s"])
                        accept(memory, stage, content, expected_revision=snapshot["revision"])
                        result["completed_stages"].append(stage)
                    checked = folder / "source"
                    checked.mkdir()
                    with TextConductor(checked / "memory.sqlite", cid + "_source", policy, memory_limits) as controller:
                        controller.begin_from_text_memory(memory)
                        def worker(request):
                            p = request.payload
                            return log.call(cid, p["role"], p["input"], p["instructions"], p["schema"], request.timeout_s, p["round"])
                        try:
                            report = controller.run(worker, clock=clock)
                        finally:
                            export_memory(controller.memory, checked)
                            write_json(checked / "report.json", controller.report())
                        result.update(status="observed", report=report, observation=observe(memory, controller))
                except BaseException as exc:
                    result.update(status="interrupted" if not isinstance(exc, Exception) else "failed", error_type=type(exc).__name__)
                    if not isinstance(exc, Exception):
                        raise
                finally:
                    export_memory(memory, folder)
        if saved_ticks is not None:
            if ticks != saved_ticks or log.rows != log.saved or results != strict_json((replay / "results.json").read_text()):
                raise ContractError("Comparison replay differs")
        aborted = False
        return results
    finally:
        write_json(out / "results.json", results)
        write_json(out / "timing.json", ticks)
        write_json(out / "summary.json", {"status": "aborted" if aborted else "observed", "mode": mode,
                   "live_api_calls": sum(r["api_attempted"] for r in log.rows) if mode == "live" else 0,
                   "recorded_exchanges": len(log.rows), "automatic_retries": 0,
                   "known_usage": {k: sum(r["usage"][k] or 0 for r in log.rows) for k in ("prompt_tokens", "completion_tokens")},
                   "unknown_usage_calls": sum(any(v is None for v in r["usage"].values()) for r in log.rows),
                   "cases": [{"id": r["id"], "status": r["status"], "source_state": r.get("report", {}).get("state")} for r in results]})
        write_json(out / "manifest.json", {"claim": "local_integrity_only", "sha256": {
                   str(p.relative_to(out)): sha(p.read_bytes()) for p in sorted(out.rglob("*")) if p.is_file() and p != out / "manifest.json"}})


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--mode", choices=("simulated", "live", "replay"), default="simulated")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--replay", type=Path)
    parser.add_argument("--stop-file", type=Path)
    parser.add_argument("--credentials-from-zshrc", action="store_true")
    args = parser.parse_args()
    try:
        if args.credentials_from_zshrc:
            if args.mode != "live":
                raise ContractError("Credentials are live-only")
            load_zshrc_literals(Path.home() / ".zshrc")
        results = run(args.out, args.mode, config_path=args.config, replay=args.replay, stop_file=args.stop_file)
        print(canonical([{"id": r["id"], "status": r["status"], "state": r.get("report", {}).get("state")} for r in results]))
        sys.exit(0 if all(r["status"] == "observed" for r in results) else 1)
    except Exception as exc:
        print(canonical({"status": "failed", "error_type": type(exc).__name__}))
        sys.exit(1)
