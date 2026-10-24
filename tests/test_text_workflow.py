# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 Ryo Higa / SpiralReality
from pathlib import Path
import tempfile
import unittest

from conductor.shared_memory import Publication
from conductor.text_workflow import (abstract_teacher, accept, model_input, open_text_memory,
                                     seed, segments, view)
from conductor.types import ContractError, canonical, strict_json


def review_fixture():
    return {"findings": [{
        "id": f"F{i:02d}", "principle_id": p["id"], "source_ids": ["M01"], "draft_ids": ["D01"],
        "elements": [{"polarity": "positive", "change": "null", "description": "保持。"},
                     {"polarity": "negative", "change": "+", "description": "仮の懸念。"}],
        "recommendation": "原条件を確認する。"} for i, p in enumerate(abstract_teacher()["principles"], 1)]}


def revision_fixture():
    return {"sentences": [{"id": "R01", "text": "原条件を保った案。"}, {"id": "R02", "text": "未解決の点を保留する。"}],
            "decisions": [{"finding_id": f"F{i:02d}", "disposition": ("hold" if i == 6 else "adopt"),
                           "reason": "本文に示した。", "output_ids": ([] if i == 6 else ["R01"])} for i in range(1, 7)]}


class TextWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "memory.sqlite"
        self.memory = open_text_memory(self.path, "episode")
        seed(self.memory, "原条件。\n未確定の事項がある。")
        accept(self.memory, "proposer", canonical({"text": "初稿。未確認の仮定。"}), expected_revision=1)

    def tearDown(self):
        self.memory.close()
        self.tmp.cleanup()

    def review(self):
        return accept(self.memory, "critic", canonical(review_fixture()), expected_revision=2)

    def test_full_handoff_reopen_trace_and_exact_source_output_spans(self):
        self.review()
        # Reopen between roles: persistence actually supplies the next handoff.
        with open_text_memory(self.path, "episode") as reviser:
            packet = model_input(view(reviser, "reviser"))
            self.assertIn("review", packet["documents"])
            publications = accept(reviser, "reviser", canonical(revision_fixture()), expected_revision=3)
        trace = strict_json(publications[1]["text"])
        self.assertEqual(trace["held_finding_ids"], ["F06"])
        self.assertEqual(len(trace["decisions"]), 6)
        for d in trace["decisions"]:
            self.assertEqual(d["effect_status"], "unassessed")
            for span in d["source_spans"] + d["draft_spans"] + d["output_spans"]:
                artifact = self.memory.history("controller", span["artifact"]["id"])[span["artifact"]["version"] - 1]
                self.assertEqual(artifact["text"][span["start"]:span["end"]], span["quote"])
                self.assertEqual(artifact["ref"], span["artifact"])
        self.assertEqual(self.memory.audit()["event_count"], 4)

    def test_vectors_are_derived_from_elements_not_model_authority(self):
        review = strict_json(self.review()[0]["text"])
        self.assertEqual(review["findings"][0]["vector"], [0, 1, 0, 1, 0, 0, 0, 0, 0])
        self.assertEqual(review["semantic_status"], "unassessed")

    def test_unknown_sentence_or_principle_and_model_verified_flag_rejected(self):
        mutations = []
        value = review_fixture(); value["findings"][0]["source_ids"] = ["M99"]; mutations.append(value)
        value = review_fixture(); value["findings"][0]["draft_ids"] = ["D99"]; mutations.append(value)
        value = review_fixture(); value["findings"][0]["principle_id"] = "G99"; mutations.append(value)
        value = review_fixture(); value["verified"] = True; mutations.append(value)
        value = review_fixture(); value["findings"][0]["vector"] = [99] * 9; mutations.append(value)
        for value in mutations:
            with self.subTest(value=value), self.assertRaises(ContractError):
                accept(self.memory, "critic", canonical(value), expected_revision=2)
        self.assertEqual(self.memory.audit()["event_count"], 2)

    def test_absence_does_not_need_a_fabricated_draft_sentence(self):
        value = review_fixture()
        value["findings"][0]["draft_ids"] = []
        value["findings"][0]["elements"] = [{"polarity": "positive", "change": "-", "description": "欠落。"}]
        result = accept(self.memory, "critic", canonical(value), expected_revision=2)
        self.assertEqual(strict_json(result[0]["text"])["findings"][0]["draft_spans"], [])

    def test_missing_duplicate_and_unlinked_adoption_fail_without_partial_revision(self):
        self.review()
        mutations = []
        value = revision_fixture(); value["decisions"].pop(); mutations.append(value)
        value = revision_fixture(); value["decisions"][1]["finding_id"] = "F01"; mutations.append(value)
        value = revision_fixture(); value["decisions"][0]["output_ids"] = []; mutations.append(value)
        value = revision_fixture(); value["decisions"][0]["output_ids"] = ["R99"]; mutations.append(value)
        for value in mutations:
            with self.subTest(value=value), self.assertRaises(ContractError):
                accept(self.memory, "reviser", canonical(value), expected_revision=3)
        self.assertEqual(self.memory.history("reviser", "revision"), [])
        self.assertEqual(self.memory.history("reviser", "trace"), [])

    def test_reject_and_hold_keep_reason_without_false_success(self):
        self.review()
        value = revision_fixture()
        value["decisions"][0].update(disposition="reject", output_ids=[], reason="根拠が足りないため採用しない。")
        trace = strict_json(accept(self.memory, "reviser", canonical(value), expected_revision=3)[1]["text"])
        self.assertEqual(trace["decisions"][0]["disposition"], "reject")
        self.assertEqual(trace["decisions"][0]["effect_status"], "unassessed")
        self.assertEqual(trace["decisions"][-1]["disposition"], "hold")

    def test_source_change_after_dispatch_rejects_old_reply(self):
        before = view(self.memory, "critic")
        self.memory.commit("controller", [Publication("memo", "条件を変更した。")], expected_revision=2)
        with self.assertRaises(ContractError):
            accept(self.memory, "critic", canonical(review_fixture()), expected_revision=before["revision"])
        self.assertEqual(self.memory.history("critic", "review"), [])

    def test_abstract_teacher_has_no_case_literals_or_old_example_sentences(self):
        teacher = canonical(abstract_teacher())
        for literal in ("多声の創作会", "90分", "最大12", "最大10", "5,000", "日本語要約", "木曜", "金曜",
                        "道具棚", "45分", "2,400", "最大7", "水曜", "土曜"):
            self.assertNotIn(literal, teacher)
        other = abstract_teacher()
        other["principles"][0]["good"] = "caller mutation"
        self.assertNotEqual(other, abstract_teacher())

    def test_sentence_offsets_preserve_wrapped_source_and_ids(self):
        source = "第一文。\n改行を\n含む第二文。"
        rows = segments(source, "M")
        self.assertEqual(len(rows), 2)
        for row in rows:
            self.assertEqual(source[row["start"]:row["end"]], row["text"])


if __name__ == "__main__":
    unittest.main()
