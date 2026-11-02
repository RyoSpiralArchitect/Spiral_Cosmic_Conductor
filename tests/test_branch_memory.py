# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 Ryo Higa / SpiralReality
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from conductor.branch_memory import BranchMemory
from conductor.breath import BreathController
from conductor.duet import DuetSession
from conductor.reflux import Finding, RefluxController
from conductor.shared_memory import ArtifactRef
from conductor.types import ContractError


class BranchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "branches.sqlite"
        self.s = DuetSession(self.path, "episode")
        self.s.begin("Source permits distinct interpretations", {"mode": "explore"})
        self.m = BranchMemory(self.s)
        self.root = self.m.create("root", "start", expected_revision=self.s.revision)

    def tearDown(self):
        self.s.close()
        self.tmp.cleanup()

    def remember(self, text="hypothesis", disposition="proposed", conditions=(), evidence=None):
        return self.m.remember("hypothesis", text, "explicit reason", evidence=evidence or (self.s.ref("source"),),
                               disposition=disposition, reopen_on=conditions, expected_revision=self.s.revision)

    def attach(self, node, branch="root"):
        return self.m.attach(branch, node, "retain", expected_revision=self.s.revision)

    def test_convenience_import_is_same_api(self):
        from conductor import intervention
        self.assertIs(intervention.DuetSession, DuetSession)
        self.assertIs(intervention.BranchMemory, BranchMemory)
        self.assertIs(intervention.RefluxController, RefluxController)

    def test_fork_isolated_and_source_unchanged(self):
        node = self.remember()
        root = self.attach(node)
        self.m.fork("root", "alternative", "branch", expected_revision=self.s.revision)
        other = self.remember("alternative only")
        self.attach(other, "alternative")
        self.assertEqual(root.to_dict(), self.m.head("root")["ref"])
        self.assertEqual(len(self.m.head("root")["data"]["nodes"]), 1)
        self.assertEqual(len(self.m.head("alternative")["data"]["nodes"]), 2)

    def test_rewind_retains_history_and_does_not_delete_nodes(self):
        a = self.remember("first")
        h = self.attach(a)
        b = self.remember("second")
        self.attach(b)
        self.m.rewind("root", h, "return", expected_revision=self.s.revision)
        self.assertEqual(self.m.head("root")["data"]["nodes"], [a.to_dict()])
        self.assertEqual(self.m.node(b)["summary"], "second")
        self.assertEqual(len(self.s.notes("narrative.branch.v1")), 4)

    def test_cross_branch_rewind_rejected(self):
        h = self.m.fork("root", "other", "x", expected_revision=self.s.revision)
        with self.assertRaises(ContractError):
            self.m.rewind("root", h, "bad", expected_revision=self.s.revision)

    def test_merge_retains_opposed_dispositions(self):
        self.m.fork("root", "other", "x", expected_revision=self.s.revision)
        a, b = self.remember("same idea", "adopted"), self.remember("same idea", "rejected")
        self.attach(a)
        self.attach(b, "other")
        self.m.merge("root", ("other",), "keep disagreement", expected_revision=self.s.revision)
        nodes = self.m.head("root")["data"]["nodes"]
        self.assertEqual(nodes, [a.to_dict(), b.to_dict()])
        self.assertEqual(self.m.node(b)["disposition"], "rejected")
        self.assertEqual(self.m.head("root")["data"]["merge_semantics"], "retain_not_consensus")

    def test_reconsider_requires_all_conditions_and_never_adopts(self):
        node = self.remember("use plan B", "held", ("permission", "capacity"))
        self.attach(node)
        for condition in ("permission", "capacity"):
            self.assertFalse(self.m.reconsider("root"))
            self.m.condition(condition, "satisfied", "explicit evidence", evidence=(self.s.ref("source"),),
                             expected_revision=self.s.revision)
        result = self.m.reconsider("root")
        self.assertEqual(result[0]["node"], node.to_dict())
        self.assertEqual(result[0]["status"], "candidate_for_reconsideration")
        self.assertEqual(self.m.node(node)["disposition"], "held")
        self.m.condition("permission", "unknown", "withdrawn assessment", evidence=(self.s.ref("source"),),
                         expected_revision=self.s.revision)
        self.assertFalse(self.m.reconsider("root"))

    def test_search_is_branch_scoped_and_lexical(self):
        self.m.fork("root", "other", "x", expected_revision=self.s.revision)
        a, b = self.remember("battery", "held"), self.remember("battery hidden")
        self.attach(a)
        self.attach(b, "other")
        result = self.m.search("root", "battery", dispositions=("held",))
        self.assertEqual([r["ref"] for r in result], [a.to_dict()])
        self.assertEqual(result[0]["semantic_status"], "unassessed")

    def test_summaries_are_not_primary_evidence(self):
        n = self.remember()
        with self.assertRaises(ContractError):
            self.remember("laundered source", evidence=(n,))

    def test_duplicate_nodes_and_branches_rejected(self):
        n = self.remember()
        self.attach(n)
        with self.assertRaises(ContractError):
            self.attach(n)
        with self.assertRaises(ContractError):
            self.m.create("root", "again", expected_revision=self.s.revision)

    def test_no_empty_or_verified_nodes(self):
        with self.assertRaises(ContractError):
            self.remember(disposition="verified")
        with self.assertRaises(ContractError):
            self.m.remember("hypothesis", "x", "reason", expected_revision=self.s.revision)

    def test_no_evidence_free_condition(self):
        with self.assertRaises(ContractError):
            self.m.condition("capacity", "satisfied", "because", expected_revision=self.s.revision)

    def test_stale_write_atomic(self):
        n = self.remember()
        before = self.s.revision
        with self.assertRaises(ContractError):
            self.m.attach("root", n, "x", expected_revision=before - 1)
        self.assertEqual(before, self.s.revision)
        self.assertEqual(self.m.head("root")["data"]["nodes"], [])

    def test_cross_episode_node_rejected(self):
        with self.assertRaises(ContractError):
            self.attach(ArtifactRef("another", "control_0", 1, "0" * 64))

    def test_explicit_selected_memory_enters_new_round(self):
        a, b = self.remember("selected"), self.remember("NEVER_INCLUDE_UNSELECTED")
        self.attach(a)
        self.attach(b)
        breath = BreathController(self.s)
        breath.signal("start", "x", expected_revision=self.s.revision)
        self.m.open_round("root", (a,), "peer_duet", "propose", "recall selected only", expected_revision=self.s.revision)
        request = breath.request()
        self.assertIn("selected", request.payload_json)
        self.assertNotIn("NEVER_INCLUDE_UNSELECTED", request.payload_json)
        self.assertNotIn(b.to_dict(), [d["ref"] for d in request.payload["documents"]])

    def test_cross_branch_selection_rejected(self):
        self.m.fork("root", "other", "x", expected_revision=self.s.revision)
        n = self.remember("other")
        self.attach(n, "other")
        with self.assertRaises(ContractError):
            self.m.open_round("root", (n,), "peer_duet", "propose", "bad", expected_revision=self.s.revision)

    def test_reopen_replays_identical_heads_and_nodes(self):
        n = self.remember("pending", "held", ("evidence",))
        self.attach(n)
        before = self.m.head("root")
        self.s.close()
        self.s = DuetSession(self.path, "episode")
        self.m = BranchMemory(self.s)
        self.assertEqual(before, self.m.head("root"))
        self.assertEqual(self.m.node(n)["reopen_on"], ["evidence"])

    def test_four_mechanisms_integrate_without_network(self):
        with patch("socket.socket", side_effect=AssertionError("no network")):
            b, r = BreathController(self.s), RefluxController(self.s)
            b.signal("start", "explore", expected_revision=self.s.revision)
            b.open_round("peer_duet", "propose", "two alternatives", expected_revision=self.s.revision)
            outputs = tuple(self.s.submit(b.request(), {"text": text, "uses": [], "disposition": "candidate"})
                            for text in ("keep two alternatives", "allow optional agreement"))
            b.signal("candidates", "compare", refs=outputs, expected_revision=self.s.revision)
            n = self.remember("unresolved alternatives", "held", ("permission",), outputs)
            self.attach(n)
            p = r.propose(Finding("productive_difference", outputs[0], (outputs[1],), "both useful", "lil"),
                          expected_revision=self.s.revision)
            self.m.fork_from_reflux(p, "root", "parallel", "retain disagreement", expected_revision=self.s.revision)
            self.assertEqual(r.report(p)["state"], "forked")
            with self.assertRaises(ContractError):
                self.m.fork_from_reflux(p, "root", "again", "no repeat", expected_revision=self.s.revision)
            b.signal("recompose", "another pass", refs=outputs, expected_revision=self.s.revision)
            self.m.open_round("parallel", (n,), "lead_lil", "propose", "change lead", expected_revision=self.s.revision)
            self.assertEqual(b.request().worker, "lil")
            self.s.hold_pending("offline test ends here", expected_revision=self.s.revision)
            b.signal("hold", "await real experiment", expected_revision=self.s.revision)
            self.assertTrue(self.s.memory.audit()["structural_integrity"])
            self.assertEqual(self.m.node(n)["disposition"], "held")

    def test_reflux_fork_requires_target_membership(self):
        b, r = BreathController(self.s), RefluxController(self.s)
        b.signal("start", "x", expected_revision=self.s.revision)
        b.open_round("peer_duet", "propose", "x", expected_revision=self.s.revision)
        outputs = tuple(self.s.submit(b.request(), {"text": "candidate", "uses": [], "disposition": "candidate"}) for _ in range(2))
        b.signal("candidates", "x", refs=outputs, expected_revision=self.s.revision)
        p = r.propose(Finding("productive_difference", outputs[0], (outputs[1],), "x", "lil"), expected_revision=self.s.revision)
        with self.assertRaises(ContractError):
            self.m.fork_from_reflux(p, "root", "bad", "unrelated root", expected_revision=self.s.revision)


if __name__ == "__main__":
    unittest.main()
