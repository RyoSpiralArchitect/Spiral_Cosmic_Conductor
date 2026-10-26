# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 Ryo Higa / SpiralReality
from copy import deepcopy
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from conductor.emergence import accept_continuation, assess_observation, packet, units
from conductor.types import ContractError, canonical
from experiments.emergence_probe import boundary, run, simulated_transport


class EmergenceProbeTests(unittest.TestCase):
    def test_prefix_is_frozen_and_future_and_invisible_references_are_rejected(self):
        prior = units("source", "原文の条件。")
        invisible = units("earlier", "生成者には渡していない既出の案。")
        target = units("revision", "新しい関係。")
        history = prior + invisible
        queue = []
        boundary(queue, "a", "revision", history, prior, target, [prior[0]["id"]])
        history.extend(units("future", "後の文章。"))
        p = queue[0]["input"]
        self.assertNotIn("future", canonical(p))
        response = {"outputs": [{"output_id": target[0]["id"], "relation": "elaborated", "novelty": "relation",
                                 "antecedent_ids": [prior[0]["id"]], "input_ids": [prior[0]["id"]],
                                 "idea": "関係の候補。", "reason": "観測の説明。"}],
                    "fates": [{"prior_id": prior[0]["id"], "use": "developed", "output_ids": [target[0]["id"]], "reason": "対応。"}]}
        self.assertEqual(assess_observation(response, p)["semantic_status"], "unassessed")
        bad = deepcopy(response)
        bad["outputs"][0]["input_ids"] = [invisible[0]["id"]]
        with self.assertRaises(ContractError): assess_observation(bad, p)
        bad = deepcopy(response)
        bad["outputs"][0]["antecedent_ids"] = ["future_U001"]
        with self.assertRaises(ContractError): assess_observation(bad, p)
        bad = deepcopy(response)
        bad["fates"][0]["use"] = "not_found"
        with self.assertRaises(ContractError): assess_observation(bad, p)
        bad = deepcopy(response)
        bad["outputs"] = []
        with self.assertRaises(ContractError): assess_observation(bad, p)

    def test_continuation_references_actual_outputs_and_covers_incoming(self):
        incoming = units("incoming", "入力。\n次の条件。", split="lines")
        response = {"sentences": [{"id": "N01", "text": "次の場面。"}],
                    "links": [{"input_id": u["id"], "use": "developed", "output_ids": ["N01"], "reason": "展開。"} for u in incoming]}
        self.assertEqual(accept_continuation(response, incoming), "次の場面。")
        for mutate in (lambda r: r["links"].pop(), lambda r: r["links"][0].update(output_ids=["N99"]),
                       lambda r: r["sentences"][0].update(id="N02")):
            bad = deepcopy(response)
            mutate(bad)
            with self.assertRaises(ContractError): accept_continuation(bad, incoming)

    def test_shared_draft_only_guidance_differs_and_observers_do_not_feed_generators(self):
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()), patch("experiments.provider_pilot.ProviderWorker._transport", side_effect=AssertionError("No network")):
            root = Path(tmp)
            result = run(root / "sim")
            self.assertEqual(result, run(root / "replay", "replay", replay=root / "sim"))
            arms = result["arms"]
            self.assertEqual([a["status"] for a in arms], ["observed", "observed"])
            self.assertEqual(arms[0]["imported_request_sha256"], arms[1]["imported_request_sha256"])
            self.assertEqual(arms[0]["imported_draft_sha256"], arms[1]["imported_draft_sha256"])
            rows = [json.loads(line) for line in (root / "sim/exchanges.jsonl").read_text().splitlines()]
            first_observer = next(r["index"] for r in rows if r["stage"] == "observer")
            self.assertTrue(all(r["stage"] == "observer" for r in rows[first_observer:]))
            critic_inputs = []
            for row in rows:
                payload = json.loads((root / "sim" / f"exchange_{row['index']:04d}.request.json").read_text())
                self.assertNotIn("max_tokens", payload)
                self.assertNotIn("max_completion_tokens", payload)
                p = json.loads(payload["messages"][1]["content"])
                if row["stage"] == "critic":
                    p["documents"].pop("review_guidance")
                    critic_inputs.append(p)
                if row["stage"] == "continuator":
                    self.assertEqual(set(p), {"source", "task_brief", "incoming"})
            self.assertEqual(*critic_inputs)
            boundaries = json.loads((root / "sim/boundaries.json").read_text())
            self.assertEqual(sum(b["arm"] == "shared" for b in boundaries), 1)
            for b in boundaries:
                if b["input"]["stage"] == "draft":
                    self.assertNotIn("review_guidance", canonical(b))
                    self.assertNotIn("continuation_U", canonical(b))
                if b["input"]["stage"] == "review":
                    self.assertNotIn("revision_U", canonical(b))

    def test_observer_failure_cannot_change_completed_generations_and_replays(self):
        def transport(case, stage, p, payload):
            if stage == "observer":
                raise TimeoutError("NEVER_PERSIST_THIS")
            return simulated_transport(case, stage, p, payload)
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()):
            root = Path(tmp)
            result = run(root / "sim", transport=transport)
            self.assertTrue(all(a["status"] == "observed" for a in result["arms"]))
            self.assertTrue(all(o["status"] == "failed" for o in result["observations"]))
            self.assertEqual(result, run(root / "replay", "replay", replay=root / "sim"))
            self.assertNotIn("NEVER_PERSIST_THIS", (root / "sim/exchanges.jsonl").read_text())

    def test_source_hold_prevents_continuation_and_preserves_repair_boundaries(self):
        seen = []
        def transport(case, stage, p, payload):
            seen.append(stage)
            if stage == "source_auditor":
                result = json.loads(simulated_transport(case, stage, p, payload))
                content = json.loads(result["choices"][0]["message"]["content"])
                content["candidates"][0]["compatibility"] = "uncertain"
            elif stage == "repairer":
                content = {"units": [{"id": u["id"], "text": u["text"]} for u in p["candidate"]["units"]],
                           "resolutions": [{"id": bid, "action": "disputed", "candidate_ids": [], "reason": "保留。"} for bid in p["assessment"]["blocker_ids"]]}
                result = {"model": "SIMULATED", "choices": [{"finish_reason": "stop", "message": {}}], "usage": {}}
            else:
                return simulated_transport(case, stage, p, payload)
            result["choices"][0]["message"]["content"] = canonical(content)
            return canonical(result).encode()
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()):
            root = Path(tmp)
            result = run(root / "sim", transport=transport)
            self.assertTrue(all(a["status"] == "held" for a in result["arms"]))
            self.assertTrue(all(a["source_report"]["reason"] == "candidate_cycle" for a in result["arms"]))
            self.assertNotIn("continuator", seen)
            self.assertEqual(sum(o["stage"] == "candidate_1" for o in result["observations"]), 2)
            self.assertEqual(result, run(root / "replay", "replay", replay=root / "sim"))


if __name__ == "__main__":
    unittest.main()
