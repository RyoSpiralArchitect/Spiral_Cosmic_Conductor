# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 Ryo Higa / SpiralReality
"""Fork a verified public draft; observe frozen prefixes without feedback."""
from __future__ import annotations

import argparse
from copy import deepcopy
from dataclasses import asdict
import math
import os
from pathlib import Path
import subprocess
import sys
import time
import zipfile

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from conductor.emergence import (CONTINUATION_INSTRUCTIONS, OBSERVER_INSTRUCTIONS, accept_continuation,
                                assess_observation, continuation_schema, observer_schema, packet, units)
from conductor.multipolar import Agent, MultipolarBroker
from conductor.shared_memory import ArtifactRef, MemoryLimits, Publication, SharedMemory
from conductor.text_conductor import TextConductor
from conductor.text_workflow import TextLimits, accept, documents, instructions, model_input, open_text_memory, response_schema, seed, view
from conductor.types import ContractError, canonical, check_id, exact_keys, strict_json
from experiments.provider_pilot import PROVIDERS, load_zshrc_literals
from experiments.source_alignment_pilot import verify_manifest
from experiments.text_memory_pilot import sha, wire_body, write_json
from experiments.transmission_comparison import ExchangeLog, envelope, export_memory, policy_for, simulate, validate_config

DEFAULT_CONFIG = REPO / "examples/emergence-probe.json"
REVIEW_INSTRUCTION = "\nreview_guidanceはこの実験での教師の解釈方針。task_briefと共に読み、指定された伝達目的に沿って批評・改稿する。"


def validate(config, receipt):
    exact_keys(config, {"schema", "seed_receipt", "seed_receipt_sha256", "seed_case", "routing", "generation", "arms"})
    if config["schema"] != "emergence_probe.v1" or receipt.get("schema") != "transmission_comparison_observation.v1":
        raise ContractError("Unknown emergence input")
    base = deepcopy(receipt["config"])
    base.update(generation=config["generation"], routing={k: config["routing"][k] for k in base["routing"]})
    validate_config(base)
    exact_keys(config["routing"], set(base["routing"]) | {"continuator", "observer"})
    if any(p not in PROVIDERS for p in config["routing"].values()):
        raise ContractError("Unknown probe provider")
    if not isinstance(config["arms"], list) or not config["arms"]:
        raise ContractError("Missing probe arms")
    seen = set()
    for arm in config["arms"]:
        exact_keys(arm, {"id", "review_guidance"})
        check_id(arm["id"])
        if arm["id"] in seen:
            raise ContractError("Duplicate probe arm")
        seen.add(arm["id"])
        exact_keys(arm["review_guidance"], {"mode", "instruction"})
        if any(not isinstance(v, str) or not v.strip() for v in arm["review_guidance"].values()):
            raise ContractError("Invalid review guidance")
    if sum(c["id"] == config["seed_case"] for c in receipt["cases"]) != 1:
        raise ContractError("Missing unique seed case")
    return base


def doc_units(name, text):
    if name == "review":
        # Each finding is an exact substring of the canonical review artifact.
        result = []
        for finding in strict_json(text)["findings"]:
            value = canonical(finding)
            start = text.index(value)
            result.append({"id": "review_" + finding["id"], "document_id": "review",
                           "document_sha256": sha(text.encode()), "start": start, "end": start + len(value),
                           "text": value, "quote_sha256": sha(value.encode())})
        return result
    split = {"memo": "sentences", "draft": "draft", "revision": "lines", "continuation": "lines"}.get(name, "whole")
    return units(name, text, split=split)


def visible(snapshot, stage, instruction):
    result = [u for name, artifact in sorted(documents(snapshot).items()) for u in doc_units(name, artifact["text"])]
    return result + units("instruction_" + stage, instruction, split="whole")


def add_history(history, added):
    existing = {u["id"]: u for u in history}
    for u in added:
        if u["id"] in existing and existing[u["id"]] != u:
            raise ContractError("Observation history changed")
        if u["id"] not in existing:
            history.append(u)
            existing[u["id"]] = u


def boundary(queue, arm, stage, history, inputs, target, tracked):
    add_history(history, inputs)
    p = packet(stage, history, [u["id"] for u in inputs], target, tracked)
    queue.append({"arm": arm, "input": deepcopy(p)})
    add_history(history, target)


def simulated_transport(case, stage, p, payload):
    if stage == "continuator":
        value = {"sentences": [{"id": "N01", "text": "模擬の次の場面。"}],
                 "links": [{"input_id": u["id"], "use": "mentioned_only", "output_ids": ["N01"], "reason": "模擬の参照。"} for u in p["incoming"]]}
    elif stage == "observer":
        value = {"outputs": [{"output_id": u["id"], "relation": "uncertain", "novelty": "uncertain",
                               "antecedent_ids": [], "input_ids": [], "idea": "模擬の観測。", "reason": "意味判定は実施していない。"} for u in p["target"]],
                 "fates": [{"prior_id": uid, "use": "uncertain", "output_ids": [], "reason": "模擬の観測。"} for uid in p["tracked_ids"]]}
    else:
        return simulate(stage, p)
    return canonical({"model": "SIMULATED", "choices": [{"finish_reason": "stop", "message": {"content": canonical(value)}}], "usage": {}}).encode()


def continue_text(folder, cid, memo, brief, incoming, origin, log, timeout):
    slots = {"memo": "source", "task_brief": "guidance", "incoming": "proposal", "origin": "trace",
             "continuation": "revision", "continuation_trace": "trace"}
    reads = {"memo", "task_brief", "incoming", "origin"}
    broker = MultipolarBroker([Agent("controller", frozenset(slots), frozenset(reads)),
                               Agent("continuator", frozenset(reads | {"continuation"}), frozenset({"continuation", "continuation_trace"}))])
    folder.mkdir()
    with SharedMemory(folder / "memory.sqlite", "continuation", broker, slots, MemoryLimits.unbounded()) as memory:
        memory.commit("controller", [Publication("memo", memo), Publication("task_brief", canonical(brief)),
                                     Publication("incoming", incoming), Publication("origin", canonical(origin))], expected_revision=0)
        snapshot = memory.snapshot("continuator", reads)
        incoming_units = units("incoming", incoming, split="lines")
        p = {"source": doc_units("memo", memo), "task_brief": brief, "incoming": incoming_units}
        try:
            content = log.call(cid, "continuator", p, CONTINUATION_INSTRUCTIONS, continuation_schema(incoming_units), timeout)
            response = strict_json(content)
            text = accept_continuation(response, incoming_units)
            parents = tuple(ArtifactRef.from_dict(a["ref"]) for a in snapshot["artifacts"])
            output_ref = ArtifactRef(memory.episode, "continuation", 1, sha(text.encode()))
            memory.commit("continuator", [Publication("continuation", text, parents),
                        Publication("continuation_trace", canonical({"schema": "continuation_trace.v1", **response,
                            "incoming_sha256": sha(incoming.encode()), "output_sha256": sha(text.encode()),
                            "output_ref": output_ref.to_dict(), "semantic_status": "unassessed",
                            "claim": "Producer-declared uptake only."}), (*parents, output_ref))], expected_revision=snapshot["revision"])
            (folder / "continuation.md").write_text(text, encoding="utf-8")
            return text, response, p
        finally:
            export_memory(memory, folder)


def run(out, mode="simulated", *, config_path=DEFAULT_CONFIG, replay=None, transport=None, stop_file=None):
    if mode not in {"simulated", "live", "replay"} or (transport is not None and mode != "simulated"):
        raise ContractError("Invalid probe mode")
    if out.exists() and (not out.is_dir() or any(out.iterdir())):
        raise ContractError("Use a new output directory")
    sources = [*sorted((REPO / "conductor").glob("*.py")), *sorted((REPO / "experiments").glob("*.py"))]
    hashes = {str(p.relative_to(REPO)): sha(p.read_bytes()) for p in sources}
    saved_ticks = None
    if mode == "replay":
        if replay is None:
            raise ContractError("Missing replay capture")
        manifest = verify_manifest(replay)
        if not {"config.json", "seed.json", "plan.json", "results.json", "timing.json", "exchanges.jsonl"} <= manifest.keys():
            raise ContractError("Incomplete probe capture")
        if strict_json((replay / "plan.json").read_text())["source_sha256"] != hashes:
            raise ContractError("Replay needs the captured implementation")
        config = strict_json((replay / "config.json").read_text())
        receipt_bytes = (replay / "seed.json").read_bytes()
        saved_ticks = strict_json((replay / "timing.json").read_text())
    else:
        config = strict_json(config_path.read_text())
        receipt_bytes = (config_path.parent / config["seed_receipt"]).read_bytes()
    if sha(receipt_bytes) != config["seed_receipt_sha256"]:
        raise ContractError("Seed receipt hash mismatch")
    receipt = strict_json(receipt_bytes.decode())
    base = validate(config, receipt)
    seed_case = next(c for c in receipt["cases"] if c["id"] == config["seed_case"])
    case = next(c for c in base["cases"] if c["id"] == config["seed_case"])
    original_exchange = next(r for r in receipt["exchanges"] if r["case"] == case["id"] and r["stage"] == "proposer")
    if (sha(seed_case["draft_text"].encode()) != seed_case["review"]["target"]["sha256"]
            or original_exchange["status"] != "returned"):
        raise ContractError("Seed draft does not match its recorded artifact")
    policy = policy_for(base, case)
    memo = base["source_memo"]
    brief = {"intent": case["intent"], "instruction": case["instruction"], "source_policy": policy.freeze(memo)}
    if mode == "live" and any(not os.environ.get(PROVIDERS[p].key_env) for p in set(config["routing"].values())):
        raise ContractError("Missing probe credential")
    secrets = [os.environ[p.key_env].encode() for p in PROVIDERS.values() if os.environ.get(p.key_env)]
    if any(s in receipt_bytes or s in canonical(config).encode() for s in secrets):
        raise ContractError("Credential-like probe input refused")
    out.mkdir(parents=True, exist_ok=True)
    write_json(out / "config.json", config)
    (out / "seed.json").write_bytes(receipt_bytes)
    write_json(out / "plan.json", {"source_sha256": hashes, "mode": mode, "seed_api_calls": 0,
               "repo_commit": subprocess.check_output(["git", "-C", str(REPO), "rev-parse", "HEAD"], text=True).strip(),
               "providers": {p: asdict(PROVIDERS[p]) for p in sorted(set(config["routing"].values()))},
               "claim": "Shared historical draft, fresh sequential arms, separate observer with frozen prefixes. No causal estimate.",
               "automatic_retries": 0, "observer_feedback": False,
               "effective_limits": {name: base[name] for name in ("memory_limits", "text_limits", "source_policy")}})
    with zipfile.ZipFile(out / "source.zip", "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sources:
            archive.write(path, str(path.relative_to(REPO)))
    log = ExchangeLog(out, mode, config, replay, (transport or simulated_transport) if mode == "simulated" else None, stop_file)
    results, ticks, queue, aborted = {"arms": [], "observations": []}, {}, [], True
    common_history = None
    try:
        for arm in config["arms"]:
            cid = arm["id"]
            folder = out / cid
            folder.mkdir()
            ticks[cid] = []
            if saved_ticks is not None:
                values = saved_ticks[cid]
                if (not isinstance(values, list) or any(type(v) not in (int, float) or not math.isfinite(v) for v in values)
                        or values != sorted(values)):
                    raise ContractError("Invalid replay clock")
            def clock():
                value = time.monotonic() if saved_ticks is None else saved_ticks[cid][len(ticks[cid])]
                ticks[cid].append(value)
                return value
            result = {"id": cid, "status": "incomplete", "completed_stages": []}
            results["arms"].append(result)
            with open_text_memory(folder / "memory.sqlite", case["id"], MemoryLimits(**base["memory_limits"]),
                                  text_limits=TextLimits(**base["text_limits"]), task_brief=True, review_guidance=True) as memory:
                try:
                    seed(memory, memo, task_brief=brief, review_guidance=arm["review_guidance"])
                    snapshot = view(memory, "proposer")
                    original_payload = envelope(receipt["config"]["routing"]["proposer"], "proposer", model_input(snapshot),
                         instructions("proposer", snapshot), response_schema("proposer", snapshot), receipt["config"]["generation"])
                    if sha(wire_body(original_payload)) != original_exchange["request_sha256"]:
                        raise ContractError("Imported draft input differs from its recorded request")
                    accept(memory, "proposer", canonical({"text": seed_case["draft_text"]}), expected_revision=snapshot["revision"])
                    result.update(imported_draft_sha256=sha(seed_case["draft_text"].encode()),
                                  imported_request_sha256=original_exchange["request_sha256"])
                    if common_history is None:
                        common_history = []
                        boundary(queue, "shared", "draft", common_history, visible(snapshot, "proposer", instructions("proposer", snapshot)),
                                 doc_units("draft", seed_case["draft_text"]), [])
                    history = deepcopy(common_history)
                    draft_ids = [u["id"] for u in doc_units("draft", seed_case["draft_text"])]
                    for stage, target_name in (("critic", "review"), ("reviser", "revision")):
                        snapshot = view(memory, stage)
                        instruction = instructions(stage, snapshot) + REVIEW_INSTRUCTION
                        content = log.call(cid, stage, model_input(snapshot), instruction, response_schema(stage, snapshot), config["generation"]["timeout_s"])
                        accept(memory, stage, content, expected_revision=snapshot["revision"])
                        target = doc_units(target_name, memory.history("controller", target_name)[-1]["text"])
                        boundary(queue, cid, target_name, history, visible(snapshot, stage, instruction), target, draft_ids)
                        result["completed_stages"].append(stage)
                    checked = folder / "source"
                    checked.mkdir()
                    repair_inputs = {}
                    with TextConductor(checked / "memory.sqlite", "emergence_source", policy, MemoryLimits(**base["memory_limits"])) as controller:
                        controller.begin_from_text_memory(memory)
                        def worker(request):
                            p = request.payload
                            if p["role"] == "repairer":
                                repair_inputs[p["round"]] = deepcopy(p)
                            return log.call(cid, p["role"], p["input"], p["instructions"], p["schema"], request.timeout_s, p["round"])
                        try:
                            report = controller.run(worker, clock=clock)
                            result["source_report"] = report
                            previous_ids = [u["id"] for u in doc_units("revision", memory.history("controller", "revision")[-1]["text"])]
                            for round_index in sorted(repair_inputs):
                                slot = f"candidate_{round_index}"
                                if slot not in controller.memory.audit()["slots_present"]:
                                    continue
                                target = units(slot, controller.memory.history("controller", slot)[-1]["text"], split="lines")
                                context = units(f"repair_input_{round_index}", canonical(repair_inputs[round_index]), split="whole")
                                boundary(queue, cid, slot, history, context, target, previous_ids)
                                previous_ids = [u["id"] for u in target]
                            if report["state"] != "released_candidate":
                                result["status"] = "held"
                                continue
                            incoming = report["result_text"]
                            text, producer_links, p = continue_text(folder / "next", cid, memo, brief, incoming, report["candidate"], log, config["generation"]["timeout_s"])
                            # The receiver sees the source, brief and final incoming text, not critique or observer judgments.
                            input_units = doc_units("memo", memo) + doc_units("task_brief", canonical(brief)) + p["incoming"]
                            input_units += units("instruction_continuator", CONTINUATION_INSTRUCTIONS, split="whole")
                            boundary(queue, cid, "continuation", history, input_units, doc_units("continuation", text), previous_ids)
                            result.update(status="observed", continuation=text, producer_links=producer_links)
                            result["completed_stages"].append("continuator")
                        finally:
                            export_memory(controller.memory, checked)
                            write_json(checked / "report.json", controller.report())
                except BaseException as exc:
                    result.update(status="failed" if isinstance(exc, Exception) else "interrupted", error_type=type(exc).__name__)
                    if not isinstance(exc, Exception):
                        raise
                finally:
                    export_memory(memory, folder)
        # These packets were frozen at each accepted boundary. Generation has finished;
        # annotations cannot affect any generator, shared memory or release decision.
        write_json(out / "boundaries.json", queue)
        for item in queue:
            p = item["input"]
            entry = {"arm": item["arm"], "stage": p["stage"], "status": "incomplete"}
            results["observations"].append(entry)
            try:
                raw = log.call(item["arm"], "observer", p, OBSERVER_INSTRUCTIONS, observer_schema(p), config["generation"]["timeout_s"])
                entry.update(status="observed", observation=assess_observation(strict_json(raw), p))
            except Exception as exc:
                entry.update(status="failed", error_type=type(exc).__name__)
        if replay is not None:
            if (ticks != saved_ticks or log.rows != log.saved or results != strict_json((replay / "results.json").read_text())
                    or queue != strict_json((replay / "boundaries.json").read_text())):
                raise ContractError("Emergence replay differs")
        aborted = False
        return results
    finally:
        write_json(out / "results.json", results)
        write_json(out / "timing.json", ticks)
        write_json(out / "boundaries.json", queue)
        write_json(out / "summary.json", {"mode": mode, "status": "aborted" if aborted else "observed",
                  "live_api_calls": sum(r["api_attempted"] for r in log.rows) if mode == "live" else 0,
                  "recorded_exchanges": len(log.rows), "automatic_retries": 0, "observer_feedback": False,
                  "known_usage": {k: sum(r["usage"][k] or 0 for r in log.rows) for k in ("prompt_tokens", "completion_tokens")},
                  "unknown_usage_calls": sum(any(v is None for v in r["usage"].values()) for r in log.rows),
                  "arms": [{"id": r["id"], "status": r["status"]} for r in results["arms"]],
                  "observation_failures": sum(r["status"] != "observed" for r in results["observations"])})
        write_json(out / "manifest.json", {"claim": "local_integrity_only", "sha256": {
                  str(p.relative_to(out)): sha(p.read_bytes()) for p in sorted(out.rglob("*")) if p.is_file() and p != out / "manifest.json"}})


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
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
        result = run(args.out, args.mode, config_path=args.config, replay=args.replay, stop_file=args.stop_file)
        print(canonical({"arms": [{"id": a["id"], "status": a["status"]} for a in result["arms"]],
                         "observations": [{"arm": x["arm"], "stage": x["stage"], "status": x["status"]} for x in result["observations"]]}))
        sys.exit(0 if all(a["status"] == "observed" for a in result["arms"] + result["observations"]) else 1)
    except Exception as exc:
        print(canonical({"status": "failed", "error_type": type(exc).__name__}))
        sys.exit(1)
