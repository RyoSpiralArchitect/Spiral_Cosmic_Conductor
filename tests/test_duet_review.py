# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 Ryo Higa / SpiralReality
"""Offline adversarial review: untrusted replies, not a hostile Python host."""
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from conductor.duet import DuetSession
from conductor.shared_memory import ArtifactRef
from conductor.types import ContractError


class DuetReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "review.sqlite"
        self.s = DuetSession(self.path, "review")
        self.s.begin("Keep two alternatives", {"goal": "preserve disagreement"})
        self.s.start_round("peer_duet", "propose", "review", expected_revision=self.s.revision)

    def tearDown(self):
        self.s.close()
        self.tmp.cleanup()

    @staticmethod
    def reply(text="candidate", uses=()):
        return {"text": text, "uses": [r.to_dict() for r in uses], "disposition": "candidate"}

    def test_cross_episode_ticket_is_not_reusable(self):
        request = self.s.request()
        with DuetSession(Path(self.tmp.name) / "other.sqlite", "other") as other:
            other.begin("Keep two alternatives", {"goal": "preserve disagreement"})
            other.start_round("peer_duet", "propose", "review", expected_revision=other.revision)
            foreign = other.request()
            with self.assertRaises(ContractError):
                self.s.submit(foreign, self.reply())
        self.s.submit(request, self.reply())

    def test_peer_cannot_cite_the_first_reply_receipt(self):
        self.s.submit(self.s.request(), self.reply("first"))
        second = self.s.request()
        with self.assertRaises(ContractError):
            self.s.submit(second, self.reply(uses=(self.s.ref("reply_0"),)))
        self.s.submit(second, self.reply())

    def test_failed_reply_preserves_pending_and_event_head(self):
        request = self.s.request()
        before = self.s.memory.audit()
        for response in ([], {"text": "x", "uses": [], "disposition": "verified"},
                         self.reply() | {"control": "yield"}):
            with self.assertRaises(ContractError):
                self.s.submit(request, response)
            self.assertEqual(before, self.s.memory.audit())
            self.assertEqual(self.s.pending()["ref"]["id"], request.slot)
        self.s.submit(request, self.reply())

    def test_forged_digest_in_visible_ref_is_rejected(self):
        request = self.s.request()
        good = self.s.ref("source")
        bad = ArtifactRef(good.episode, good.id, good.version, "0" * 64)
        with self.assertRaises(ContractError):
            self.s.submit(request, self.reply(uses=(bad,)))
        self.s.submit(request, self.reply(uses=(good,)))

    def test_held_ticket_cannot_be_resubmitted_after_reopen(self):
        request = self.s.request()
        self.s.hold_pending("operator stopped", expected_revision=self.s.revision)
        before = self.s.memory.audit()
        self.s.close()
        self.s = DuetSession(self.path, "review")
        with self.assertRaises(ContractError):
            self.s.submit(request, self.reply())
        self.assertEqual(before, self.s.memory.audit())

    def test_concurrent_second_handle_consumes_request_only_once(self):
        request = self.s.request()
        with DuetSession(self.path, "review") as other:
            other.submit(request, self.reply())
            with self.assertRaises(ContractError):
                self.s.submit(request, self.reply("duplicate"))
        self.assertEqual(len(self.s.replies()), 1)

    def test_payload_change_with_original_slot_is_rejected(self):
        request = self.s.request()
        with self.assertRaises(ContractError):
            self.s.submit(replace(request, payload_json=request.payload_json + " "), self.reply())
        self.s.submit(request, self.reply())

    def test_refusal_does_not_start_second_peer_on_reopen(self):
        request = self.s.request()
        self.s.submit(request, {"text": "outside scope", "uses": [], "disposition": "refused"})
        self.s.close()
        self.s = DuetSession(self.path, "review")
        self.assertIsNone(self.s.request())
        self.assertEqual(self.s.round_status()["state"], "held")
        self.assertEqual(len(self.s.replies()), 1)
