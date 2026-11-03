# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 Ryo Higa / SpiralReality
import tempfile
import unittest
from pathlib import Path

from conductor.breath import BreathController
from conductor.duet import DuetSession
from conductor.reflux import Finding, RefluxController
from conductor.types import ContractError


class RefluxReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.s = DuetSession(Path(self.tmp.name) / "review.sqlite", "review")
        self.s.begin("Preserve disagreement", {"goal": "compare"})
        self.b, self.r = BreathController(self.s), RefluxController(self.s)
        self.signal("start")
        self.b.open_round("peer_duet", "review", "initial critiques", expected_revision=self.s.revision)
        self.outputs = tuple(self.s.submit(self.b.request(), self.response()) for _ in range(2))
        self.signal("candidates", self.outputs)

    def tearDown(self):
        self.s.close()
        self.tmp.cleanup()

    @staticmethod
    def response():
        return {"text": "revised assessment", "uses": [], "disposition": "candidate"}

    def signal(self, event, refs=()):
        return self.b.signal(event, "host decision", refs=refs, expected_revision=self.s.revision)

    def finding(self, kind, target):
        return Finding(kind, target, (self.s.ref("source"),), "needs reconsideration", "lil")

    def correct(self, kind, target):
        plan = self.r.propose(self.finding(kind, target), expected_revision=self.s.revision)
        self.signal("recompose", (plan,))
        request = self.r.request(plan, expected_revision=self.s.revision)
        output = self.r.submit(request, self.response())
        self.signal("recheck", (output,))
        return output

    def test_a_revised_critique_is_still_correctable(self):
        revised = self.correct("review_mismatch", self.outputs[0])
        second = self.r.propose(self.finding("review_mismatch", revised), expected_revision=self.s.revision)
        self.assertEqual(self.r.plan(second)["recipient"], "bro")
        self.signal("recompose", (second,))
        request = self.r.request(second, expected_revision=self.s.revision)
        self.assertEqual(request.payload["purpose"], "revise_review")
        again = self.r.submit(request, self.response())
        self.assertEqual(self.s.resolve(again)["semantic_status"], "unassessed")
        self.assertEqual(self.s.resolve(revised)["author"], "bro")

    def test_nonreview_revision_does_not_gain_review_role(self):
        revised = self.correct("counterexample", self.outputs[0])
        before = self.s.revision
        with self.assertRaises(ContractError):
            self.r.propose(self.finding("review_mismatch", revised), expected_revision=before)
        self.assertEqual(before, self.s.revision)

    def test_consumed_plan_cannot_be_redispatched_after_recheck(self):
        plan = self.r.propose(self.finding("review_mismatch", self.outputs[0]), expected_revision=self.s.revision)
        self.signal("recompose", (plan,))
        output = self.r.submit(self.r.request(plan, expected_revision=self.s.revision), self.response())
        self.signal("recheck", (output,))
        self.signal("recompose", (output,))
        with self.assertRaises(ContractError):
            self.r.request(plan, expected_revision=self.s.revision)
