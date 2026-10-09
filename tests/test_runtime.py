# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 Ryo Higa / SpiralReality. All rights reserved.
from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from conductor import Conductor, Ledger, Limits
from conductor.adapters import AdapterError, ScriptedWorker
from conductor.runtime import export_run, render_answer
from conductor.scenarios import capacity_task
from conductor.types import Reply, Usage


class RuntimeTests(unittest.TestCase):
    def make(self, speculation=True, limits=None, fast=None, careful=None):
        ledger = Ledger(":memory:", capacity_task())
        self.addCleanup(ledger.close)
        f = fast or ScriptedWorker("fast", corrupt={"workers"})
        c = careful or ScriptedWorker("careful", cost_units=3)
        return ledger, Conductor(ledger, f, c, limits, speculation)

    def test_wrong_fact_repaired_and_answer_flips(self):
        ledger, c = self.make()
        out = c.run()
        self.assertTrue(out["complete"])
        self.assertIs(out["results"]["can_deliver"], True)
        self.assertEqual(out["usage"]["calls"], 9)
        changes = [e["payload"]["ops"][0] for e in ledger.events() if e["kind"] == "patch_committed"]
        old = [op for op in changes if op["claim_id"] == "can_deliver"]
        self.assertEqual([op["value"] for op in old], [False, True])
        self.assertEqual(out["verification_failures"], 1)

    def test_independent_branch_is_not_recomputed(self):
        ledger, c = self.make()
        c.run()
        calls = [e for e in ledger.events() if e["kind"] == "call_reserved" and e["payload"]["claim_id"] == "site_name"]
        self.assertEqual(len(calls), 1)

    def test_non_speculative_mode_avoids_two_wasted_calls(self):
        _, c = self.make(speculation=False)
        out = c.run()
        self.assertTrue(out["complete"])
        self.assertEqual(out["usage"]["calls"], 7)

    def test_call_budget_stops_without_unverified_answer(self):
        _, c = self.make(limits=Limits(max_calls=1))
        out = c.run()
        self.assertFalse(out["complete"])
        self.assertEqual(out["stop_reason"], "budget_exhausted")
        self.assertNotIn("can_deliver", out["results"])
        self.assertIn("INCOMPLETE", render_answer(out))

    def test_cost_budget(self):
        _, c = self.make(limits=Limits(max_cost_units=6))
        out = c.run()
        self.assertFalse(out["complete"])
        self.assertLessEqual(out["usage"]["cost_units"], 6)

    def test_output_reservation_budget(self):
        _, c = self.make(limits=Limits(max_reserved_output_tokens=512))
        out = c.run()
        self.assertEqual(out["usage"]["calls"], 1)
        self.assertEqual(out["usage"]["reserved_output_tokens"], 512)
        self.assertIsNone(out["usage"]["prompt_tokens"])

    def test_resume_keeps_spent_calls(self):
        ledger, c = self.make(limits=Limits(max_calls=3))
        out = c.run()
        self.assertEqual(out["usage"]["calls"], 3)
        out = Conductor(ledger, ScriptedWorker("fast", corrupt={"workers"}), ScriptedWorker("careful", 3)).run()
        self.assertTrue(out["complete"])
        # Budget exhaustion ran pending checks before speculative descendants existed.
        # Three prior reservations plus four resumed calls; nothing is refunded.
        self.assertEqual(out["usage"]["calls"], 7)

    def test_already_complete_resume_does_not_call_workers(self):
        ledger, c = self.make()
        c.run()
        fresh = ScriptedWorker("unused")
        report = Conductor(ledger, fresh, fresh).run()
        self.assertTrue(report["complete"])
        self.assertEqual(fresh.calls, 0)

    def test_worker_error_escalates_and_charges_attempt(self):
        class Failing(ScriptedWorker):
            def propose(self, dispatch, timeout_s):
                raise AdapterError("intentional")
        _, c = self.make(fast=Failing("broken"))
        out = c.run()
        self.assertTrue(out["complete"])
        self.assertEqual(out["usage"]["calls"], 12)

    def test_repeated_failure_terminates(self):
        class Failing(ScriptedWorker):
            def propose(self, dispatch, timeout_s):
                raise AdapterError("intentional")
        _, c = self.make(fast=Failing("f"), careful=Failing("c"))
        out = c.run()
        self.assertEqual(out["stop_reason"], "attempt_limit")
        self.assertEqual(out["usage"]["calls"], 3)

    def test_deadline_discards_late_reply(self):
        class Slow(ScriptedWorker):
            def propose(self, dispatch, timeout_s):
                time.sleep(0.04)
                return super().propose(dispatch, timeout_s)
        _, c = self.make(fast=Slow("slow"), limits=Limits(deadline_s=0.015))
        out = c.run()
        self.assertFalse(out["complete"])
        self.assertEqual(out["stop_reason"], "deadline")
        self.assertEqual(out["claims"]["workers"]["status"], "missing")

    def test_provider_overrun_is_recorded_and_stopped(self):
        class Overrun(ScriptedWorker):
            def propose(self, dispatch, timeout_s):
                reply = super().propose(dispatch, timeout_s)
                return Reply(reply.patch, Usage(completion_tokens=999999))
        _, c = self.make(fast=Overrun("overrun"))
        out = c.run()
        self.assertFalse(out["complete"])
        self.assertEqual(out["stop_reason"], "provider_output_overrun")

    def test_malicious_verification_payload_not_accepted(self):
        class Forging(ScriptedWorker):
            def propose(self, dispatch, timeout_s):
                reply = super().propose(dispatch, timeout_s)
                reply.patch["ops"][0]["op"] = "verify"
                return reply
        _, c = self.make(fast=Forging("forging"))
        out = c.run()
        self.assertTrue(out["complete"])
        self.assertTrue(all(v["proposer"] != "forging" for v in out["claims"].values()))

    def test_export_includes_replayable_trace(self):
        ledger, c = self.make()
        out = c.run()
        with tempfile.TemporaryDirectory() as d:
            export_run(ledger, out, Path(d))
            expected = {"report.json", "task.json", "answer.txt", "events.jsonl", "TRACE.md"}
            self.assertEqual({p.name for p in Path(d).iterdir()}, expected)
            self.assertIn("verification_failed", (Path(d) / "events.jsonl").read_text())
