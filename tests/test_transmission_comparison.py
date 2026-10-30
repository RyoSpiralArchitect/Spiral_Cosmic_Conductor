# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 Ryo Higa / SpiralReality
import contextlib
from dataclasses import asdict
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from conductor.multipolar import Agent, MultipolarBroker
from conductor.shared_memory import MemoryLimits, Publication, SharedMemory
from conductor.source_alignment import SourceLimits, SourcePolicy, assess, candidate_units, source_units
from conductor.text_conductor import TextConductor
from conductor.text_workflow import TextLimits, accept, open_text_memory, response_schema, seed, view
from conductor.types import ContractError, canonical
from experiments.transmission_comparison import DEFAULT_CONFIG, run, simulate


class OpenTransmissionTests(unittest.TestCase):
    def test_long_reasons_many_units_and_references_are_preserved(self):
        memo = "".join(f"条件{i}を保つ。" for i in range(40))
        candidate = "\n".join(f"条件{i}を保つ。" for i in range(40))
        mids = [u["id"] for u in source_units(memo, SourceLimits.unbounded())]
        cids = [u["id"] for u in candidate_units(candidate, SourceLimits.unbounded())]
        reply = {"sources": [{"id": mid, "status": "preserved", "candidate_ids": cids, "reason": "長い根拠。" * 100} for mid in mids],
                 "candidates": [{"id": cid, "origin": "source", "source_ids": mids,
                                  "compatibility": "compatible", "reason": "長い根拠。" * 100} for cid in cids]}
        with tempfile.TemporaryDirectory() as tmp:
            with TextConductor(Path(tmp) / "memory.sqlite", "open", SourcePolicy.unbounded(), MemoryLimits.unbounded()) as controller:
                controller.begin(memo, candidate)
                report = controller.run(lambda _: canonical(reply))
                self.assertEqual(report["state"], "released_candidate")
                saved = json.loads(controller.memory.history("controller", "check_0")[-1]["text"])
                self.assertEqual(saved["sources"], reply["sources"])
                self.assertEqual(saved["candidates"], reply["candidates"])
                reply["sources"][0]["candidate_ids"].append("C999")
                with self.assertRaises(ContractError):
                    assess(reply, memo, candidate, SourcePolicy.unbounded().freeze(memo))

    def test_rounds_are_not_preallocated_or_limited_to_old_two_repairs(self):
        policy = SourcePolicy.unbounded()
        def worker(request):
            p = request.payload
            if p["role"] == "repairer":
                return canonical({"units": [{"id": "C01", "text": f"改稿{p['round']}。"}],
                                  "resolutions": [{"id": "M01", "action": "addressed", "candidate_ids": ["C01"], "reason": "変更。"}]})
            return canonical({"sources": [{"id": "M01", "status": "preserved" if p["round"] == 3 else "changed", "candidate_ids": ["C01"], "reason": "比較。"}],
                              "candidates": [{"id": "C01", "origin": "proposal", "source_ids": [], "compatibility": "compatible", "reason": "追加。"}]})
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "memory.sqlite"
            with TextConductor(path, "open", policy, MemoryLimits.unbounded()) as controller:
                controller.begin("原条件。", "初稿。")
                result = controller.run(worker)
                self.assertEqual(result["calls_used"], 7)
                self.assertEqual(result["state"], "released_candidate")
                self.assertEqual(result["round"], 3)
                with self.assertRaises(ContractError):
                    controller.memory.commit("repairer", [Publication("candidate_0", "改竄")], expected_revision=result["audit"]["event_count"])
            with TextConductor(path, "open", policy, MemoryLimits.unbounded()) as reopened:
                self.assertEqual(reopened.report(), result)

    def test_unlimited_repair_holds_a_rechecked_candidate_cycle(self):
        def worker(request):
            p = request.payload
            if p["role"] == "repairer":
                return canonical({"units": [{"id": "C01", "text": "同じ候補。"}],
                                  "resolutions": [{"id": "M01", "action": "disputed", "candidate_ids": [], "reason": "異論。"}]})
            return canonical({"sources": [{"id": "M01", "status": "changed", "candidate_ids": ["C01"], "reason": "未解決。"}],
                              "candidates": [{"id": "C01", "origin": "proposal", "source_ids": [], "compatibility": "compatible", "reason": "追加。"}]})
        with tempfile.TemporaryDirectory() as tmp, TextConductor(Path(tmp) / "memory.sqlite", "open", SourcePolicy.unbounded(), MemoryLimits.unbounded()) as controller:
            controller.begin("原条件。", "同じ候補。")
            result = controller.run(worker)
            self.assertEqual((result["state"], result["reason"], result["calls_used"]), ("held", "candidate_cycle", 3))
            self.assertIn("check_1", result["audit"]["slots_present"])

    def test_unbounded_memory_exceeds_old_events_and_preserves_family_scopes(self):
        agent = Agent("writer", frozenset({"item_"}), frozenset({"item_"}))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "memory.sqlite"
            with SharedMemory(path, "many", MultipolarBroker([agent]), {"root": "source"}, MemoryLimits.unbounded(), families={"item_": "proposal"}) as memory:
                for i in range(65):
                    memory.commit("writer", [Publication(f"item_{i}", "保存する本文。" * 2400)], expected_revision=i)
                self.assertEqual(memory.audit()["event_count"], 65)
                self.assertGreater(len(memory.history("writer", "item_64")[0]["text"]), 16_000)
                for name in ("root", "item_00", "item_other"):
                    with self.assertRaises(ContractError):
                        memory.commit("writer", [Publication(name, "許可外")], expected_revision=65)

    def test_old_overflow_capture_is_accepted_only_with_explicit_open_profile(self):
        fixture = json.loads((Path(__file__).parent / "fixtures/text_memory_reference_overflow_2026-10-25.json").read_text())
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "text.sqlite"
            with open_text_memory(path, fixture["episode"], MemoryLimits.unbounded(), text_limits=TextLimits.unbounded()) as memory:
                seed(memory, fixture["memo"])
                accept(memory, "proposer", fixture["proposal_content"], expected_revision=1)
                result = accept(memory, "critic", fixture["review_content"], expected_revision=2)
                saved = json.loads(result[0]["text"])
                self.assertEqual(max(len(f["draft_ids"]) for f in saved["findings"]), 19)
                schema = response_schema("reviser", view(memory, "reviser"))["properties"]
                self.assertNotIn("maxItems", schema["sentences"])
                self.assertEqual(schema["decisions"]["items"]["properties"]["output_ids"]["items"]["pattern"], "^R[0-9]+$")
            with self.assertRaises(ContractError):
                open_text_memory(path, fixture["episode"], MemoryLimits.unbounded(), text_limits=TextLimits())

    def test_same_route_pair_replays_without_network_or_provider_caps(self):
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()), patch("experiments.provider_pilot.ProviderWorker._transport", side_effect=AssertionError("No network")):
            root = Path(tmp)
            results = run(root / "sim")
            replay = run(root / "replay", "replay", replay=root / "sim")
            self.assertEqual(results, replay)
            rows = [json.loads(line) for line in (root / "sim/exchanges.jsonl").read_text().splitlines()]
            paths = [[r["stage"] for r in rows if r["case"] == result["id"]] for result in results]
            self.assertEqual(paths, [["proposer", "critic", "reviser", "source_auditor"]] * 2)
            for request in (root / "sim").glob("exchange_*.request.json"):
                payload = json.loads(request.read_text())
                self.assertNotIn("max_tokens", payload)
                self.assertNotIn("max_completion_tokens", payload)
            for result in results:
                self.assertTrue(result["observation"]["spans_resolved"])
                self.assertEqual(result["report"]["state"], "released_candidate")

    def test_transport_failure_preserves_exact_replay(self):
        seen = []
        def transport(case, stage, packet, payload):
            seen.append((case, stage))
            if case == "faithful" and stage == "critic":
                raise TimeoutError("NEVER_STORE")
            return simulate(stage, packet)
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()):
            root = Path(tmp)
            result = run(root / "sim", transport=transport)
            self.assertEqual(result[0]["status"], "failed")
            self.assertNotIn(("faithful", "reviser"), seen)
            self.assertEqual(result[1]["status"], "observed")
            self.assertNotIn("NEVER_STORE", (root / "sim/exchanges.jsonl").read_text())
            self.assertEqual(result, run(root / "replay", "replay", replay=root / "sim"))

    def test_configured_timeout_hold_replays_without_waiting(self):
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()):
            root = Path(tmp)
            config = json.loads(DEFAULT_CONFIG.read_text())
            config["source_policy"]["call_timeout_s"] = 1
            (root / "config.json").write_text(canonical(config))
            with patch("experiments.transmission_comparison.time.monotonic", side_effect=[0, 2, 3, 3.5]):
                result = run(root / "sim", config_path=root / "config.json")
            self.assertEqual(result[0]["report"]["reason"], "late_reply")
            self.assertEqual(result[1]["report"]["state"], "released_candidate")
            self.assertEqual(result, run(root / "replay", "replay", replay=root / "sim"))


if __name__ == "__main__":
    unittest.main()
