# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 Ryo Higa / SpiralReality
"""Targeted return routes. A declared defect is not a proven defect.

Post-hoc observation is never accepted as generation feedback. The host must
explicitly propose a route, move to recompose, then reserve a single operation.
"""
from __future__ import annotations

from dataclasses import dataclass

from .breath import BreathController
from .duet import DuetRequest, DuetSession, nonempty, unique_refs
from .shared_memory import ArtifactRef
from .types import ContractError, canonical, exact_keys, strict_json

ROUTES = {
    "annotation_error": "repair_annotation",
    "source_conflict": "revise_source",
    "counterexample": "revise_example",
    "review_mismatch": "revise_review",
    "productive_difference": "fork",
}
# A corrected critique remains a critique. This does not grant reviewing status
# to arbitrary revise_* purposes or relax the actual-author/receipt binding.
REVIEW_PURPOSES = frozenset({"review", "critique", "source_audit", "revise_review"})


@dataclass(frozen=True)
class Finding:
    kind: str
    target: ArtifactRef
    evidence: tuple[ArtifactRef, ...]
    reason: str
    reported_by: str
    origin_lane: str = "intervention"


class RefluxController:
    def __init__(self, session: DuetSession):
        self.session = session
        self.breath = BreathController(session)

    def propose(self, finding: Finding, *, expected_revision: int) -> ArtifactRef:
        s = self.session
        s.check_revision(expected_revision)
        if self.breath.phase != "compare":
            raise ContractError("Reflux proposals require comparison phase")
        if not isinstance(finding, Finding) or not isinstance(finding.kind, str) or finding.kind not in ROUTES:
            raise ContractError("Unknown finding category")
        if finding.origin_lane != "intervention":
            raise ContractError("Post-hoc observations cannot feed generation")
        if finding.reported_by not in (*s.participants, "controller"):
            raise ContractError("Unregistered finding reporter")
        nonempty(finding.reason)
        evidence = unique_refs(finding.evidence)
        if not evidence:
            raise ContractError("A return route needs explicit public evidence")
        target = s.resolve(finding.target)
        if target["kind"] != "proposal" or target["author"] not in s.participants:
            raise ContractError("Return targets must be participant outputs")
        replies = [r for r in s.replies() if r["output"] == finding.target.to_dict()]
        if len(replies) != 1:
            raise ContractError("Target lacks a unique request receipt")
        if finding.kind == "review_mismatch" and replies[0]["purpose"] not in REVIEW_PURPOSES:
            raise ContractError("Review mismatch must target the actual reviewing role")
        data = {"kind": finding.kind, "target": finding.target.to_dict(),
                "evidence": [r.to_dict() for r in evidence], "reason": finding.reason,
                "reported_by": finding.reported_by, "origin_lane": finding.origin_lane,
                "recipient": target["author"], "action": ROUTES[finding.kind],
                "assessment_basis": "declared_finding_not_semantic_proof"}
        return s.record("reflux.plan.v1", data, refs=tuple(dict.fromkeys((finding.target, *evidence))),
                        expected_revision=expected_revision)

    def plan(self, ref: ArtifactRef) -> dict:
        document = self.session.resolve(ref)
        body = strict_json(document["text"])
        if not isinstance(body, dict) or document["author"] != "controller" or body.get("kind") != "reflux.plan.v1":
            raise ContractError("Not a host Reflux plan")
        return body["data"]

    def _requests(self, plan_ref: ArtifactRef) -> list[dict]:
        return [strict_json(a["text"]) | {"slot": a["ref"]["id"]}
                for a in self.session._family("request_")
                if strict_json(a["text"])["context"].get("reflux_plan") == plan_ref.to_dict()]

    def request(self, plan_ref: ArtifactRef, *, expected_revision: int) -> DuetRequest:
        s = self.session
        s.check_revision(expected_revision)
        plan = self.plan(plan_ref)
        if plan["action"] == "fork":
            raise ContractError("Productive difference requires a branch, not a repair call")
        if self.breath.phase != "recompose" or s.round_status()["state"] == "active":
            raise ContractError("Explicit recompose phase and completed/held duet required")
        if self._requests(plan_ref):
            raise ContractError("Plan already reserved; no implicit retry")
        target = ArtifactRef.from_dict(plan["target"])
        refs = tuple(dict.fromkeys((target, plan_ref, *(ArtifactRef.from_dict(r) for r in plan["evidence"]))))
        instruction = ("Respond only with annotation JSON {target: exact target reference, notes: nonempty string list}. "
                       "Do not replace or rewrite the target body."
                       if plan["action"] == "repair_annotation" else
                       "Reconsider only the identified public output in light of the declared finding. "
                       "Retain other alternatives; propose a new candidate, never certify the finding or the repair.")
        return s.reserve(plan["recipient"], plan["action"], instruction, refs=refs,
                         context={"reflux_plan": plan_ref.to_dict(), "target": plan["target"]},
                         expected_revision=expected_revision)

    def submit(self, request: DuetRequest, response: dict) -> ArtifactRef | None:
        exact_keys(response, {"text", "uses", "disposition"})
        # Validate binding before interpreting even a special annotation payload.
        meta, _ = self.session._validate_request(request)
        if "reflux_plan" not in meta["context"]:
            raise ContractError("Not a Reflux request")
        plan = self.plan(ArtifactRef.from_dict(meta["context"]["reflux_plan"]))
        if plan["action"] != meta["purpose"] or plan["recipient"] != request.worker:
            raise ContractError("Reflux target binding mismatch")
        if plan["action"] == "repair_annotation" and response.get("disposition") == "candidate":
            annotation = strict_json(response.get("text", ""))
            exact_keys(annotation, {"target", "notes"})
            if canonical(annotation["target"]) != canonical(plan["target"]):
                raise ContractError("Annotation changed its target")
            if not isinstance(annotation["notes"], list) or not annotation["notes"]:
                raise ContractError("Annotation needs notes")
            for note in annotation["notes"]:
                nonempty(note)
        return self.session.submit(request, response)

    def report(self, plan_ref: ArtifactRef) -> dict:
        plan = self.plan(plan_ref)
        requests = self._requests(plan_ref)
        replies = [r for r in self.session.replies() if r["context"].get("reflux_plan") == plan_ref.to_dict()]
        forked = any(n["data"].get("reflux_plan") == plan_ref.to_dict()
                     for n in self.session.notes("narrative.branch.v1"))
        return {"plan": plan_ref.to_dict(), "action": plan["action"],
                "state": ("forked" if forked else replies[-1]["disposition"] if replies else "pending" if requests else "planned"),
                "candidate": replies[-1]["output"] if replies else None,
                "requires_recheck": bool(replies and replies[-1]["output"]),
                "semantic_status": "unassessed", "original_retained": True}
