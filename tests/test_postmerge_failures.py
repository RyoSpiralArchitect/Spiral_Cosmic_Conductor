# SPDX-License-Identifier: AGPL-3.0-only
"""Regressions from the 2026-10-13 live pilot; no provider calls."""
import json
import tempfile
import unittest
from pathlib import Path

from conductor.adapters import ScriptedWorker, worker_messages
from conductor.cosmic import run_cosmic, verify_bundle
from conductor.ledger import Ledger
from conductor.runtime import Conductor, Limits
from conductor.scenarios import capacity_task
from conductor.types import Reply, Usage

FIXTURE = json.loads((Path(__file__).parent / "fixtures/provider_failures_2026-10-13.json").read_text())


def recorded_patch(name):
    return json.loads(FIXTURE["responses"][name]["raw_content"])


class RecordedFailureTests(unittest.TestCase):
    def test_missing_reference_rejected_then_repair_gets_fact_pointer_and_failure(self):
        seen = []

        class MissingReference(ScriptedWorker):
            def propose(self, dispatch, timeout_s):
                if dispatch.view["write_ids"] == ["demand"]:
                    return Reply(recorded_patch("missing_fact_evidence"), Usage())
                return super().propose(dispatch, timeout_s)

        class Repair(ScriptedWorker):
            def propose(self, dispatch, timeout_s):
                seen.append(json.loads(worker_messages(dispatch)[1]["content"])["target_contract"])
                return super().propose(dispatch, timeout_s)

        with Ledger(":memory:", capacity_task()) as ledger:
            report = Conductor(ledger, MissingReference("fast"), Repair("careful", 3)).run()
            self.assertTrue(report["complete"])
            self.assertEqual(report["usage"]["calls"], 7)
            self.assertEqual(report["verification_failures"], 0)
            self.assertEqual(report["claims"]["demand"]["value"], 240)
            self.assertEqual(len(seen), 1)
            contract = seen[0]
            self.assertEqual(contract["required_op"], "add_claim")
            self.assertEqual(contract["required_evidence_ids"], ["current"])
            self.assertEqual(contract["value_spec"], {"kind": "fact", "source": "current", "field": "demand"})
            self.assertEqual(contract["repair_feedback"]["reason"], "missing_source_reference")
            self.assertNotIn("value", contract)
            rejections = [e["payload"] for e in ledger.events() if e["kind"] == "patch_rejected"]
            self.assertEqual(rejections, [{"reason": "missing_source_reference", "write_ids": ["demand"]}])

    def test_recorded_value_errors_remain_unverified(self):
        for name, cid in (("wrong_fact_value", "per_worker"), ("wrong_repair_value", "demand")):
            with self.subTest(name=name), Ledger(":memory:", capacity_task()) as ledger:
                if name == "wrong_repair_value":
                    initial = ledger.issue("initial", "proposer", {cid})
                    reply = ScriptedWorker("initial").propose(initial, 1)
                    self.assertTrue(ledger.apply(initial.ticket, reply.patch).accepted)
                dispatch = ledger.issue("recorded", "repairer", {cid})
                self.assertTrue(ledger.apply(dispatch.ticket, recorded_patch(name)).accepted)
                self.assertEqual(ledger.verify(cid).reason, "value_mismatch")
                self.assertEqual(ledger.snapshot()[cid]["status"], "invalidated")

    def test_recorded_derived_reference_cannot_break_cosmic_export(self):
        class BadRepair(ScriptedWorker):
            def propose(self, dispatch, timeout_s):
                if dispatch.view["write_ids"] == ["capacity"]:
                    return Reply(recorded_patch("derived_source_evidence"), Usage())
                return super().propose(dispatch, timeout_s)

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "run"
            result = run_cosmic(capacity_task(), ScriptedWorker("fast", corrupt={"workers"}),
                                BadRepair("careful", 3), out,
                                limits=Limits(max_calls=9, max_attempts_per_claim=2))
            # This repair still fails. It must remain an incomplete run with valid receipts.
            self.assertFalse(result.complete)
            self.assertEqual(result.stop_reason, "attempt_limit")
            self.assertTrue(verify_bundle(out)["manifest_valid"])
            events = [json.loads(line) for line in (out / "events.jsonl").read_text().splitlines()]
            self.assertEqual([e["payload"]["reason"] for e in events if e["kind"] == "patch_rejected"],
                             ["evidence_outside_target_scope"])
            capsules = [json.loads(line) for line in (out / "capsules.jsonl").read_text().splitlines()]
            self.assertTrue(all(c["evidence_ids"] == [] for c in capsules if c["claim_id"] == "capacity"))
            self.assertEqual(recorded_patch("derived_source_evidence")["ops"][0]["evidence_ids"], ["current"])
