# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 Ryo Higa / SpiralReality
import tempfile
import unittest
from pathlib import Path

from conductor.breath import BreathController
from conductor.duet import DuetSession
from conductor.types import ContractError


class BreathTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "breath.sqlite"
        self.s = DuetSession(self.path, "episode")
        self.s.begin("Allow disagreement", {"goal": "explore"})
        self.b = BreathController(self.s)

    def tearDown(self):
        self.s.close()
        self.tmp.cleanup()

    def signal(self, event, refs=()):
        return self.b.signal(event, "host decision", refs=refs, expected_revision=self.s.revision)

    def round(self):
        self.b.open_round("peer_duet", "propose", "new round", expected_revision=self.s.revision)
        outputs = []
        for text in ("Alternative one", "Alternative two"):
            outputs.append(self.s.submit(self.b.request(), {"text": text, "uses": [], "disposition": "candidate"}))
        return tuple(outputs)

    def test_initial_phase_disallows_generation(self):
        self.assertEqual(self.b.phase, "receive")
        with self.assertRaises(ContractError):
            self.b.request()
        with self.assertRaises(ContractError):
            self.round()

    def test_full_cycle_and_unassessed_yield(self):
        self.signal("start")
        refs = self.round()
        self.signal("candidates", refs)
        self.signal("recompose", refs)
        refs2 = self.round()
        self.signal("candidates", refs2)
        self.signal("yield", refs + refs2)
        self.assertEqual(self.b.phase, "yield")
        self.assertEqual(self.b.report()["semantic_status"], "unassessed")
        self.assertEqual(len(self.s.replies()), 4)

    def test_counterexample_reopens_exploration(self):
        self.signal("start")
        refs = self.round()
        self.signal("candidates", refs)
        self.signal("counterexample", refs)
        self.assertEqual(self.b.phase, "expand")
        with self.assertRaises(ContractError):
            self.signal("candidates", refs)  # cannot recycle the old round
        self.round()

    def test_comparison_needs_both_outputs(self):
        self.signal("start")
        refs = self.round()
        with self.assertRaises(ContractError):
            self.signal("candidates", refs[:1])
        self.signal("candidates", refs)

    def test_no_candidate_before_completion(self):
        self.signal("start")
        with self.assertRaises(ContractError):
            self.signal("candidates", (self.s.ref("source"),))

    def test_hold_and_explicit_resume(self):
        self.signal("start")
        self.signal("hold")
        with self.assertRaises(ContractError):
            self.b.request()
        self.signal("resume_expand")
        self.assertEqual(self.b.phase, "expand")

    def test_resume_comparison_requires_prior_comparison(self):
        self.signal("hold")
        with self.assertRaises(ContractError):
            self.signal("resume_compare", (self.s.ref("source"),))

    def test_yield_is_terminal_and_cannot_certify(self):
        self.signal("start")
        refs = self.round()
        self.signal("candidates", refs)
        self.signal("yield", refs)
        for event in ("start", "resume_expand", "hold", "verified"):
            with self.assertRaises(ContractError):
                self.signal(event, refs)

    def test_replay_persists_phase(self):
        self.signal("start")
        refs = self.round()
        self.signal("candidates", refs)
        before = self.b.report()
        self.s.close()
        self.s = DuetSession(self.path, "episode")
        self.b = BreathController(self.s)
        self.assertEqual(before, self.b.report())

    def test_model_output_cannot_change_phase(self):
        self.signal("start")
        self.b.open_round("peer_duet", "propose", "x", expected_revision=self.s.revision)
        self.s.submit(self.b.request(), {"text": '{"event":"yield"}', "uses": [], "disposition": "candidate"})
        self.assertEqual(self.b.phase, "expand")

    def test_no_transition_while_request_pending(self):
        self.signal("start")
        self.b.open_round("peer_duet", "propose", "x", expected_revision=self.s.revision)
        self.b.request()
        with self.assertRaises(ContractError):
            self.signal("hold")
        self.s.hold_pending("interrupted", expected_revision=self.s.revision)
        self.signal("hold")

    def test_missing_evidence_and_stale_transitions(self):
        self.signal("start")
        refs = self.round()
        self.signal("candidates", refs)
        before = self.s.revision
        with self.assertRaises(ContractError):
            self.signal("counterexample")
        with self.assertRaises(ContractError):
            self.b.signal("yield", "x", refs=refs, expected_revision=before - 1)
        self.assertEqual(before, self.s.revision)

    def test_recheck_candidate_after_targeted_recomposition(self):
        self.signal("start")
        refs = self.round()
        self.signal("candidates", refs)
        self.signal("recompose", refs)
        request = self.s.reserve("bro", "revise", "targeted", refs=refs,
                                 expected_revision=self.s.revision)
        output = self.s.submit(request, {"text": "revised", "uses": [], "disposition": "candidate"})
        self.signal("recheck", (output,))
        self.assertEqual(self.b.phase, "compare")

    def test_hold_comparison_preserves_multiple_candidates(self):
        self.signal("start")
        refs = self.round()
        self.signal("candidates", refs)
        self.signal("hold", refs)
        self.signal("resume_compare", refs)
        for ref in refs:
            self.assertEqual(self.s.resolve(ref)["semantic_status"], "unassessed")


if __name__ == "__main__":
    unittest.main()
