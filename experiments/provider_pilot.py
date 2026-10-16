# SPDX-License-Identifier: AGPL-3.0-only
"""Bounded provider observation harness using the shared worker message contract.

Run with python3 -I -S -B experiments/provider_pilot.py --mode simulated --out ...
Live requests require --mode live and existing credentials. No transport retries.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import sys
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from conductor.adapters import (AdapterError, ScriptedWorker, SYSTEM_PROMPT, _NoRedirect,
                                worker_messages, worker_patch_schema)
from conductor.cosmic import run_cosmic, verify_bundle, write_json
from conductor.runtime import Limits
from conductor.scenarios import capacity_task
from conductor.types import ContractError, Dispatch, PATCH_SCHEMA, Reply, Usage, canonical, parse_patch, strict_json


@dataclass(frozen=True)
class Provider:
    model: str
    endpoint: str
    key_env: str


PROVIDERS = {
    "openai": Provider("gpt-4.1-mini-2025-04-14", "https://api.openai.com/v1/chat/completions", "OPENAI_API_KEY"),
    "mistral": Provider("mistral-small-2603", "https://api.mistral.ai/v1/chat/completions", "MISTRAL_API_KEY"),
    "gemini": Provider("gemini-2.5-flash", "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions", "GEMINI_API_KEY"),
}
CASES = (
    ("01_mistral_natural", "mistral", "openai", False),
    ("02_openai_natural", "openai", "gemini", False),
    ("03_gemini_natural", "gemini", "mistral", False),
    ("04_injected_repair", "mistral", "openai", True),
)


def load_zshrc_literals(path: Path) -> None:
    """Opt-in literal assignment import only: never execute shell startup code."""
    names = {p.key_env for p in PROVIDERS.values()}
    for line in path.read_text(encoding="utf-8").splitlines():
        match = re.fullmatch(r"\s*(?:export\s+)?([A-Z_]+)=(.*)", line)
        if not match or match[1] not in names or os.environ.get(match[1]):
            continue
        try:
            values = shlex.split(match[2], comments=True)
        except ValueError:
            continue
        if len(values) == 1 and values[0] and not any(c in values[0] for c in "$`\r\n;"):
            os.environ[match[1]] = values[0]


class CallBudget:
    def __init__(self, maximum: int = 36):
        self.maximum, self.used = maximum, 0

    def reserve(self) -> None:
        if self.used >= self.maximum:
            raise AdapterError("Experiment HTTP call budget exhausted")
        self.used += 1


def token_count(usage: dict[str, Any], key: str) -> int | None:
    value = usage.get(key)
    return value if type(value) is int and value >= 0 else None


def safe_text(value: str, secrets: list[str]) -> str:
    for secret in secrets:
        if secret:
            value = value.replace(secret, "[REDACTED]")
    return value


class ProviderWorker:
    max_output_tokens = 1024

    def __init__(self, provider: str, role: str, mode: str, budget: CallBudget,
                 record: Callable[[dict[str, Any]], None], *, inject_fault: bool = False,
                 explicit_contract: bool = True):
        if provider not in PROVIDERS or role not in {"fast", "careful"} or mode not in {"live", "simulated"}:
            raise ContractError("Invalid experiment worker configuration")
        self.provider, self.spec, self.mode = provider, PROVIDERS[provider], mode
        self.name = provider + "-" + role
        self.cost_units = 1 if role == "fast" else 3
        self.budget, self.record = budget, record
        self.inject_fault, self.injected = inject_fault, False
        self.explicit_contract = explicit_contract
        self._key = os.environ.get(self.spec.key_env, "") if mode == "live" else "simulation-placeholder"
        if not self._key:
            raise ContractError("Missing credential: " + self.spec.key_env)
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())

    def _transport(self, payload: dict[str, Any], body: bytes, timeout_s: float) -> bytes:
        if self.mode == "simulated":
            view = strict_json(payload["messages"][1]["content"])["view"]
            patch = ScriptedWorker("simulation").propose(Dispatch("unused", view, "simulation", "proposer"), 1).patch
            return canonical({"id": "SIMULATED", "model": self.spec.model,
                              "choices": [{"finish_reason": "stop", "message": {"content": canonical(patch)}}],
                              "usage": {}}).encode()
        request = urllib.request.Request(self.spec.endpoint, data=body, method="POST", headers={
            "Content-Type": "application/json", "Authorization": "Bearer " + self._key})
        with self._opener.open(request, timeout=timeout_s) as response:
            raw = response.read(1_000_001)
        if len(raw) > 1_000_000:
            raise AdapterError("Response byte limit exceeded")
        return raw

    def propose(self, dispatch: Dispatch, timeout_s: float) -> Reply:
        if timeout_s <= 0:
            raise AdapterError("No request time remains")
        if self.explicit_contract:
            messages = worker_messages(dispatch)
            response_format = {"type": "json_schema", "json_schema": {
                "name": "scoped_patch", "strict": True, "schema": worker_patch_schema(dispatch)}}
        else:
            # Explicit historical comparison only; normal runs use the repaired path.
            legacy_view = {key: value for key, value in dispatch.view.items() if key != "repair_feedback"}
            messages = [{"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": canonical({"view": legacy_view, "patch_schema": PATCH_SCHEMA})}]
            response_format = {"type": "json_object"}
        payload: dict[str, Any] = {
            "model": self.spec.model, "stream": False, "temperature": 0,
            "messages": messages,
            "response_format": response_format,
        }
        payload["max_completion_tokens" if self.provider == "openai" else "max_tokens"] = self.max_output_tokens
        if self.provider == "openai":
            payload["store"] = False
        if self.provider == "gemini":
            payload["reasoning_effort"] = "none"  # documented for Gemini 2.5 Flash
        body = canonical(payload).encode()
        if len(body) > 128_000:
            raise AdapterError("Request byte limit exceeded")
        self.budget.reserve()
        started = time.monotonic()
        row: dict[str, Any] = {
            "call_index": self.budget.used, "mode": self.mode, "provider": self.provider,
            "model_requested": self.spec.model, "worker": self.name, "role": dispatch.role,
            "claim_id": dispatch.view["write_ids"][0], "request": payload,
            "explicit_dispatch_contract": self.explicit_contract,
            "request_bytes": len(body), "status": "started",
        }
        try:
            raw = self._transport(payload, body, timeout_s)
            row["response_bytes"] = len(raw)
            result = strict_json(raw.decode())
            choice = result["choices"][0]
            message = choice["message"]
            content = message.get("content")
            row.update(response_id=result.get("id"), model_returned=result.get("model"),
                       finish_reason=choice.get("finish_reason"), raw_content=content)
            usage = result.get("usage") or {}
            if not isinstance(usage, dict):
                usage = {}
            measured = Usage(token_count(usage, "prompt_tokens"), token_count(usage, "completion_tokens"), len(body), len(raw))
            row["usage"] = asdict(measured)
            if choice.get("finish_reason") != "stop" or message.get("tool_calls") or message.get("function_call") or message.get("refusal"):
                raise AdapterError("Incomplete, refused or tool response")
            if not isinstance(content, str):
                raise AdapterError("Expected JSON text")
            patch = strict_json(content)
            parse_patch(patch)
            # Keep the exact model content separately; the injected value is host-authored.
            if self.inject_fault and not self.injected and row["claim_id"] == "workers":
                if len(patch["ops"]) != 1 or patch["ops"][0]["value"] != 4:
                    raise AdapterError("Fault condition requires the model to first propose the correct value")
                patch["ops"][0]["value"] = 3
                patch["ops"][0]["reason"] = "HOST FAULT INJECTION: replace correct model value 4 with 3 for a repair mechanics test."
                row["host_fault_injection"] = {"original_value": 4, "injected_value": 3, "model_error": False}
                self.injected = True
            row.update(status="parsed", applied_patch=patch)
            return Reply(patch, measured)
        except urllib.error.HTTPError as exc:
            row.update(status="http_error", http_status=exc.code)
            raise AdapterError("Provider HTTP error " + str(exc.code)) from None
        except (AdapterError, ContractError, urllib.error.URLError, OSError, ValueError, KeyError, TypeError, IndexError, RecursionError) as exc:
            row.update(status="failed", error_type=type(exc).__name__)
            raise AdapterError("Provider response failed (" + type(exc).__name__ + ")") from None
        finally:
            row["latency_ms"] = round((time.monotonic() - started) * 1000, 3)
            self.record(row)


def run_pilot(out: Path, mode: str, *, explicit_contract: bool = True) -> dict[str, Any]:
    if mode not in {"simulated", "live"}:
        raise ContractError("Invalid experiment mode")
    if out.exists() and (not out.is_dir() or any(out.iterdir())):
        raise ContractError("Experiment output must be a new or empty directory; resume is unsupported")
    if mode == "live":
        missing = [spec.key_env for spec in PROVIDERS.values() if not os.environ.get(spec.key_env)]
        if missing:
            raise ContractError("Missing credentials: " + ", ".join(missing))
    secrets = [os.environ.get(spec.key_env, "") for spec in PROVIDERS.values()]
    out.mkdir(parents=True, exist_ok=True)
    limits = Limits(max_calls=9, max_cost_units=24, max_reserved_output_tokens=9216,
                    deadline_s=180, call_timeout_s=25, max_attempts_per_claim=2)
    source_hashes = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                     for p in [*sorted((ROOT / "conductor").glob("*.py")), Path(__file__).resolve()]}
    plan = {"mode": mode, "explicit_dispatch_contract": explicit_contract,
            "response_format": "json_schema" if explicit_contract else "json_object",
            "providers": {k: asdict(v) for k, v in PROVIDERS.items()},
            "limits_per_case": asdict(limits), "maximum_total_generation_calls": 36,
            "transport_retries": 0, "speculation": True, "source_sha256": source_hashes,
            "cases": [{"name": n, "fast": f, "careful": c, "host_fault_injection": x} for n, f, c, x in CASES],
            "interpretation": "One synthetic closed-world task. No provider ranking or open-ended reasoning claim."}
    write_json(out / "plan.json", plan)
    budget = CallBudget()
    summaries = []
    status, active_case, error_type = "aborted", None, None
    write_json(out / "summary.json", {"mode": mode, "completed_cases": summaries,
                                      "generation_calls": 0, "status": "running"})
    try:
        for case_name, fast_provider, careful_provider, inject in CASES:
            active_case = case_name
            case_dir = out / case_name
            case_dir.mkdir()
            rows: list[dict[str, Any]] = []

            def record(row: dict[str, Any]) -> None:
                sanitized = safe_text(canonical(row), secrets)
                with (case_dir / "exchanges.jsonl").open("a", encoding="utf-8") as log:
                    log.write(sanitized + "\n")
                rows.append(strict_json(sanitized))
                print(f"  {case_name}: {row['worker']} {row['claim_id']} {row['status']}", flush=True)

            fast = ProviderWorker(fast_provider, "fast", mode, budget, record, inject_fault=inject,
                                  explicit_contract=explicit_contract)
            careful = ProviderWorker(careful_provider, "careful", mode, budget, record,
                                     explicit_contract=explicit_contract)
            result = run_cosmic(capacity_task(), fast, careful, case_dir / "runtime", limits=limits)
            report = strict_json(result.report_path.read_text())
            verification = verify_bundle(case_dir / "runtime")
            events = [strict_json(line) for line in (case_dir / "runtime" / "events.jsonl").read_text().splitlines()]
            summary = {"case": case_name, "mode": mode, "complete": result.complete,
                       "stop_reason": result.stop_reason, "host_fault_injected": fast.injected,
                       "workers_used": report["workers_used"], "usage": report["usage"],
                       "results": report["results"], "verification_failures": report["verification_failures"],
                       "invalidated_claim_events": report["invalidated_claim_events"],
                       "worker_failures": sum(e["kind"] == "worker_failed" for e in events),
                       "patch_rejections": sum(e["kind"] == "patch_rejected" for e in events),
                       "failed_http_or_responses": sum(r["status"] != "parsed" for r in rows),
                       "artifact_verification": verification, "audit": report["audit"]}
            summaries.append(summary)
            write_json(out / "summary.json", {"mode": mode, "completed_cases": summaries,
                                              "generation_calls": budget.used, "status": "running"})
            print(f"CASE COMPLETE: {case_name} complete={result.complete} calls={report['usage']['calls']}", flush=True)
        active_case = None
        status = "completed" if all(s["complete"] for s in summaries) else "completed_with_incomplete_cases"
    except BaseException as exc:
        # Preserve partial evidence on export errors or interruption; never log exception text.
        error_type = type(exc).__name__
        raise
    finally:
        summary = {"mode": mode, "completed_cases": summaries, "generation_calls": budget.used,
                   "status": status}
        if active_case is not None:
            summary["aborted_case"] = {"case": active_case, "error_type": error_type}
        write_json(out / "summary.json", summary)
        manifest = {str(p.relative_to(out)): hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in sorted(out.rglob("*")) if p.is_file() and p != out / "pilot_manifest.json"}
        write_json(out / "pilot_manifest.json", {"sha256": manifest, "claim": "local_integrity_only",
                                                "status": status})

    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("simulated", "live"), default="simulated")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--credentials-from-zshrc", action="store_true")
    parser.add_argument("--legacy-dispatch-contract", action="store_true",
                        help="Reproduce the original ambiguous prompt for comparison")
    args = parser.parse_args()
    try:
        if args.credentials_from_zshrc:
            load_zshrc_literals(Path.home() / ".zshrc")
        result = run_pilot(args.out, args.mode, explicit_contract=not args.legacy_dispatch_contract)
        print(canonical({"status": result["status"], "generation_calls": result["generation_calls"]}))
        return 0 if result["status"] == "completed" else 2
    except (ContractError, OSError, ValueError) as exc:
        # Do not echo source text, shell lines, HTTP bodies, or credentials.
        print("Experiment aborted: " + type(exc).__name__, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
