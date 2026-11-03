# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 Ryo Higa / SpiralReality
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from conductor.duet import DuetSession
from conductor.shared_memory import ArtifactRef, Publication
from conductor.types import ContractError


class DuetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "duet.sqlite"
        self.s = DuetSession(self.path, "episode")
        self.s.begin("Keep disagreement; no consensus is required.", {"mode": "explore", "goal": "cooperate"})

    def tearDown(self):
        self.s.close()
        self.tmp.cleanup()

    def start(self, mode="peer_duet", purpose="propose", refs=()):
        return self.s.start_round(mode, purpose, "explicit host round", refs=refs, expected_revision=self.s.revision)

    def answer(self, request, text="A candidate", uses=(), disposition="candidate"):
        return self.s.submit(request, {"text": text, "uses": [r.to_dict() for r in uses], "disposition": disposition})

    def test_peer_second_cannot_read_first(self):
        self.start()
        a = self.answer(self.s.request(), "FIRST_PRIVATE_TO_PEER")
        second = self.s.request()
        self.assertNotIn("FIRST_PRIVATE_TO_PEER", second.payload_json)
        self.assertNotIn(a.to_dict(), [d["ref"] for d in second.payload["documents"]])
        with self.assertRaises(ContractError):
            self.answer(second, uses=(a,))
        self.answer(second)
        self.assertEqual(self.s.round_status()["state"], "complete")

    def test_lead_modes_order_and_handoff(self):
        for mode, first in (("lead_bro", "bro"), ("lead_lil", "lil")):
            self.start(mode)
            request = self.s.request()
            self.assertEqual(request.worker, first)
            output = self.answer(request, "Visible leading proposal")
            second = self.s.request()
            self.assertIn(output.to_dict(), [d["ref"] for d in second.payload["documents"]])
            self.answer(second, uses=(output,))

    def test_braid_alternates_after_previous_lead(self):
        self.start("lead_lil")
        self.answer(self.s.request())
        self.answer(self.s.request())  # bro was last
        self.start("braid")
        self.assertEqual(self.s.request().worker, "lil")

    def test_no_automatic_round_or_call(self):
        self.assertIsNone(self.s.request())
        with patch("socket.socket", side_effect=AssertionError("network forbidden")):
            self.start()
            self.answer(self.s.request())
            self.answer(self.s.request())
            self.assertIsNone(self.s.request())

    def test_one_use_request(self):
        self.start()
        request = self.s.request()
        self.answer(request)
        with self.assertRaises(ContractError):
            self.answer(request)

    def test_pending_blocks_redispatch_and_control(self):
        self.start()
        self.s.request()
        with self.assertRaises(ContractError):
            self.s.request()
        with self.assertRaises(ContractError):
            self.s.record("host.note", {}, expected_revision=self.s.revision)

    def test_payload_and_worker_binding(self):
        self.start()
        request = self.s.request()
        for bad in (replace(request, worker="lil"), replace(request, payload_json="{}"),
                    replace(request, expected_revision=True)):
            with self.assertRaises(ContractError):
                self.answer(bad)
        value = request.payload
        value["worker"] = "lil"
        self.answer(request)

    def test_refusal_retained_and_stops_round(self):
        self.start()
        self.answer(self.s.request(), "Not within my scope", disposition="refused")
        self.assertEqual(self.s.round_status()["state"], "held")
        self.assertIsNone(self.s.request())
        self.assertEqual(self.s.replies()[0]["refusal"], "Not within my scope")
        self.start("lead_lil")  # explicit new round, not implicit retry

    def test_reopen_pending_requires_explicit_hold(self):
        self.start()
        request = self.s.request()
        self.s.close()
        self.s = DuetSession(self.path, "episode")
        with self.assertRaises(ContractError):
            self.s.request()
        self.s.hold_pending("operator stopped", expected_revision=self.s.revision)
        with self.assertRaises(ContractError):
            self.answer(request)
        self.assertEqual(self.s.round_status()["state"], "held")

    def test_invisible_unknown_cross_episode_refs_rejected(self):
        for ref in (ArtifactRef("other", "source", 1, "0" * 64),
                    ArtifactRef("episode", "source", 1, "0" * 64)):
            with self.assertRaises(ContractError):
                self.start(refs=(ref,))

    def test_no_unknown_fields_or_semantic_promotion(self):
        self.start()
        request = self.s.request()
        with self.assertRaises(ContractError):
            self.s.submit(request, {"text": "x", "uses": [], "disposition": "candidate", "verified": True})
        with self.assertRaises(ContractError):
            self.answer(request, disposition="verified")
        output = self.answer(request)
        self.assertEqual(self.s.resolve(output)["semantic_status"], "unassessed")

    def test_default_unbounded_and_long_output(self):
        self.assertIsNone(self.s.memory.limits.max_artifact_chars)
        self.start()
        output = self.answer(self.s.request(), "x" * 17000)
        self.assertEqual(len(self.s.resolve(output)["text"]), 17000)

    def test_no_private_context_in_projection(self):
        self.s.memory.broker.remember_private("bro", {"secret": "NEVER_PROJECT_ME"})
        self.start()
        self.assertNotIn("NEVER_PROJECT_ME", self.s.request().payload_json)

    def test_wrong_participants_or_reopened_contract(self):
        with self.assertRaises(ContractError):
            DuetSession(self.path, "episode", ("bro", "other"))
        for actors in (("bro", "bro"), ("controller", "bro"), ("bro",)):
            with self.assertRaises(ContractError):
                DuetSession(self.path, "other", actors)

    def test_stale_revision_has_no_side_effect(self):
        before = self.s.revision
        with self.assertRaises(ContractError):
            self.s.start_round("peer_duet", "propose", "x", expected_revision=0)
        self.assertEqual(before, self.s.revision)

    def test_immutable_source_and_changed_basis(self):
        self.start()
        request = self.s.request()
        self.s.memory.commit("controller", [Publication("source", "changed")], expected_revision=self.s.revision)
        with self.assertRaises(ContractError):
            self.answer(request)

    def test_worker_cannot_write_control_or_source(self):
        for slot in ("source", "control_0"):
            with self.assertRaises(ContractError):
                self.s.memory.commit("bro", [Publication(slot, "forged")], expected_revision=self.s.revision)

    def test_explicit_context_round_reuse_and_replay(self):
        self.start()
        a = self.answer(self.s.request())
        self.answer(self.s.request())
        self.start(refs=(a,))
        request = self.s.request()
        self.assertIn(a.to_dict(), [d["ref"] for d in request.payload["documents"]])
        self.answer(request)
        self.answer(self.s.request())
        before = self.s.memory.audit()
        self.s.close()
        self.s = DuetSession(self.path, "episode")
        self.assertEqual(before, self.s.memory.audit())
        self.assertEqual(self.s.round_status()["state"], "complete")

    def test_duplicate_uses_and_empty_text_rejected_atomically(self):
        self.start()
        request = self.s.request()
        before = self.s.revision
        for text, uses in ((" ", ()), ("x", (self.s.ref("source"),) * 2)):
            with self.assertRaises(ContractError):
                self.answer(request, text, uses)
        self.assertEqual(before, self.s.revision)

    def test_direct_reservation_cannot_leak_peer_output(self):
        self.start()
        first = self.answer(self.s.request(), "not for peer")
        status = self.s.round_status()
        round_ref = ArtifactRef.from_dict(status["round"]["ref"])
        with self.assertRaises(ContractError):
            self.s.reserve("lil", "propose", "leak", refs=(round_ref, first),
                           context={"duet_round": round_ref.to_dict(), "mode": "peer_duet"},
                           expected_revision=self.s.revision)
        with self.assertRaises(ContractError):
            self.s.reserve("bro", "propose", "wrong lane", expected_revision=self.s.revision)

    def test_second_handle_rejects_stale_round(self):
        with DuetSession(self.path, "episode") as other:
            revision = other.revision
            self.start()
            with self.assertRaises(ContractError):
                other.start_round("peer_duet", "propose", "stale", expected_revision=revision)


if __name__ == "__main__":
    unittest.main()
