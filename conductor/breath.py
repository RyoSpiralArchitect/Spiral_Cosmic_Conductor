# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 Ryo Higa / SpiralReality
"""Explicit, replayable thinking phases; never a physiological/quality metric."""
from __future__ import annotations

from .duet import DuetSession, nonempty, unique_refs
from .shared_memory import ArtifactRef
from .types import ContractError, strict_json

PHASES = frozenset({"receive", "expand", "compare", "recompose", "yield", "hold"})
TRANSITIONS = {
    ("receive", "start"): "expand",
    ("expand", "candidates"): "compare",
    ("recompose", "candidates"): "compare",
    ("recompose", "recheck"): "compare",
    ("compare", "counterexample"): "expand",
    ("compare", "recompose"): "recompose",
    ("compare", "yield"): "yield",
    ("hold", "resume_expand"): "expand",
    ("hold", "resume_compare"): "compare",
}
for _phase in PHASES - {"yield", "hold"}:
    TRANSITIONS[(_phase, "hold")] = "hold"


class BreathController:
    """Host commands move phases; model text cannot trigger a transition.

    Yield means return unassessed candidates to the caller, not source fidelity,
    consensus, or verified truth. Existing TextConductor release rules still
    apply when the caller needs source checking. No generation loop is provided.
    """

    def __init__(self, session: DuetSession):
        self.session = session

    @property
    def phase(self) -> str:
        phase = "receive"
        for note in self.session.notes("breath.transition.v1"):
            data = note["data"]
            if data["from"] != phase or TRANSITIONS.get((phase, data["event"])) != data["to"]:
                raise ContractError("Invalid persisted phase transition")
            phase = data["to"]
        return phase

    def _current_targeted_outputs(self) -> set[ArtifactRef]:
        """Outputs reserved after entering this recomposition, not older work."""
        s = self.session
        notes = s.notes("breath.transition.v1")
        if not notes or notes[-1]["data"]["to"] != "recompose":
            return set()
        boundary = notes[-1]["ref"]
        seq = next((event["seq"] for event in s.memory.events()
                    if any(a["ref"] == boundary for a in event["artifacts"])), None)
        if seq is None:
            raise ContractError("Missing recomposition boundary")
        requests = {a["ref"]["id"]: strict_json(a["text"]) for a in s._family("request_")}
        return {ArtifactRef.from_dict(reply["output"]) for reply in s.replies()
                if reply["output"] is not None and "duet_round" not in reply["context"]
                and requests[reply["request"]]["expected_revision"] > seq}

    def signal(self, event: str, reason: str, *, refs=(), expected_revision: int) -> ArtifactRef:
        s = self.session
        s.check_revision(expected_revision)
        nonempty(reason)
        phase = self.phase
        if not isinstance(event, str):
            raise ContractError("Invalid Breath event")
        target = TRANSITIONS.get((phase, event))
        if target is None:
            raise ContractError("Illegal Breath transition")
        refs = unique_refs(refs)
        for ref in refs:
            s.resolve(ref)
        if event in {"candidates", "counterexample", "recompose", "yield", "resume_compare", "recheck"} and not refs:
            raise ContractError("Content-dependent transition requires public evidence")
        status = s.round_status()
        # An idle gap between two turns is not a completed round. Hold may retain
        # that unfinished round; resuming it must return to generation, not skip it.
        if status["state"] == "active" and target not in {"expand", "hold"}:
            raise ContractError("Complete or explicitly hold the active duet before leaving generation")
        round_ref = None
        if event == "candidates":
            if status["state"] != "complete":
                raise ContractError("Both candidates required before comparison")
            round_ref = status["round"]["ref"]
            required = {ArtifactRef.from_dict(r["output"]) for r in status["replies"]}
            if not required <= set(refs):
                raise ContractError("Comparison must retain both candidates")
            consumed = [n["data"].get("round") for n in s.notes("breath.transition.v1")]
            if round_ref in consumed:
                raise ContractError("Round already compared; explicitly open a new round")
        if event in {"yield", "resume_compare", "recheck"}:
            if not any(s.resolve(ref)["kind"] == "proposal" for ref in refs):
                raise ContractError("Comparison/yield must identify a candidate")
        if event == "recheck" and not (set(refs) & self._current_targeted_outputs()):
            raise ContractError("Recheck requires a targeted candidate reserved in this recomposition")
        if event == "resume_compare":
            transitions = s.notes("breath.transition.v1")
            if not any(n["data"]["to"] == "compare" for n in transitions):
                raise ContractError("No prior comparison to resume")
        notes = s.notes("breath.transition.v1")
        parents = list(refs)
        if notes:
            parents.append(ArtifactRef.from_dict(notes[-1]["ref"]))
        if round_ref:
            parents.append(ArtifactRef.from_dict(round_ref))
        return s.record("breath.transition.v1", {"from": phase, "to": target, "event": event,
                        "reason": reason, "round": round_ref,
                        "result_status": "yielded_unassessed" if target == "yield" else "in_progress"},
                        refs=tuple(dict.fromkeys(parents)), expected_revision=expected_revision)

    def open_round(self, mode: str, purpose: str, reason: str, *, refs=(), expected_revision: int) -> ArtifactRef:
        self.session.check_revision(expected_revision)
        if self.phase not in {"expand", "recompose"}:
            raise ContractError("Rounds require expand/recompose phase")
        return self.session.start_round(mode, purpose, reason, refs=refs, expected_revision=expected_revision)

    def request(self):
        if self.phase not in {"expand", "recompose"}:
            raise ContractError("Generation is not enabled in this phase")
        return self.session.request()

    def report(self) -> dict:
        return {"schema": "breath.v1", "phase": self.phase,
                "revision": self.session.revision, "semantic_status": "unassessed",
                "transitions": self.session.notes("breath.transition.v1")}
