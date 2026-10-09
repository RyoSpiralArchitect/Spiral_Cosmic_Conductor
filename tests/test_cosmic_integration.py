# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 Ryo Higa / SpiralReality
import json
import tempfile
import unittest
from pathlib import Path

from conductor.adapters import ScriptedWorker
from conductor.backtracking import derive_structural_credit
from conductor.control import BudgetRouter
from conductor.cosmic import run_cosmic, verify_bundle
from conductor.evidence import EvidenceIndex
from conductor.multipolar import Agent, MeaningCapsule, MultipolarBroker
from conductor.scenarios import capacity_task
from conductor.spiraltorch_bridge import RefractProposal, new_proposal, validated_backend_ready
from conductor.types import ContractError


class CosmicIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.task = capacity_task()

    def test_end_to_end_correction_with_capsules_credits_and_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = run_cosmic(self.task,
                                ScriptedWorker("fast", cost_units=1, corrupt={"workers"}),
                                ScriptedWorker("careful", cost_units=3), Path(tmp) / "run")
            self.assertTrue(result.complete)
            self.assertGreater(result.capsules, 0)
            self.assertEqual(result.structural_credit_events, 1)
            r = json.loads(result.report_path.read_text())
            self.assertEqual(r["results"]["can_deliver"], True)
            self.assertEqual(r["results"]["site_name"], "Beppu")
            root = Path(tmp) / "run"
            council = json.loads((root / "council.json").read_text())
            self.assertFalse(council["private_context_exported"])
            self.assertEqual(council["conflicts"][0]["state"], "resolved_by_verified_claim")
            self.assertEqual(json.loads((root / "telemetry.json").read_text())["measured_energy_joules"], None)
            plan = json.loads((root / "spiraltorch_refract_proposal.json").read_text())
            self.assertEqual(plan["status"], "NOT_EXECUTED")
            self.assertTrue(json.loads((root / "artifact_manifest.json").read_text())["sha256"])

    def test_artifact_manifest_detects_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "run"
            run_cosmic(self.task, ScriptedWorker("fast"), ScriptedWorker("careful"), out)
            self.assertTrue(verify_bundle(out)["manifest_valid"])
            (out / "telemetry.json").write_text("{}\n")
            with self.assertRaises(ContractError):
                verify_bundle(out)

    def test_broker_denial_blocks_actual_worker_dispatch(self):
        from conductor.ledger import Ledger
        from conductor.runtime import Conductor
        with tempfile.TemporaryDirectory() as tmp:
            w1, w2 = ScriptedWorker("fast"), ScriptedWorker("careful")
            broker = MultipolarBroker([Agent("fast", frozenset(), frozenset()),
                                      Agent("careful", frozenset(self.task.by_id), frozenset(self.task.by_id))])
            with Ledger(Path(tmp) / "ledger.sqlite", self.task) as ledger:
                report = Conductor(ledger, w1, w2, broker=broker).run()
                self.assertFalse(report["complete"])
                self.assertEqual(report["stop_reason"], "policy_refused")
                self.assertEqual(report["usage"]["calls"], 0)
                self.assertEqual(len(broker.refusals), 1)

    def test_resume_fails_for_unrelated_task_and_accepts_same(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "run"
            run_cosmic(self.task, ScriptedWorker("fast"), ScriptedWorker("careful"), p)
            with self.assertRaises(ContractError):
                run_cosmic(self.task, ScriptedWorker("fast"), ScriptedWorker("careful"), p)
            result = run_cosmic(self.task, ScriptedWorker("fast"), ScriptedWorker("careful"), p, resume=True)
            self.assertTrue(result.complete)

    def test_broker_refusal_private_context_and_conflict_retention(self):
        broker = MultipolarBroker([Agent("controller", frozenset({"slot"}), frozenset({"slot"})),
                                  Agent("observer", frozenset(), frozenset()),
                                  Agent("worker", frozenset({"slot"}), frozenset({"slot"}))])
        broker.remember_private("worker", {"note": "do not export"})
        with self.assertRaises(ContractError):
            broker.private_context("worker", "controller")
        self.assertFalse(broker.authorize_dispatch("observer", read_ids={"slot"}, write_ids={"slot"}))
        capsule = MeaningCapsule("cap_1", "worker", "controller", "slot", 12, "verified", (), "a"*64)
        self.assertFalse(broker.deliver(capsule).accepted)
        capsule = MeaningCapsule("cap_2", "worker", "controller", "slot", 12, "refused", (), "a"*64)
        self.assertFalse(broker.deliver(capsule).accepted)
        conflict = broker.record_conflict("slot", "b"*64, "disputed")
        self.assertEqual(conflict.state, "unresolved")
        export = broker.export()
        self.assertNotIn("do not export", str(export))
        self.assertEqual(len(export["conflicts"]), 1)

    def test_routing_escalates_and_budget_fails_closed(self):
        r = BudgetRouter()
        self.assertEqual(r.choose(claim_id="x", attempts=0, invalidated=False,
                                  available_cost_units=1, fast_cost_units=1, careful_cost_units=3).label, "fast")
        self.assertEqual(r.choose(claim_id="x", attempts=1, invalidated=True,
                                  available_cost_units=3, fast_cost_units=1, careful_cost_units=3).label, "careful")
        self.assertEqual(r.choose(claim_id="x", attempts=1, invalidated=True,
                                  available_cost_units=2, fast_cost_units=1, careful_cost_units=3).label, "none")

    def test_evidence_retrieval_scoped_and_bounded(self):
        index = EvidenceIndex(self.task)
        src = set(self.task.sources)
        hits = index.search("staff", authorized=src)
        self.assertLessEqual(len(hits), 4)
        self.assertTrue({h.source_id for h in hits} <= src)
        with self.assertRaises(ContractError):
            index.search("staff", authorized={"not_authorized"})

    def test_spiraltorch_proposal_is_never_self_authorized(self):
        proposal = new_proposal()
        self.assertEqual(proposal.to_dict()["status"], "NOT_EXECUTED")
        with self.assertRaises(ContractError):
            RefractProposal("test", "cpu", "matmul", "f32", "try", executed=True)
        self.assertFalse(validated_backend_ready({"backend": "cpu", "status": "ready",
                                                  "proof_type": "static_host_contract"}, proposal))
        self.assertTrue(validated_backend_ready({"backend": "cpu", "status": "ready",
                                                 "proof_type": "static_host_contract",
                                                 "trusted_local_adapter": True}, proposal))


if __name__ == "__main__":
    unittest.main()
