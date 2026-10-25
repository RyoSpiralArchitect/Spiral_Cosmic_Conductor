# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 Ryo Higa / SpiralReality
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from conductor.multipolar import Agent, MultipolarBroker
from conductor.shared_memory import ArtifactRef, MemoryLimits, Publication, SharedMemory, text_hash
from conductor.text_workflow import open_text_memory, seed
from conductor.types import ContractError


class SharedMemoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "memory.sqlite"
        self.memory = open_text_memory(self.path, "episode")
        seed(self.memory, "原文の条件を保持する。")

    def tearDown(self):
        self.memory.close()
        self.tmp.cleanup()

    def source_ref(self):
        return ArtifactRef.from_dict(self.memory.snapshot("proposer", {"memo"})["artifacts"][0]["ref"])

    def publish_draft(self):
        return self.memory.commit("proposer", [Publication("draft", "提案である。", (self.source_ref(),))], expected_revision=1)[0]

    def test_durable_reopen_exact_history_and_episode_contract(self):
        artifact = self.publish_draft()
        with open_text_memory(self.path, "episode") as reopened:
            self.assertEqual(reopened.history("critic", "draft"), [artifact])
            self.assertEqual(reopened.audit(), self.memory.audit())
        with self.assertRaises(ContractError):
            open_text_memory(self.path, "another_episode")

    def test_stale_reply_between_two_connections_is_rejected_without_retry(self):
        with open_text_memory(self.path, "episode") as other:
            snapshot = other.snapshot("proposer", {"memo"})
            self.publish_draft()
            before = self.memory.audit()
            with self.assertRaises(ContractError):
                other.commit("proposer", [Publication("draft", "古い応答。")], expected_revision=snapshot["revision"])
            self.assertEqual(self.memory.audit(), before)

    def test_source_update_blocks_transitively_stale_dispatch_and_parent_use(self):
        draft = self.publish_draft()
        draft_ref = ArtifactRef.from_dict(draft["ref"])
        self.memory.commit("critic", [Publication("review", "批評。", (draft_ref,))], expected_revision=2)
        self.memory.commit("controller", [Publication("memo", "更新した条件。")], expected_revision=3)
        with self.assertRaises(ContractError):
            self.memory.snapshot("reviser", {"review"})
        old = self.memory.snapshot("reviser", {"review"}, allow_stale=True)
        self.assertFalse(old["artifacts"][0]["fresh"])
        with self.assertRaises(ContractError):
            self.memory.commit("reviser", [Publication("revision", "古い親を使う。", (draft_ref,))], expected_revision=4)
        self.assertEqual(len(self.memory.history("controller", "memo")), 2)
        self.assertEqual(self.memory.history("critic", "draft")[0], draft)

    def test_private_context_and_scope_do_not_cross_into_public_views(self):
        self.memory.broker.remember_private("critic", {"secret": "PRIVATE_SENTINEL"})
        public = self.memory.snapshot("proposer", {"memo", "principles"})
        self.assertNotIn("PRIVATE_SENTINEL", json.dumps(public))
        self.assertFalse(public["private_context_exported"])
        with self.assertRaises(ContractError):
            self.memory.snapshot("proposer", {"review"})
        with self.assertRaises(ContractError):
            self.memory.commit("critic", [Publication("memo", "上書き。")], expected_revision=1)

    def test_cross_episode_or_forged_hash_parents_are_rejected(self):
        good = self.source_ref()
        for bad in (ArtifactRef("other", good.id, good.version, good.sha256),
                    ArtifactRef(good.episode, good.id, good.version, "f" * 64)):
            with self.assertRaises(ContractError):
                self.memory.commit("proposer", [Publication("draft", "提案。", (bad,))], expected_revision=1)
        self.assertEqual(self.memory.audit()["event_count"], 1)

    def test_atomic_batch_rollback_preserves_no_partial_revision(self):
        with self.assertRaises(ContractError):
            self.memory.commit("reviser", [Publication("revision", "改稿。"), Publication("trace", "")], expected_revision=1)
        self.assertEqual(self.memory.history("reviser", "revision"), [])
        self.assertEqual(self.memory.audit()["event_count"], 1)

    def test_batch_trace_can_reference_the_new_revision(self):
        ref = ArtifactRef("episode", "revision", 1, text_hash("改稿。"))
        self.memory.commit("reviser", [Publication("revision", "改稿。"), Publication("trace", "追跡。", (ref,))], expected_revision=1)
        self.assertTrue(self.memory.snapshot("controller", {"trace"})["artifacts"][0]["fresh"])
        self.assertEqual(self.memory.audit()["event_count"], 2)

    def test_self_parent_is_rejected_instead_of_creating_instant_staleness(self):
        first = self.publish_draft()
        with self.assertRaises(ContractError):
            self.memory.commit("proposer", [Publication("draft", "改稿。", (ArtifactRef.from_dict(first["ref"]),))], expected_revision=2)

    def test_replacing_an_ancestor_through_its_descendant_is_rejected(self):
        draft = self.publish_draft()
        with self.assertRaises(ContractError):
            self.memory.commit("controller", [Publication("memo", "循環する新条件。", (ArtifactRef.from_dict(draft["ref"]),))], expected_revision=2)
        self.assertEqual(self.memory.audit()["event_count"], 2)

    def test_later_parent_replacement_in_a_batch_cannot_stale_its_new_child(self):
        agent = Agent("host", frozenset({"parent", "child"}), frozenset({"parent", "child"}))
        with SharedMemory(Path(self.tmp.name) / "batch.sqlite", "batch", MultipolarBroker([agent]),
                          {"parent": "source", "child": "proposal"}) as memory:
            parent = memory.commit("host", [Publication("parent", "原文。")], expected_revision=0)[0]
            with self.assertRaises(ContractError):
                memory.commit("host", [Publication("child", "旧条件への提案。", (ArtifactRef.from_dict(parent["ref"]),)),
                                       Publication("parent", "新条件。")], expected_revision=1)
            self.assertEqual(memory.history("host", "child"), [])
            self.assertEqual(memory.audit()["event_count"], 1)

    def test_tampered_event_fails_audit_and_reopen(self):
        db = sqlite3.connect(self.path)
        row = db.execute("SELECT body FROM memory_events WHERE seq=1").fetchone()[0]
        event = json.loads(row)
        event["artifacts"][0]["text"] = "改変"
        db.execute("UPDATE memory_events SET body=? WHERE seq=1", (json.dumps(event),))
        db.commit()
        db.close()
        with self.assertRaises(ContractError):
            self.memory.audit()
        with self.assertRaises(ContractError):
            open_text_memory(self.path, "episode")

    def test_limits_reject_atomically_and_views_do_not_truncate(self):
        agent = Agent("host", frozenset({"x"}), frozenset({"x"}))
        path = Path(self.tmp.name) / "small.sqlite"
        limits = MemoryLimits(max_events=1, max_artifact_chars=20, max_storage_bytes=2000, max_view_chars=50)
        with SharedMemory(path, "small", MultipolarBroker([agent]), {"x": "source"}, limits) as m:
            with self.assertRaises(ContractError):
                m.commit("host", [Publication("x", "x" * 21)], expected_revision=0)
            m.commit("host", [Publication("x", "x")], expected_revision=0)
            with self.assertRaises(ContractError):
                m.snapshot("host", {"x"})
            with self.assertRaises(ContractError):
                m.commit("host", [Publication("x", "y")], expected_revision=1)
            self.assertEqual(m.audit()["event_count"], 1)

    def test_storage_budget_is_checked_before_inserting(self):
        agent = Agent("host", frozenset({"x"}), frozenset({"x"}))
        with SharedMemory(Path(self.tmp.name) / "bytes.sqlite", "bytes", MultipolarBroker([agent]),
                          {"x": "source"}, MemoryLimits(max_storage_bytes=100)) as m:
            with self.assertRaises(ContractError):
                m.commit("host", [Publication("x", "短い文。")], expected_revision=0)
            self.assertEqual(m.audit()["event_count"], 0)

    def test_returned_objects_cannot_mutate_persistent_history(self):
        public = self.memory.snapshot("proposer", {"memo"})
        public["artifacts"][0]["text"] = "caller mutation"
        self.assertNotEqual(self.memory.history("controller", "memo")[0]["text"], "caller mutation")


if __name__ == "__main__":
    unittest.main()
