# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 Ryo Higa / SpiralReality
"""Bounded, deterministic retrieval for supplied facts (ND-RAG adapter).

Lexical retrieval is NOT narrative semantic understanding, trust, or correctness.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any

from .types import ContractError, Task, canonical, digest


@dataclass(frozen=True)
class EvidenceHit:
    source_id: str
    score: int
    sha256: str
    title: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class EvidenceIndex:
    def __init__(self, task: Task):
        self.task = task
        self.records = {
            key: {"sha256": digest(value), "title": value["title"],
                  "tokens": self._tokens(value["title"] + " " + value["text"] + " "
                                         + " ".join(value["facts"].keys()))}
            for key, value in task.sources.items()
        }

    @staticmethod
    def _tokens(value: str) -> set[str]:
        return set(re.findall(r"[\w\u3040-\u30ff\u4e00-\u9fff]+", value.lower()))

    def search(self, query: str, *, authorized: set[str], limit: int = 4) -> list[EvidenceHit]:
        if not isinstance(query, str) or len(query) > 4000:
            raise ContractError("Invalid evidence query")
        if type(limit) is not int or not 1 <= limit <= 16:
            raise ContractError("Invalid evidence limit")
        if not authorized <= self.records.keys():
            raise ContractError("Unknown authorization source")
        tokens = self._tokens(query)
        rows = [EvidenceHit(src, len(tokens & r["tokens"]), r["sha256"], r["title"])
                for src, r in self.records.items() if src in authorized]
        rows.sort(key=lambda hit: (-hit.score, hit.source_id))
        return rows[:limit]

    def validate_refs(self, references: list[str] | tuple[str, ...], authorized: set[str]) -> None:
        if not set(references) <= authorized or not authorized <= self.records.keys():
            raise ContractError("Evidence outside granted scope")

    def manifest(self) -> dict[str, Any]:
        return {src: {"sha256": v["sha256"], "title": v["title"]}
                for src, v in sorted(self.records.items())}
