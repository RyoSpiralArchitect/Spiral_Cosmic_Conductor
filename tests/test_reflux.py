# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 Ryo Higa / SpiralReality
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from conductor.breath import BreathController
from conductor.duet import DuetSession
from conductor.reflux import Finding, RefluxController
from conductor.types import ContractError, canonical


class RefluxTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "reflux.sqlite"
        self.s = DuetSession(self.path, "episode")
        self.s.begin("Disagreement may remain", {"goal": "explore"})
        self.b = BreathController(self.s)
        self.r = RefluxController(self.s)
        self.b.signal("start", "start", expected_revision=self.s.revision)
        self.b.open_round("peer_duet", "review", "review alternatives", expected_revision=self.s.revision)
        self.outputs = tuple(self.s.submit(self.b.request(), self.response(text))
                             for text in ("bro's review", "lil's review"))
        self.b.signal("candidates", "compare", refs=self.outputs, expected_revision=self.s.revision)

    def tearDown(self):
        self.s.close()
        self.tmp.cleanup()

    @staticmethod
    def response(text="new candidate"):
        return {"text": text, "uses": [], "disposition": "candidate"}

    def finding(self, kind="counterexample", index=0):
        return Finding(kind, self.outputs[index], (self.s.ref("source"),), "declared issue", "lil")

    def plan(self, kind="counterexample", index=0):
        return self.r.propose(self.finding(kind, index), expected_revision=self.s.revision)

    def recompose(self, plan):
        self.b.signal("recompose", "explicit intervention", refs=(plan,), expected_revision=self.s.revision)

    def test_returns_to_author_not_always_careful(self):
        p = self.plan(index=1)
        self.assertEqual(self.r.plan(p)["recipient"], "lil")
        self.recompose(p)
        self.assertEqual(self.r.request(p, expected_revision=self.s.revision).worker, "lil")

    def test_critic_is_also_correctable(self):
        p = self.plan("review_mismatch")
        self.recompose(p)
        req = self.r.request(p, expected_revision=self.s.revision)
        self.assertEqual(req.worker, "bro")
        self.assertEqual(req.payload["purpose"], "revise_review")

    def test_original_and_unrelated_branch_unchanged(self):
        before = [self.s.resolve(o) for o in self.outputs]
        p = self.plan("source_conflict")
        self.recompose(p)
        candidate = self.r.submit(self.r.request(p, expected_revision=self.s.revision), self.response())
        self.assertEqual(before, [self.s.resolve(o) for o in self.outputs])
        report = self.r.report(p)
        self.assertEqual(report["semantic_status"], "unassessed")
        self.assertTrue(report["requires_recheck"])
        self.b.signal("recheck", "new candidate needs checking", refs=(candidate,), expected_revision=self.s.revision)
        self.assertEqual(self.b.phase, "compare")

    def test_observer_results_rejected_without_side_effect(self):
        before = self.s.revision
        with self.assertRaises(ContractError):
            self.r.propose(replace(self.finding(), origin_lane="posthoc_observer"), expected_revision=before)
        self.assertEqual(before, self.s.revision)

    def test_source_not_a_rewrite_target(self):
        with self.assertRaises(ContractError):
            self.r.propose(replace(self.finding(), target=self.s.ref("source")), expected_revision=self.s.revision)

    def test_empty_evidence_or_unknown_reporter_rejected(self):
        for bad in (replace(self.finding(), evidence=()), replace(self.finding(), reported_by="unregistered")):
            with self.assertRaises(ContractError):
                self.r.propose(bad, expected_revision=self.s.revision)

    def test_productive_difference_is_fork_not_model_call(self):
        p = self.plan("productive_difference")
        self.assertEqual(self.r.report(p)["action"], "fork")
        before = self.s.revision
        with self.assertRaises(ContractError):
            self.r.request(p, expected_revision=before)
        self.assertEqual(before, self.s.revision)

    def test_explicit_phase_change_required(self):
        p = self.plan()
        with self.assertRaises(ContractError):
            self.r.request(p, expected_revision=self.s.revision)

    def test_repeated_plan_not_retried(self):
        p = self.plan()
        self.recompose(p)
        self.r.submit(self.r.request(p, expected_revision=self.s.revision), self.response())
        with self.assertRaises(ContractError):
            self.r.request(p, expected_revision=self.s.revision)

    def test_annotation_only_separate_from_body(self):
        p = self.plan("annotation_error")
        self.recompose(p)
        req = self.r.request(p, expected_revision=self.s.revision)
        before = self.s.revision
        with self.assertRaises(ContractError):
            self.r.submit(req, self.response("rewrite entire document"))
        self.assertEqual(before, self.s.revision)
        note = {"target": self.outputs[0].to_dict(), "notes": ["Reference points to heading, not evidence"]}
        out = self.r.submit(req, self.response(canonical(note)))
        self.assertEqual(self.s.resolve(self.outputs[0])["text"], "bro's review")
        self.assertIn("Reference points", self.s.resolve(out)["text"])

    def test_annotation_cannot_change_target(self):
        p = self.plan("annotation_error")
        self.recompose(p)
        req = self.r.request(p, expected_revision=self.s.revision)
        with self.assertRaises(ContractError):
            self.r.submit(req, self.response(canonical({"target": self.outputs[1].to_dict(), "notes": ["x"]})))

    def test_resume_keeps_pending_without_redispatch(self):
        p = self.plan()
        self.recompose(p)
        req = self.r.request(p, expected_revision=self.s.revision)
        self.s.close()
        self.s = DuetSession(self.path, "episode")
        self.r = RefluxController(self.s)
        self.assertEqual(self.r.report(p)["state"], "pending")
        with self.assertRaises(ContractError):
            self.r.request(p, expected_revision=self.s.revision)
        self.r.submit(req, self.response())

    def test_stale_and_forged_plan_rejected(self):
        p = self.plan()
        self.recompose(p)
        with self.assertRaises(ContractError):
            self.r.request(p, expected_revision=0)
        with self.assertRaises((ContractError, KeyError)):
            self.r.plan(self.s.ref("policy"))

    def test_refusal_not_repair_success(self):
        p = self.plan()
        self.recompose(p)
        self.r.submit(self.r.request(p, expected_revision=self.s.revision),
                      {"text": "Cannot resolve evidence", "uses": [], "disposition": "refused"})
        self.assertEqual(self.r.report(p)["state"], "refused")
        self.assertFalse(self.r.report(p)["requires_recheck"])

    def test_no_prompt_can_promote_to_verified(self):
        p = self.plan()
        self.recompose(p)
        req = self.r.request(p, expected_revision=self.s.revision)
        with self.assertRaises(ContractError):
            self.r.submit(req, self.response() | {"verified": True})


if __name__ == "__main__":
    unittest.main()
