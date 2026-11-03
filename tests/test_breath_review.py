# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 Ryo Higa / SpiralReality
"""Regression checks added during pre-merge adversarial review."""
import tempfile
import unittest
from pathlib import Path

from conductor.breath import BreathController
from conductor.duet import DuetSession
from conductor.types import ContractError


class BreathReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.s = DuetSession(Path(self.tmp.name) / "review.sqlite", "review")
        self.s.begin("Keep alternatives", {"goal": "explore"})
        self.b = BreathController(self.s)
        self.signal("start")
        self.b.open_round("peer_duet", "propose", "initial", expected_revision=self.s.revision)
        self.old = tuple(self.answer(self.b.request()) for _ in range(2))
        self.signal("candidates", self.old)

    def tearDown(self):
        self.s.close()
        self.tmp.cleanup()

    def signal(self, event, refs=()):
        return self.b.signal(event, "host assessment", refs=refs, expected_revision=self.s.revision)

    def answer(self, request):
        return self.s.submit(request, {"text": "candidate", "uses": [], "disposition": "candidate"})

    def partial_round(self):
        self.signal("recompose", self.old)
        self.b.open_round("peer_duet", "propose", "unfinished", expected_revision=self.s.revision)
        return self.answer(self.b.request())

    def test_recheck_cannot_abandon_unfinished_round(self):
        output = self.partial_round()
        before = self.s.revision
        with self.assertRaises(ContractError):
            self.signal("recheck", (output,))
        self.assertEqual(before, self.s.revision)

    def test_hold_resume_compare_cannot_skip_second_participant(self):
        output = self.partial_round()
        self.signal("hold")
        with self.assertRaises(ContractError):
            self.signal("resume_compare", (output,))
        self.signal("resume_expand")
        self.answer(self.b.request())
        self.assertEqual(self.s.round_status()["state"], "complete")

    def test_recheck_requires_a_new_targeted_output(self):
        self.signal("recompose", self.old)
        with self.assertRaises(ContractError):
            self.signal("recheck", self.old)
        request = self.s.reserve("bro", "revise", "targeted correction", refs=self.old,
                                 expected_revision=self.s.revision)
        new = self.answer(request)
        self.signal("recheck", (new,))
        self.assertEqual(self.b.phase, "compare")

    def test_previous_recomposition_output_cannot_be_reused(self):
        self.signal("recompose", self.old)
        request = self.s.reserve("bro", "revise", "first correction", expected_revision=self.s.revision)
        output = self.answer(request)
        self.signal("recheck", (output,))
        self.signal("recompose", (output,))
        with self.assertRaises(ContractError):
            self.signal("recheck", (output,))
