# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 Ryo Higa / SpiralReality
import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from conductor.shared_memory import Publication
from conductor.source_alignment import SourcePolicy, assess, candidate_units, schema, source_units
from conductor.text_conductor import TextConductor
from conductor.types import ContractError, canonical, strict_json

MEMO = "参加者は最大7人。追加の係は頼めない。氏名は集めない。日程は未確定。"
DRAFT = "参加者は最大7人。\n準備係を追加する。\n氏名を記録する。\n日程は未確定。"
FIXED = "参加者は最大7人。\n進行役だけで準備も行う。\n氏名を記録せずに参加する。\n日程は未確定。"


def judgment(bad=False):
    return {"sources": [{"id": f"M{i:02d}", "status": "changed" if bad and i in (2, 3) else "preserved",
                          "candidate_ids": [f"C{i:02d}"], "reason": "原条件との対応を点検。"} for i in range(1, 5)],
            "candidates": [{"id": f"C{i:02d}", "origin": "proposal" if i in (2, 3) else "source",
                            "source_ids": [f"M{i:02d}"], "compatibility": "conflict" if bad and i in (2, 3) else "compatible",
                            "reason": "原条件に照らして判断。"} for i in range(1, 5)]}


def fixed_reply(request, text=FIXED):
    return canonical({"units": [{"id": u["id"], "text": u["text"]} for u in candidate_units(text)],
                      "resolutions": [{"id": bid, "action": "addressed", "candidate_ids": ["C02", "C03"],
                                       "reason": "本文に修正を記載したとの宣言。"} for bid in request.payload["input"]["assessment"]["blocker_ids"]]})


class TextConductorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "source.sqlite"

    def tearDown(self):
        self.tmp.cleanup()

    def test_real_routing_repair_rechecks_original_before_releasing(self):
        calls = []
        def worker(request):
            calls.append(request)
            if request.payload["role"] == "repairer":
                return fixed_reply(request)
            self.assertEqual(set(request.payload["input"]), {"source", "candidate", "policy"})
            self.assertNotIn("critique", canonical(request.payload))
            return canonical(judgment(bad=request.payload["round"] == 0))
        with TextConductor(self.path, "episode") as conductor:
            conductor.begin(MEMO, DRAFT)
            report = conductor.run(worker)
            self.assertEqual([r.payload["role"] for r in calls], ["source_auditor", "repairer", "source_auditor"])
            self.assertEqual(report["state"], "released_candidate")
            self.assertEqual(report["calls_used"], 3)
            self.assertEqual(report["result_text"], FIXED)
            self.assertEqual(report["semantic_status"], "unassessed")
            self.assertEqual(calls[0].payload["input"]["source"], calls[2].payload["input"]["source"])
            self.assertNotEqual(calls[0].payload["input"]["candidate"]["ref"], calls[2].payload["input"]["candidate"]["ref"])
            self.assertEqual(conductor.memory.history("controller", "candidate_0")[0]["text"], DRAFT)
            trace = strict_json(conductor.memory.history("controller", "repair_trace_1")[0]["text"])
            self.assertTrue(trace["requires_source_recheck"])
            self.assertEqual(trace["effect_status"], "unassessed")

    def test_repair_declaration_cannot_bypass_second_check(self):
        def worker(request):
            return fixed_reply(request, DRAFT) if request.payload["role"] == "repairer" else canonical(judgment(True))
        with TextConductor(self.path, "episode") as conductor:
            conductor.begin(MEMO, DRAFT)
            report = conductor.run(worker)
            self.assertEqual(report["state"], "held")
            self.assertEqual(report["reason"], "repair_limit")
            self.assertEqual(report["calls_used"], 3)
            self.assertIsNone(report["result_text"])
            self.assertEqual(set(report["blocker_ids"]), {"M02", "M03", "C02", "C03"})

    def test_compatible_new_proposal_is_not_forbidden(self):
        response = judgment()
        response["candidates"].append({"id": "C05", "origin": "proposal", "source_ids": [],
                                        "compatibility": "compatible", "reason": "原条件に影響しない任意の補助。"})
        with TextConductor(self.path, "episode") as conductor:
            conductor.begin(MEMO, FIXED + "\n任意で好きな色を選ぶ。")
            report = conductor.run(lambda _: canonical(response))
            self.assertEqual(report["state"], "released_candidate")
            self.assertEqual(report["calls_used"], 1)

    def test_missing_duplicate_forged_and_incomplete_coverage_fail_closed(self):
        responses = []
        value = judgment(); value["sources"].pop(); responses.append(value)
        value = judgment(); value["candidates"].pop(); responses.append(value)
        value = judgment(); value["sources"][1] = value["sources"][0]; responses.append(value)
        value = judgment(); value["sources"][0]["candidate_ids"] = ["C99"]; responses.append(value)
        value = judgment(); value["candidates"][0]["source_ids"] = []; responses.append(value)
        value = judgment(); value["sources"][0]["status"] = "omitted"; responses.append(value)
        value = judgment(); value["verified"] = True; responses.append(value)
        for index, value in enumerate(responses):
            with self.subTest(index=index), TextConductor(Path(self.tmp.name) / f"bad{index}.sqlite", "episode") as conductor:
                conductor.begin(MEMO, DRAFT)
                report = conductor.run(lambda _: canonical(value))
                self.assertEqual(report["state"], "held")
                self.assertEqual(report["calls_used"], 1)
                self.assertEqual(report["reason"], "worker_failed:ContractError")
                self.assertNotIn("check_0", report["audit"]["slots_present"])

    def test_exploration_allows_only_explicitly_unprotected_source_changes(self):
        response = judgment()
        response["sources"][1].update(status="omitted", candidate_ids=[])
        preserve = SourcePolicy().freeze(MEMO)
        explore = SourcePolicy(mode="explore", protected_source_ids=("M01", "M03", "M04")).freeze(MEMO)
        self.assertIn("M02", assess(response, MEMO, FIXED, preserve)["blocker_ids"])
        self.assertEqual(assess(response, MEMO, FIXED, explore)["blocker_ids"], [])
        response["sources"][2].update(status="uncertain")
        self.assertIn("M03", assess(response, MEMO, FIXED, explore)["blocker_ids"])

    def test_unknown_origin_and_uncertain_compatibility_are_not_passes(self):
        value = judgment()
        value["candidates"][0]["origin"] = "unknown"
        value["candidates"][1]["compatibility"] = "uncertain"
        result = assess(value, MEMO, FIXED, SourcePolicy().freeze(MEMO))
        self.assertEqual(result["blocker_ids"], ["C01", "C02"])

    def test_reservation_survives_restart_and_cannot_silently_retry(self):
        with TextConductor(self.path, "episode") as conductor:
            conductor.begin(MEMO, DRAFT)
            request = conductor.request()
        with TextConductor(self.path, "episode") as resumed:
            self.assertEqual(resumed.report()["state"], "in_flight")
            with self.assertRaisesRegex(ContractError, "unresolved reservation"):
                resumed.run(lambda _: self.fail("No duplicate request allowed"))
            resumed.hold_pending()
            self.assertEqual(resumed.report()["calls_used"], 1)
            self.assertEqual(resumed.report()["state"], "held")
            with self.assertRaises(ContractError):
                resumed.submit(request, canonical(judgment()))

    def test_concurrent_controller_cannot_reserve_same_step(self):
        with TextConductor(self.path, "episode") as first:
            first.begin(MEMO, DRAFT)
            with TextConductor(self.path, "episode") as second:
                first.request()
                with self.assertRaises(ContractError):
                    second.request()
                self.assertEqual(second.report()["calls_used"], 1)

    def test_call_budget_and_zero_repairs_hold_without_extra_worker_calls(self):
        for limit in (SourcePolicy(max_calls=1), SourcePolicy(max_repairs=0)):
            with self.subTest(limit=limit), TextConductor(Path(self.tmp.name) / f"budget{limit.max_repairs}.sqlite", "episode", limit) as conductor:
                conductor.begin(MEMO, DRAFT)
                report = conductor.run(lambda _: canonical(judgment(True)))
                self.assertEqual(report["state"], "held")
                self.assertEqual(report["calls_used"], 1)

    def test_budget_cannot_release_a_repaired_candidate_without_recheck(self):
        def worker(request):
            return fixed_reply(request) if request.payload["role"] == "repairer" else canonical(judgment(True))
        with TextConductor(self.path, "episode", SourcePolicy(max_calls=2)) as conductor:
            conductor.begin(MEMO, DRAFT)
            report = conductor.run(worker)
            self.assertEqual(report["calls_used"], 2)
            self.assertEqual(report["state"], "held")
            self.assertEqual(report["reason"], "call_budget")
            self.assertEqual(report["candidate"]["id"], "candidate_1")
            self.assertIsNone(report["result_text"])

    def test_malformed_repair_publishes_neither_candidate_nor_trace(self):
        def worker(request):
            if request.payload["role"] != "repairer":
                return canonical(judgment(True))
            value = strict_json(fixed_reply(request))
            value["resolutions"][0]["candidate_ids"] = ["C99"]
            return canonical(value)
        with TextConductor(self.path, "episode") as conductor:
            conductor.begin(MEMO, DRAFT)
            report = conductor.run(worker)
            self.assertEqual(report["calls_used"], 2)
            self.assertEqual(report["state"], "held")
            self.assertTrue(report["blocker_ids"])
            self.assertNotIn("candidate_1", report["audit"]["slots_present"])
            self.assertNotIn("repair_trace_1", report["audit"]["slots_present"])

    def test_late_reply_and_interrupt_are_charged_without_admission(self):
        with TextConductor(self.path, "episode", SourcePolicy(call_timeout_s=1)) as conductor:
            conductor.begin(MEMO, FIXED)
            with patch("conductor.text_conductor.time.monotonic", side_effect=[0, 2]):
                report = conductor.run(lambda _: canonical(judgment()))
            self.assertEqual(report["reason"], "late_reply")
            self.assertEqual(report["calls_used"], 1)
            self.assertNotIn("check_0", report["audit"]["slots_present"])
        def interrupt(_):
            raise KeyboardInterrupt()
        with TextConductor(Path(self.tmp.name) / "interrupt.sqlite", "episode") as conductor:
            conductor.begin(MEMO, FIXED)
            with self.assertRaises(KeyboardInterrupt):
                conductor.run(interrupt)
            self.assertEqual(conductor.report()["state"], "held")
            self.assertEqual(conductor.report()["calls_used"], 1)

    def test_request_mutation_and_policy_downgrade_are_rejected(self):
        with TextConductor(self.path, "episode") as conductor:
            conductor.begin(MEMO, DRAFT)
            request = conductor.request()
            request.payload["input"]["policy"]["protected_source_ids"] = []
            with self.assertRaisesRegex(ContractError, "payload changed"):
                conductor.submit(request, canonical(judgment()))
        with self.assertRaisesRegex(ContractError, "policy cannot change"):
            TextConductor(self.path, "episode", SourcePolicy(mode="explore"))

    def test_source_update_invalidates_an_inflight_result(self):
        with TextConductor(self.path, "episode") as conductor:
            conductor.begin(MEMO, DRAFT)
            request = conductor.request()
            conductor.memory.commit("controller", [Publication("memo", "条件を変更した。")], expected_revision=request.expected_revision)
            with self.assertRaises(ContractError):
                conductor.submit(request, canonical(judgment()))
            self.assertNotIn("check_0", conductor.memory.audit()["slots_present"])

    def test_scope_excludes_upstream_critique_and_private_context(self):
        with TextConductor(self.path, "episode") as conductor:
            conductor.begin(MEMO, DRAFT, origin={"upstream_critique": "NOT_FOR_AUDITOR"})
            conductor.memory.broker.remember_private("controller", {"secret": "PRIVATE_CANARY"})
            request = conductor.request()
            self.assertNotIn("NOT_FOR_AUDITOR", canonical(request.payload))
            self.assertNotIn("PRIVATE_CANARY", canonical(request.payload))
            with self.assertRaises(ContractError):
                conductor.memory.snapshot("source_auditor", {"origin"})
            with self.assertRaises(ContractError):
                conductor.memory.commit("source_auditor", [Publication("outcome", "forged pass")], expected_revision=request.expected_revision)

    def test_schema_covers_exact_ids_and_repair_output_precedes_explanation(self):
        props = schema("source_auditor", MEMO, DRAFT)["properties"]
        self.assertEqual(props["sources"]["minItems"], 4)
        self.assertEqual(props["sources"]["items"]["properties"]["id"]["enum"], ["M01", "M02", "M03", "M04"])
        self.assertEqual(list(schema("repairer", MEMO, DRAFT, ["M02"])["properties"]), ["units", "resolutions"])


if __name__ == "__main__":
    unittest.main()
