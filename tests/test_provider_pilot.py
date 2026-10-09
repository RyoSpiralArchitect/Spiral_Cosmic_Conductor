# SPDX-License-Identifier: AGPL-3.0-only
import io
import hashlib
import json
import os
import tempfile
import unittest
import urllib.error
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from conductor.adapters import AdapterError
from conductor.ledger import Ledger
from conductor.runtime import Conductor, Limits
from conductor.scenarios import capacity_task
from conductor.types import ContractError, canonical
from experiments.provider_pilot import CallBudget, PROVIDERS, ProviderWorker, load_zshrc_literals, run_pilot


class ProviderPilotTests(unittest.TestCase):
    def setUp(self):
        self.ledger = Ledger(":memory:", capacity_task())
        self.addCleanup(self.ledger.close)
        self.dispatch = self.ledger.issue("mistral-fast", "proposer", {"workers"})
        self.rows = []
        self.worker = ProviderWorker("mistral", "fast", "simulated", CallBudget(), self.rows.append)

    def test_simulated_full_pilot_and_injected_repair(self):
        with tempfile.TemporaryDirectory() as tmp, redirect_stdout(io.StringIO()):
            result = run_pilot(Path(tmp) / "pilot", "simulated")
            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["generation_calls"], 27)
            self.assertTrue(json.loads((Path(tmp) / "pilot/plan.json").read_text())["explicit_dispatch_contract"])
            natural = result["completed_cases"][:3]
            self.assertTrue(all(s["usage"]["calls"] == 6 for s in natural))
            self.assertTrue(all(s["verification_failures"] == 0 for s in natural))
            recovery = result["completed_cases"][3]
            self.assertTrue(recovery["host_fault_injected"])
            self.assertEqual(recovery["invalidated_claim_events"], 3)
            self.assertEqual(recovery["workers_used"], ["mistral-fast", "openai-careful"])
            rows = [json.loads(x) for x in (Path(tmp) / "pilot/04_injected_repair/exchanges.jsonl").read_text().splitlines()]
            self.assertEqual(json.loads(rows[0]["raw_content"])["ops"][0]["value"], 4)
            self.assertEqual(rows[0]["applied_patch"]["ops"][0]["value"], 3)
            self.assertTrue(all("ticket" not in json.dumps(r["request"]) for r in rows))

    def test_credentials_preflight_leaves_no_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {}, clear=True):
            out = Path(tmp) / "not_created"
            with self.assertRaises(ContractError):
                run_pilot(out, "live")
            self.assertFalse(out.exists())

    def test_invalid_output_and_truncation_recorded_without_retry(self):
        for message, reason in [({"content": "not json"}, "stop"),
                                ({"content": "{}"}, "length"),
                                ({"content": "{}", "tool_calls": [{}]}, "stop")]:
            raw = json.dumps({"choices": [{"finish_reason": reason, "message": message}]}).encode()
            with patch.object(self.worker, "_transport", return_value=raw) as transport:
                with self.assertRaises(AdapterError):
                    self.worker.propose(self.dispatch, 2)
                self.assertEqual(transport.call_count, 1)
                self.assertEqual(self.rows[-1]["status"], "failed")

    def test_http_errors_do_not_log_body_or_credentials(self):
        error = urllib.error.HTTPError("https://example.invalid", 401, "secret-test-value", {}, io.BytesIO(b"secret-test-value"))
        with patch.object(self.worker, "_transport", side_effect=error) as transport:
            with self.assertRaises(AdapterError) as caught:
                self.worker.propose(self.dispatch, 2)
            self.assertNotIn("secret-test-value", str(caught.exception) + json.dumps(self.rows))
            self.assertEqual(transport.call_count, 1)
            self.assertEqual(self.rows[-1]["http_status"], 401)

    def test_budget_exhaustion_precedes_transport(self):
        self.worker.budget = CallBudget(0)
        with patch.object(self.worker, "_transport") as transport:
            with self.assertRaises(AdapterError):
                self.worker.propose(self.dispatch, 1)
            transport.assert_not_called()

    def test_payload_targets_provider_without_grant_or_key(self):
        for provider in PROVIDERS:
            worker = ProviderWorker(provider, "fast", "simulated", CallBudget(), self.rows.append)
            reply = worker.propose(self.dispatch, 2)
            self.assertEqual(reply.patch["ops"][0]["value"], 4)
            data = json.dumps(self.rows[-1])
            self.assertNotIn(self.dispatch.ticket, data)
            self.assertNotIn("simulation-placeholder", data)

    def test_explicit_contract_maps_state_and_evidence_without_supplying_answer(self):
        self.worker.propose(self.dispatch, 2)
        payload = json.loads(self.rows[-1]["request"]["messages"][1]["content"])
        self.assertEqual(payload["target_contract"], {
            "claim_id": "workers", "current_status": "missing", "required_op": "add_claim",
            "required_evidence_ids": ["current"],
        })
        self.assertTrue(self.ledger.apply(self.dispatch.ticket, self.rows[-1]["applied_patch"]).accepted)
        again = self.ledger.issue("mistral-fast", "repairer", {"workers"})
        self.worker.propose(again, 2)
        contract = json.loads(self.rows[-1]["request"]["messages"][1]["content"])["target_contract"]
        self.assertEqual(contract["required_op"], "revise_claim")
        self.assertNotIn("value", contract)

    def test_legacy_contract_is_an_explicit_comparison_only(self):
        with tempfile.TemporaryDirectory() as tmp, redirect_stdout(io.StringIO()):
            result = run_pilot(Path(tmp) / "pilot", "simulated", explicit_contract=False)
            self.assertEqual(result["generation_calls"], 27)
            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["completed_cases"][-1]["invalidated_claim_events"], 3)
            row = json.loads((Path(tmp) / "pilot/01_mistral_natural/exchanges.jsonl").read_text().splitlines()[0])
            self.assertNotIn("target_contract", json.loads(row["request"]["messages"][1]["content"]))

    def test_recorded_live_exchanges_replay_with_identical_requests(self):
        # No live calls: these are exact returned texts from both frozen pilots.
        fixture = json.loads((Path(__file__).parent / "fixtures/provider_pilot_2026-10-10.json").read_text())
        for case in fixture["cases"]:
            with self.subTest(case=case["name"]):
                remaining = list(case["exchanges"])
                rows = []
                budget = CallBudget()

                def replay(worker, payload, body, timeout_s):
                    self.assertTrue(remaining, "Unexpected extra provider call")
                    recorded = remaining.pop(0)
                    self.assertEqual(worker.provider, recorded["provider"])
                    # Covers prompt, view, target contract, and provider-specific options.
                    self.assertEqual(hashlib.sha256(body).hexdigest(), recorded["request_sha256"])
                    return canonical({"choices": [{"finish_reason": "stop", "message": {
                        "content": recorded["raw_content"]}}], "usage": recorded["usage"]}).encode()

                fast = ProviderWorker(case["fast"], "fast", "simulated", budget, rows.append,
                                      explicit_contract=case["explicit_contract"], inject_fault=case["inject_fault"])
                careful = ProviderWorker(case["careful"], "careful", "simulated", budget, rows.append,
                                         explicit_contract=case["explicit_contract"])
                with Ledger(":memory:", capacity_task()) as ledger, patch.object(ProviderWorker, "_transport", replay):
                    result = Conductor(ledger, fast, careful, limits=Limits(**fixture["limits_per_case"])).run()
                    self.assertEqual({key: result[key] for key in case["expected_report"]}, case["expected_report"])
                    self.assertEqual(result["usage"]["calls"], len(case["exchanges"]))
                    self.assertEqual([event["payload"]["reason"] for event in ledger.events()
                                      if event["kind"] == "patch_rejected"], case["expected_rejections"])
                    self.assertEqual([event["payload"]["invalidated"] for event in ledger.events()
                                      if event["kind"] == "verification_failed"], case["expected_invalidations"])
                    self.assertTrue(result["audit"]["state_replay_matches"])
                self.assertEqual(remaining, [])

    def test_zshrc_import_is_literal_and_does_not_execute(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {}, clear=True):
            p = Path(tmp) / "zshrc"
            marker = Path(tmp) / "must_not_exist"
            p.write_text(f"export GEMINI_API_KEY='literal-placeholder'\nexport OPENAI_API_KEY=$(touch {marker})\nexport MISTRAL_API_KEY=`touch {marker}`\n")
            load_zshrc_literals(p)
            self.assertEqual(os.environ["GEMINI_API_KEY"], "literal-placeholder")
            self.assertNotIn("OPENAI_API_KEY", os.environ)
            self.assertNotIn("MISTRAL_API_KEY", os.environ)
            self.assertFalse(marker.exists())


if __name__ == "__main__":
    unittest.main()
