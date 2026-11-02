# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 Ryo Higa / SpiralReality
"""Append-only public decision branches, not a new truth store or private memory.

Fork/rewind/merge preserve their source history. Search is lexical. Conditions
are evidence-linked host assessments; satisfying one only suggests reconsidering
an old node and never promotes it to verified truth or automatically executes it.
"""
from __future__ import annotations

import re

from .breath import BreathController
from .duet import MODES, DuetSession, nonempty, unique_refs
from .reflux import RefluxController
from .shared_memory import ArtifactRef
from .types import ContractError, check_id, strict_json

NODE_KINDS = frozenset({"question", "hypothesis", "evidence", "counterexample", "decision"})
DISPOSITIONS = frozenset({"proposed", "adopted", "rejected", "held"})


class BranchMemory:
    def __init__(self, session: DuetSession):
        self.session = session

    def _read(self, ref: ArtifactRef, kind: str) -> dict:
        document = self.session.resolve(ref)
        value = strict_json(document["text"])
        if not isinstance(value, dict) or document["author"] != "controller" or value.get("kind") != kind:
            raise ContractError("Wrong narrative record kind")
        return value["data"]

    def node(self, ref: ArtifactRef) -> dict:
        return self._read(ref, "narrative.node.v1")

    def head(self, branch: str) -> dict:
        check_id(branch)
        records = [n for n in self.session.notes("narrative.branch.v1") if n["data"]["branch"] == branch]
        if not records:
            raise ContractError("Unknown branch")
        return records[-1]

    def remember(self, kind: str, summary: str, rationale: str, *, evidence=(),
                 disposition: str = "proposed", reopen_on: tuple[str, ...] = (),
                 expected_revision: int) -> ArtifactRef:
        self.session.check_revision(expected_revision)
        if not isinstance(kind, str) or not isinstance(disposition, str) or kind not in NODE_KINDS or disposition not in DISPOSITIONS:
            raise ContractError("Invalid narrative node kind/disposition")
        nonempty(summary)
        nonempty(rationale)
        if (not isinstance(reopen_on, tuple) or len(set(reopen_on)) != len(reopen_on)):
            raise ContractError("Reopen conditions must be distinct identifiers")
        for condition in reopen_on:
            check_id(condition)
        evidence = unique_refs(evidence)
        if not evidence:
            raise ContractError("Narrative nodes need actual public source/output evidence")
        for ref in evidence:
            doc = self.session.resolve(ref)
            if doc["kind"] not in {"source", "proposal"}:
                raise ContractError("A summary or control record is not primary content evidence")
        return self.session.record("narrative.node.v1", {"node_kind": kind, "summary": summary,
                                   "rationale": rationale, "evidence": [r.to_dict() for r in evidence],
                                   "disposition": disposition, "reopen_on": list(reopen_on),
                                   "assessment_basis": "host_assessment_not_truth"},
                                   refs=evidence, expected_revision=expected_revision)

    def _write_head(self, branch: str, operation: str, nodes, reason: str, *, previous=None,
                    sources=(), extra=None, expected_revision: int) -> ArtifactRef:
        self.session.check_revision(expected_revision)
        check_id(branch)
        nonempty(reason)
        nodes = unique_refs(nodes)
        sources = unique_refs(sources)
        for node in nodes:
            self.node(node)
        parents = tuple(dict.fromkeys((*nodes, *sources, *((previous,) if previous else ()))))
        body = {"branch": branch, "operation": operation, "nodes": [n.to_dict() for n in nodes],
                "reason": reason, "previous": previous.to_dict() if previous else None,
                "sources": [r.to_dict() for r in sources], "merge_semantics": "retain_not_consensus"}
        if extra:
            body.update(extra)
        return self.session.record("narrative.branch.v1", body, refs=parents, expected_revision=expected_revision)

    def create(self, branch: str, reason: str, *, expected_revision: int) -> ArtifactRef:
        if any(n["data"]["branch"] == branch for n in self.session.notes("narrative.branch.v1")):
            raise ContractError("Branch already exists")
        return self._write_head(branch, "create", (), reason, expected_revision=expected_revision)

    def attach(self, branch: str, node: ArtifactRef, reason: str, *, expected_revision: int) -> ArtifactRef:
        head = self.head(branch)
        nodes = tuple(ArtifactRef.from_dict(n) for n in head["data"]["nodes"])
        if node in nodes:
            raise ContractError("Node already in branch")
        return self._write_head(branch, "attach", (*nodes, node), reason,
                                previous=ArtifactRef.from_dict(head["ref"]), expected_revision=expected_revision)

    def fork(self, parent: str, branch: str, reason: str, *, expected_revision: int) -> ArtifactRef:
        if any(n["data"]["branch"] == branch for n in self.session.notes("narrative.branch.v1")):
            raise ContractError("Branch already exists")
        source = self.head(parent)
        return self._write_head(branch, "fork", tuple(ArtifactRef.from_dict(n) for n in source["data"]["nodes"]),
                                reason, sources=(ArtifactRef.from_dict(source["ref"]),), expected_revision=expected_revision)

    def rewind(self, branch: str, historical_head: ArtifactRef, reason: str, *, expected_revision: int) -> ArtifactRef:
        old = self._read(historical_head, "narrative.branch.v1")
        if old["branch"] != branch:
            raise ContractError("Rewind target belongs to another branch")
        head = self.head(branch)
        cursor = ArtifactRef.from_dict(head["ref"])
        while cursor != historical_head:
            previous = self._read(cursor, "narrative.branch.v1")["previous"]
            if previous is None:
                raise ContractError("Rewind target is not an ancestor")
            cursor = ArtifactRef.from_dict(previous)
        return self._write_head(branch, "rewind", tuple(ArtifactRef.from_dict(n) for n in old["nodes"]), reason,
                                previous=ArtifactRef.from_dict(head["ref"]), sources=(historical_head,),
                                expected_revision=expected_revision)

    def merge(self, target: str, sources: tuple[str, ...], reason: str, *, expected_revision: int) -> ArtifactRef:
        if not sources or len(set(sources)) != len(sources) or target in sources:
            raise ContractError("Choose distinct other source branches")
        head, others = self.head(target), [self.head(name) for name in sources]
        nodes = tuple(dict.fromkeys(ArtifactRef.from_dict(n) for b in [head, *others] for n in b["data"]["nodes"]))
        return self._write_head(target, "merge", nodes, reason, previous=ArtifactRef.from_dict(head["ref"]),
                                sources=tuple(ArtifactRef.from_dict(b["ref"]) for b in others),
                                expected_revision=expected_revision)

    def condition(self, name: str, status: str, reason: str, *, evidence=(), expected_revision: int) -> ArtifactRef:
        check_id(name)
        if not isinstance(status, str) or status not in {"satisfied", "unsatisfied", "unknown"}:
            raise ContractError("Unknown condition status")
        nonempty(reason)
        evidence = unique_refs(evidence)
        if not evidence:
            raise ContractError("Condition assessment needs explicit evidence")
        for ref in evidence:
            if self.session.resolve(ref)["kind"] not in {"source", "proposal"}:
                raise ContractError("Condition requires source/output evidence")
        old = [n for n in self.session.notes("narrative.condition.v1") if n["data"]["name"] == name]
        parents = (*evidence, *((ArtifactRef.from_dict(old[-1]["ref"]),) if old else ()))
        return self.session.record("narrative.condition.v1", {"name": name, "status": status, "reason": reason,
                                   "evidence": [r.to_dict() for r in evidence], "assessment_basis": "host_assessment"},
                                   refs=parents, expected_revision=expected_revision)

    def reconsider(self, branch: str) -> list[dict]:
        latest = {n["data"]["name"]: n for n in self.session.notes("narrative.condition.v1")}
        result = []
        for raw in self.head(branch)["data"]["nodes"]:
            node = self.node(ArtifactRef.from_dict(raw))
            conditions = node["reopen_on"]
            if (node["disposition"] in {"held", "rejected"} and conditions
                    and all(c in latest and latest[c]["data"]["status"] == "satisfied" for c in conditions)):
                result.append({"node": raw, "conditions": [latest[c]["ref"] for c in conditions],
                               "status": "candidate_for_reconsideration", "semantic_status": "unassessed"})
        return result

    def search(self, branch: str, query: str, *, kinds=(), dispositions=()) -> list[dict]:
        """Untruncated lexical retrieval within one explicitly chosen branch."""
        nonempty(query)
        if not set(kinds) <= NODE_KINDS or not set(dispositions) <= DISPOSITIONS:
            raise ContractError("Unknown memory search filter")
        tokens = set(re.findall(r"\w+", query.casefold()))
        results = []
        for raw in self.head(branch)["data"]["nodes"]:
            node = self.node(ArtifactRef.from_dict(raw))
            if (kinds and node["node_kind"] not in kinds) or (dispositions and node["disposition"] not in dispositions):
                continue
            text = (node["summary"] + " " + node["rationale"]).casefold()
            score = sum(token in text for token in tokens)
            if score:
                results.append({"ref": raw, "node": node, "lexical_hits": score, "semantic_status": "unassessed"})
        return sorted(results, key=lambda r: (-r["lexical_hits"], r["ref"]["id"]))

    def fork_from_reflux(self, plan_ref: ArtifactRef, parent: str, branch: str, reason: str,
                         *, expected_revision: int) -> ArtifactRef:
        self.session.check_revision(expected_revision)
        plan = RefluxController(self.session).plan(plan_ref)
        if plan["action"] != "fork":
            raise ContractError("Reflux plan did not request a branch")
        if any(n["data"].get("reflux_plan") == plan_ref.to_dict() for n in self.session.notes("narrative.branch.v1")):
            raise ContractError("Fork plan already consumed")
        if any(n["data"]["branch"] == branch for n in self.session.notes("narrative.branch.v1")):
            raise ContractError("Branch already exists")
        head = self.head(parent)
        nodes = tuple(ArtifactRef.from_dict(n) for n in head["data"]["nodes"])
        if not any(plan["target"] in self.node(n)["evidence"] for n in nodes):
            raise ContractError("Reflux target is not in the parent branch")
        return self._write_head(branch, "fork", nodes, reason,
                                sources=(ArtifactRef.from_dict(head["ref"]), plan_ref),
                                extra={"reflux_plan": plan_ref.to_dict()}, expected_revision=expected_revision)

    def open_round(self, branch: str, selected: tuple[ArtifactRef, ...], mode: str, purpose: str,
                   reason: str, *, expected_revision: int) -> ArtifactRef:
        """Project selected nodes/content only, not the entire branch or private state."""
        self.session.check_revision(expected_revision)
        selected = unique_refs(selected)
        if not selected:
            raise ContractError("Choose explicit memory nodes")
        head = self.head(branch)
        allowed = {ArtifactRef.from_dict(n) for n in head["data"]["nodes"]}
        if not set(selected) <= allowed:
            raise ContractError("Memory selection crosses branch boundary")
        if mode not in MODES:
            raise ContractError("Unknown duet mode")
        refs = list(selected)
        for ref in selected:
            refs.extend(ArtifactRef.from_dict(r) for r in self.node(ref)["evidence"])
        return BreathController(self.session).open_round(mode, purpose, reason,
                                                         refs=tuple(dict.fromkeys(refs)),
                                                         expected_revision=expected_revision)
