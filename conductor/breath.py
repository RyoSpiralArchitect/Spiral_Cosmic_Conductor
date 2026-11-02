# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 Ryo Higa / SpiralReality
"""Explicit, replayable thinking phases; never a physiological/quality metric."""
from __future__ import annotations

from .duet import DuetSession, nonempty, unique_refs
from .shared_memory import ArtifactRef
from .types import ContractError

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
        round_ref = None
        if event == "candidates":
            status = s.round_status()
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
