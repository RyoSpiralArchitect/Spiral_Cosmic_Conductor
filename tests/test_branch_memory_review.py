# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 Ryo Higa / SpiralReality
import tempfile
import unittest
from pathlib import Path

from conductor.branch_memory import BranchMemory
from conductor.breath import BreathController
from conductor.duet import DuetSession


class BranchMemoryReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.s = DuetSession(Path(self.tmp.name) / "review.sqlite", "review")
        self.s.begin("Plan B is held until permission changes", {"goal": "reconsider"})
        self.m = BranchMemory(self.s)
        self.m.create("root", "start", expected_revision=self.s.revision)
        self.node = self.m.remember("hypothesis", "Plan B", "await explicit permission",
                                    evidence=(self.s.ref("source"),), disposition="held",
                                    reopen_on=("permission",), expected_revision=self.s.revision)
        self.m.attach("root", self.node, "retain", expected_revision=self.s.revision)
        request = self.s.reserve("bro", "evidence_update", "new permission evidence", expected_revision=self.s.revision)
        self.evidence = self.s.submit(request, {"text": "Permission is now granted", "uses": [], "disposition": "candidate"})
        self.condition = self.m.condition("permission", "satisfied", "host assessment from new evidence",
                                           evidence=(self.evidence,), expected_revision=self.s.revision)

    def tearDown(self):
        self.s.close()
        self.tmp.cleanup()

    def next_request(self):
        breath = BreathController(self.s)
        breath.signal("start", "explicit reconsideration", expected_revision=self.s.revision)
        self.m.open_round("root", (self.node,), "peer_duet", "propose", "reconsider selected plan",
                          expected_revision=self.s.revision)
        return breath.request()

    def test_reconsidered_node_carries_current_condition_and_evidence(self):
        self.assertEqual(self.m.reconsider("root")[0]["node"], self.node.to_dict())
        request = self.next_request()
        refs = [doc["ref"] for doc in request.payload["documents"]]
        self.assertIn(self.condition.to_dict(), refs)
        self.assertIn(self.evidence.to_dict(), refs)
        self.assertEqual(self.m.node(self.node)["disposition"], "held")

    def test_unrelated_condition_is_not_projected(self):
        unrelated = self.m.condition("unrelated", "satisfied", "UNRELATED_CONDITION_MUST_STAY_OUT",
                                      evidence=(self.s.ref("source"),), expected_revision=self.s.revision)
        request = self.next_request()
        refs = [doc["ref"] for doc in request.payload["documents"]]
        self.assertIn(self.condition.to_dict(), refs)
        self.assertNotIn(unrelated.to_dict(), refs)
        self.assertNotIn("UNRELATED_CONDITION_MUST_STAY_OUT", request.payload_json)

    def test_revoked_condition_projects_latest_assessment_only(self):
        latest = self.m.condition("permission", "unknown", "permission assessment withdrawn",
                                  evidence=(self.s.ref("source"),), expected_revision=self.s.revision)
        self.assertEqual(self.m.reconsider("root"), [])
        request = self.next_request()  # explicit host selection is still allowed
        refs = [doc["ref"] for doc in request.payload["documents"]]
        self.assertIn(latest.to_dict(), refs)
        self.assertNotIn(self.condition.to_dict(), refs)
        self.assertNotIn(self.evidence.to_dict(), refs)

    def test_reconsider_is_read_only(self):
        before = self.s.memory.audit()
        self.assertEqual(len(self.m.reconsider("root")), 1)
        self.assertEqual(before, self.s.memory.audit())
        self.assertIsNone(self.s.pending())
