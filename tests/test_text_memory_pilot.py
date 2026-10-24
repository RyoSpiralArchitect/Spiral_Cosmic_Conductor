# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 Ryo Higa / SpiralReality
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from conductor.types import ContractError, canonical
from experiments.text_memory_pilot import DEFAULT_CASES, envelope, run, simulated


class TextMemoryPilotTests(unittest.TestCase):
    def test_simulation_replay_reopens_state_with_exact_requests_and_heads(self):
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()), \
                patch("experiments.provider_pilot.ProviderWorker._transport", side_effect=AssertionError("No network")):
            root = Path(tmp)
            original = run(root / "sim")
            replay = run(root / "replay", "replay", replay=root / "sim")
            self.assertEqual(original["committed_calls"], 6)
            self.assertEqual(replay["live_api_calls"], 0)
            self.assertEqual(len(replay["matched_replay_calls"]), 6)
            for before, after in zip(original["cases"], replay["cases"]):
                self.assertEqual(before["audit"], after["audit"])
                self.assertEqual(before["audit"]["event_count"], 4)
            teacher = (root / "sim/teacher.json").read_bytes()
            for case in original["cases"]:
                self.assertEqual((root / "sim" / case["id"] / "principles.json").read_bytes(), teacher)
            with self.assertRaises(ContractError):
                run(root / "sim")

    def test_failed_stage_is_charged_not_retried_and_other_episode_can_complete(self):
        seen = []
        def transport(case, stage, snapshot, payload, body, timeout):
            seen.append((case["id"], stage))
            if case["id"] == "creative_session" and stage == "critic":
                raise TimeoutError("UNTRUSTED_ERROR_TEXT")
            return simulated(stage, snapshot)
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()):
            root = Path(tmp)
            result = run(root / "sim", transport=transport)
            self.assertEqual(result["calls"], 5)
            self.assertEqual(result["failed_calls"], 1)
            self.assertEqual(result["cases"][0]["status"], "failed")
            self.assertEqual(result["cases"][1]["status"], "completed")
            self.assertNotIn(("creative_session", "reviser"), seen)
            self.assertNotIn("UNTRUSTED_ERROR_TEXT", (root / "sim/exchanges.jsonl").read_text())
            replay = run(root / "replay", "replay", replay=root / "sim")
            self.assertEqual(replay["failed_calls"], 1)
            self.assertEqual(len(replay["matched_replay_calls"]), 5)

    def test_truncation_is_preserved_with_usage_and_no_dependent_calls(self):
        def transport(case, stage, snapshot, payload, body, timeout):
            result = json.loads(simulated(stage, snapshot))
            if stage == "proposer":
                result["choices"][0]["finish_reason"] = "length"
                result["usage"] = {"prompt_tokens": 12, "completion_tokens": 3072}
            return canonical(result).encode()
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()):
            root = Path(tmp)
            result = run(root / "sim", transport=transport)
            self.assertEqual(result["calls"], 2)
            self.assertEqual(result["committed_calls"], 0)
            self.assertEqual(result["known_usage"]["completion_tokens"], 6144)
            replay = run(root / "replay", "replay", replay=root / "sim")
            self.assertEqual(len(replay["matched_replay_calls"]), 2)

    def test_interrupt_leaves_reservation_partial_response_and_public_state(self):
        def transport(*args):
            raise KeyboardInterrupt()
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()):
            root = Path(tmp) / "interrupted"
            with self.assertRaises(KeyboardInterrupt):
                run(root, transport=transport)
            summary = json.loads((root / "summary.json").read_text())
            self.assertEqual(summary["status"], "aborted")
            self.assertEqual(summary["calls"], 1)
            self.assertEqual(summary["cases"][0]["audit"]["event_count"], 1)
            self.assertEqual(len((root / "reservations.jsonl").read_text().splitlines()), 1)

    def test_mutated_capture_is_rejected_before_replay(self):
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()):
            root = Path(tmp)
            run(root / "sim")
            (root / "sim/creative_session/revision.md").write_text("changed")
            with self.assertRaises(ContractError):
                run(root / "replay", "replay", replay=root / "sim")

    def test_explicit_fork_reuses_only_completed_draft_and_records_two_new_calls(self):
        def transport(case, stage, snapshot, payload, body, timeout):
            if stage == "critic":
                raise TimeoutError()
            return simulated(stage, snapshot)
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()):
            root = Path(tmp)
            run(root / "original", transport=transport)
            source = root / "original/tool_shelf"
            original_bytes = (source / "memory.sqlite").read_bytes()
            cases = json.loads(DEFAULT_CASES.read_text())
            subset = root / "subset.json"
            subset.write_text(json.dumps([c for c in cases if c["id"] == "tool_shelf"]))
            fork = run(root / "fork", cases_path=subset, resume_from=source)
            self.assertEqual(fork["calls"], 2)
            self.assertEqual(fork["cases"][0]["completed_stages"], ["critic", "reviser"])
            self.assertEqual(fork["cases"][0]["audit"]["event_count"], 4)
            self.assertEqual((source / "memory.sqlite").read_bytes(), original_bytes)
            replay = run(root / "fork-replay", "replay", replay=root / "fork")
            self.assertEqual(len(replay["matched_replay_calls"]), 2)
            self.assertEqual(fork["cases"][0]["audit"], replay["cases"][0]["audit"])

    def test_fork_cannot_import_a_different_memo(self):
        def transport(case, stage, snapshot, payload, body, timeout):
            if stage == "critic":
                raise TimeoutError()
            return simulated(stage, snapshot)
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()):
            root = Path(tmp)
            run(root / "original", transport=transport)
            subset = root / "subset.json"
            subset.write_text(json.dumps([{"id": "tool_shelf", "memo": "different memo"}]))
            with self.assertRaises(ContractError):
                run(root / "fork", cases_path=subset, resume_from=root / "original/tool_shelf")


if __name__ == "__main__":
    unittest.main()
