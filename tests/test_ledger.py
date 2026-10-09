# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 Ryo Higa / SpiralReality. All rights reserved.
from __future__ import annotations

import copy
import json
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from conductor import ContractError, Ledger, Task
from conductor.scenarios import capacity_task
from conductor.types import canonical, parse_patch, strict_json


def patch(cid, value, refs=None, op="add_claim"):
    return {"ops": [{"op": op, "claim_id": cid, "value": value, "evidence_ids": refs or [], "reason": "test"}]}


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.ledger = Ledger(":memory:", capacity_task())
        self.addCleanup(self.ledger.close)

    def put(self, cid, value, refs=None, role="proposer", worker="worker"):
        status = self.ledger.snapshot()[cid]["status"]
        d = self.ledger.issue(worker, role, {cid})
        return self.ledger.apply(d.ticket, patch(cid, value, refs, "add_claim" if status == "missing" else "revise_claim"))

    def test_initial_state_unverified(self):
        self.assertTrue(all(c["status"] == "missing" for c in self.ledger.snapshot().values()))

    def test_valid_proposal_is_not_automatically_verified(self):
        self.assertTrue(self.put("workers", 4, ["current"]).accepted)
        self.assertEqual(self.ledger.snapshot()["workers"]["status"], "candidate")
        self.assertTrue(self.ledger.verify("workers").accepted)

    def test_scope_blocks_other_claims(self):
        d = self.ledger.issue("limited", "proposer", {"workers"})
        r = self.ledger.apply(d.ticket, patch("site_name", "X", ["current"]))
        self.assertEqual(r.reason, "out_of_scope")
        self.assertEqual(self.ledger.snapshot()["site_name"]["rev"], 0)

    def test_model_cannot_claim_verification_authority(self):
        d = self.ledger.issue("worker", "proposer", {"workers"})
        r = self.ledger.apply(d.ticket, patch("workers", 4, ["current"], "verify"))
        self.assertFalse(r.accepted)
        self.assertEqual(r.reason, "invalid_patch_schema")

    def test_forged_role_in_payload_rejected(self):
        d = self.ledger.issue("worker", "proposer", {"workers"})
        p = patch("workers", 4, ["current"])
        p["role"] = "verifier"
        self.assertFalse(self.ledger.apply(d.ticket, p).accepted)

    def test_unseen_evidence_rejected(self):
        d = self.ledger.issue("worker", "proposer", {"workers"})
        r = self.ledger.apply(d.ticket, patch("workers", 4, ["archived"]))
        self.assertEqual(r.reason, "unknown_or_unseen_evidence")

    def test_correct_value_without_source_is_not_verified(self):
        self.put("workers", 4)
        self.assertEqual(self.ledger.verify("workers").reason, "missing_source_reference")

    def test_read_context_excludes_unrelated_sources(self):
        d = self.ledger.issue("worker", "proposer", {"workers"})
        self.assertEqual(set(d.view["sources"]), {"current"})
        self.assertNotIn("site_name", d.view["claims"])
        self.assertNotIn("archived", d.view["sources"])

    def test_same_target_stale_read_rejected(self):
        a = self.ledger.issue("a", "proposer", {"workers"})
        b = self.ledger.issue("b", "proposer", {"workers"})
        self.assertTrue(self.ledger.apply(a.ticket, patch("workers", 4, ["current"])).accepted)
        self.assertEqual(self.ledger.apply(b.ticket, patch("workers", 3, ["current"])).reason, "stale_read_set")

    def test_dependency_change_causes_stale_read(self):
        self.put("workers", 4, ["current"])
        self.put("per_worker", 75, ["current"])
        d = self.ledger.issue("derive", "proposer", {"capacity"})
        self.put("workers", 3, ["current"])
        self.assertEqual(self.ledger.apply(d.ticket, patch("capacity", 300)).reason, "stale_read_set")

    def test_disjoint_writes_from_same_state_both_commit(self):
        a = self.ledger.issue("a", "proposer", {"workers"})
        b = self.ledger.issue("b", "proposer", {"site_name"})
        self.assertTrue(self.ledger.apply(a.ticket, patch("workers", 4, ["current"])).accepted)
        self.assertTrue(self.ledger.apply(b.ticket, patch("site_name", "Beppu", ["site"])).accepted)

    def test_grant_replay_refused(self):
        d = self.ledger.issue("a", "proposer", {"workers"})
        p = patch("workers", 4, ["current"])
        self.ledger.apply(d.ticket, p)
        self.assertEqual(self.ledger.apply(d.ticket, p).reason, "replayed_grant")

    def test_unknown_grant_refused(self):
        self.assertEqual(self.ledger.apply("fabricated", patch("workers", 4)).reason, "unknown_grant")

    def test_expired_grant_refused(self):
        d = self.ledger.issue("a", "proposer", {"workers"})
        self.ledger.db.execute("UPDATE grants SET expires=0 WHERE token=?", (d.ticket,))
        self.assertEqual(self.ledger.apply(d.ticket, patch("workers", 4)).reason, "expired_grant")

    def test_batch_is_atomic_on_invalid_second_operation(self):
        d = self.ledger.issue("a", "proposer", {"workers"})
        p = patch("workers", 4, ["current"])
        p["ops"] += patch("site_name", "X")["ops"]
        self.assertFalse(self.ledger.apply(d.ticket, p).accepted)
        self.assertEqual(self.ledger.snapshot()["workers"]["status"], "missing")

    def test_value_type_not_coerced(self):
        self.put("workers", "4", ["current"])
        self.assertEqual(self.ledger.verify("workers").reason, "value_mismatch")

    def test_dependency_failure_invalidates_only_descendants(self):
        self.put("workers", 3, ["current"])
        self.put("per_worker", 75, ["current"])
        self.put("demand", 240, ["current"])
        self.put("site_name", "Beppu", ["site"])
        self.put("capacity", 225)
        self.put("can_deliver", False)
        before = self.ledger.snapshot()["site_name"]
        result = self.ledger.verify("workers")
        self.assertEqual(set(result.invalidated), {"workers", "capacity", "can_deliver"})
        self.assertEqual(before, self.ledger.snapshot()["site_name"])
        self.assertTrue(self.ledger.audit()["state_replay_matches"])

    def test_verification_waits_for_dependencies(self):
        self.put("workers", 4, ["current"])
        self.put("per_worker", 75, ["current"])
        self.put("capacity", 300)
        self.assertEqual(self.ledger.verify("capacity").reason, "dependencies_not_verified")
        self.ledger.verify("workers")
        self.ledger.verify("per_worker")
        self.assertTrue(self.ledger.verify("capacity").accepted)

    def test_revision_clears_proof(self):
        self.put("workers", 4, ["current"])
        self.ledger.verify("workers")
        self.put("workers", 4, ["current"])
        claim = self.ledger.snapshot()["workers"]
        self.assertEqual(claim["status"], "candidate")
        self.assertIsNone(claim["verification"])

    def test_critic_can_challenge_but_not_edit(self):
        self.put("workers", 4, ["current"])
        self.ledger.verify("workers")
        d = self.ledger.issue("critic", "critic", {"workers"})
        result = self.ledger.apply(d.ticket, patch("workers", None, op="request_backtrack"))
        self.assertTrue(result.accepted)
        self.assertEqual(self.ledger.snapshot()["workers"]["status"], "challenged")
        self.assertTrue(self.ledger.verify("workers").accepted)
        d = self.ledger.issue("critic", "critic", {"workers"})
        self.assertEqual(self.ledger.apply(d.ticket, patch("workers", 8, op="revise_claim")).reason, "out_of_scope")

    def test_attach_evidence_requires_reverification(self):
        self.put("workers", 4)
        d = self.ledger.issue("repair", "repairer", {"workers"})
        self.assertTrue(self.ledger.apply(d.ticket, patch("workers", None, ["current"], "attach_evidence")).accepted)
        self.assertTrue(self.ledger.verify("workers").accepted)

    def test_grant_view_is_detached_from_state(self):
        d = self.ledger.issue("a", "proposer", {"workers"})
        d.view["claims"]["workers"]["value"] = 99
        d.view["sources"]["current"]["facts"]["workers"] = 99
        self.assertIsNone(self.ledger.snapshot()["workers"]["value"])
        self.assertEqual(self.ledger.task.sources["current"]["facts"]["workers"], 4)

    def test_tampered_event_detected(self):
        self.put("workers", 4, ["current"])
        self.ledger.db.execute("UPDATE events SET actor='changed' WHERE seq=1")
        with self.assertRaises(ContractError):
            self.ledger.audit()

    def test_tampered_snapshot_detected(self):
        claim = self.ledger.snapshot()["workers"]
        claim["value"] = 99
        self.ledger.db.execute("UPDATE claims SET body=? WHERE id='workers'", (canonical(claim),))
        with self.assertRaises(ContractError):
            self.ledger.audit()

    def test_controller_lease_blocks_second_controller(self):
        with self.ledger.controller_lease(10):
            with self.assertRaises(ContractError):
                with self.ledger.controller_lease(10):
                    pass
        with self.ledger.controller_lease(10):
            pass

    def test_resume_and_task_mismatch(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "ledger.sqlite"
            with Ledger(path, capacity_task()) as a:
                dispatch = a.issue("a", "proposer", {"workers"})
                a.apply(dispatch.ticket, patch("workers", 4, ["current"]))
            with Ledger(path, capacity_task()) as b:
                self.assertEqual(b.snapshot()["workers"]["value"], 4)
                b.audit()
            with self.assertRaises(ContractError):
                Ledger(path, capacity_task(workers=5))

    def test_concurrent_conflicting_connections_commit_once(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "ledger.sqlite"
            with Ledger(path, capacity_task()) as a, Ledger(path, capacity_task()) as b:
                da = a.issue("a", "proposer", {"workers"})
                db = b.issue("b", "proposer", {"workers"})
                with ThreadPoolExecutor(2) as pool:
                    results = list(pool.map(lambda pair: pair[0].apply(pair[1].ticket, patch("workers", 4, ["current"])), [(a, da), (b, db)]))
                self.assertEqual(sum(r.accepted for r in results), 1)
                self.assertTrue(a.audit()["state_replay_matches"])


class SchemaTests(unittest.TestCase):
    def test_duplicate_json_keys_rejected(self):
        with self.assertRaises(ContractError):
            strict_json('{"ops":[],"ops":[]}')

    def test_nan_json_rejected(self):
        with self.assertRaises(ContractError):
            strict_json('{"x":NaN}')

    def test_oversized_patch_rejected(self):
        with self.assertRaises(ContractError):
            parse_patch(patch("workers", "x" * 40000))

    def test_float_claim_rejected(self):
        with self.assertRaises(ContractError):
            parse_patch(patch("workers", 4.0))

    def test_cyclic_task_rejected(self):
        raw = capacity_task().to_dict()
        raw["slots"][0] = {"id": "workers", "kind": "mul", "deps": ["capacity", "demand"]}
        with self.assertRaises(ContractError):
            Task.from_dict(raw)

    def test_duplicate_slots_rejected(self):
        raw = capacity_task().to_dict()
        raw["slots"].append(raw["slots"][0])
        with self.assertRaises(ContractError):
            Task.from_dict(raw)

    def test_bad_evidence_rejected(self):
        raw = capacity_task().to_dict()
        raw["slots"][0]["source"] = "missing"
        with self.assertRaises(ContractError):
            Task.from_dict(raw)

    def test_missing_target_rejected(self):
        raw = capacity_task().to_dict()
        raw["targets"] = ["does-not-exist"]
        with self.assertRaises(ContractError):
            Task.from_dict(raw)
