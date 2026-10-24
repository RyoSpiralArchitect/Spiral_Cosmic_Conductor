# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 Ryo Higa / SpiralReality
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from conductor.text_conductor import TextConductor
from conductor.text_workflow import abstract_teacher, open_text_memory, seed, accept
from conductor.types import ContractError, canonical
from experiments.source_alignment_pilot import FIXTURE, run


class SourceAlignmentPilotTests(unittest.TestCase):
    def test_captured_candidate_fixture_routes_and_replays_without_network(self):
        with tempfile.TemporaryDirectory() as tmp, patch("experiments.provider_pilot.ProviderWorker._transport", side_effect=AssertionError("No network")):
            root = Path(tmp)
            result = run(root / "sim")
            replay = run(root / "replay", "replay", replay=root / "sim")
            self.assertEqual(result, replay)
            self.assertEqual(result["state"], "released_candidate")
            self.assertEqual(result["calls_used"], 3)
            first = json.loads((root / "sim/check_0.json").read_text())
            self.assertEqual(first["blocker_ids"], json.loads(FIXTURE.read_text())["expected_initial_blockers"])
            for file in (root / "sim").glob("request_*.request.json"):
                body = json.loads(file.read_bytes())
                if body["response_format"]["json_schema"]["name"] == "source_auditor":
                    packet = json.loads(body["messages"][1]["content"])
                    self.assertEqual(set(packet), {"source", "candidate", "policy"})
                    self.assertNotIn("before_assessment", json.dumps(packet))
            self.assertEqual(json.loads((root / "sim/summary.json").read_text())["live_api_calls"], 0)

    def test_unresolved_candidate_is_held_and_replays_as_held(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = run(root / "sim", scenario="unresolved")
            replay = run(root / "replay", "replay", replay=root / "sim")
            self.assertEqual(result, replay)
            self.assertEqual(result["state"], "held")
            self.assertIsNone(result["result_text"])
            self.assertEqual(result["calls_used"], 3)
            self.assertEqual((root / "sim/candidate_0.md").read_text(), (root / "sim/candidate_1.md").read_text())

    def test_transport_failure_is_charged_and_replay_preserves_outcome(self):
        def fail(*args):
            raise TimeoutError("UNTRUSTED_ERROR_DETAIL")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = run(root / "failed", transport=fail)
            self.assertEqual(result["state"], "held")
            self.assertEqual(result["calls_used"], 1)
            self.assertNotIn("UNTRUSTED_ERROR_DETAIL", (root / "failed/exchanges.jsonl").read_text())
            replay = run(root / "replay", "replay", replay=root / "failed")
            self.assertEqual(result, replay)

    def test_changed_capture_is_rejected_before_replay(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run(root / "sim")
            (root / "sim/candidate_0.md").write_text("changed")
            with self.assertRaises(ContractError):
                run(root / "replay", "replay", replay=root / "sim")

    def test_truncated_audit_is_preserved_and_not_followed_by_repair(self):
        def truncated(*args):
            return canonical({"choices": [{"finish_reason": "length", "message": {"content": '{"sources":['}}],
                              "usage": {"prompt_tokens": 100, "completion_tokens": 8192}}).encode()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report = run(root / "short", transport=truncated)
            self.assertEqual(report["calls_used"], 1)
            self.assertEqual(report["state"], "held")
            self.assertTrue((root / "short/request_0.response.json").exists())
            self.assertFalse((root / "short/request_1.request.json").exists())
            self.assertEqual(report, run(root / "replay", "replay", replay=root / "short"))

    def test_imports_linked_text_memory_without_copying_critique_to_auditor(self):
        memo = "氏名は収集しない。"
        revision = "氏名を集めずに行う。"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with open_text_memory(root / "previous.sqlite", "previous") as previous:
                seed(previous, memo)
                accept(previous, "proposer", canonical({"text": revision}), expected_revision=1)
                review = {"findings": [{"id": f"F{i:02d}", "principle_id": p["id"], "source_ids": ["M01"], "draft_ids": ["D01"],
                                       "elements": [{"polarity": "positive", "change": "null", "description": "CRITIQUE_CANARY"}],
                                       "recommendation": "CRITIQUE_CANARY"} for i, p in enumerate(abstract_teacher()["principles"], 1)]}
                accept(previous, "critic", canonical(review), expected_revision=2)
                accept(previous, "reviser", canonical({"sentences": [{"id": "R01", "text": revision}],
                       "decisions": [{"finding_id": f"F{i:02d}", "disposition": "adopt", "reason": "CRITIQUE_CANARY", "output_ids": ["R01"]} for i in range(1, 7)]}), expected_revision=3)
                original_audit = previous.audit()
                with TextConductor(root / "checked.sqlite", "checked") as conductor:
                    conductor.begin_from_text_memory(previous)
                    request = conductor.request()
                    self.assertNotIn("CRITIQUE_CANARY", canonical(request.payload))
                    self.assertEqual(request.payload["input"]["source"]["units"][0]["text"], memo)
                    origin = json.loads(conductor.memory.history("controller", "origin")[0]["text"])
                    self.assertEqual(origin["upstream_event_head"], original_audit["event_head_sha256"])
                self.assertEqual(previous.audit(), original_audit)


if __name__ == "__main__":
    unittest.main()
