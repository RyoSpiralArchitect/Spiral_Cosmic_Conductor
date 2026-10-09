# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 Ryo Higa / SpiralReality
"""One inspectable control-plane run: ledger + policy + plural projections + receipts."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .adapters import Worker
from .backtracking import derive_structural_credit
from .control import BudgetRouter
from .evidence import EvidenceIndex
from .ledger import Ledger
from .multipolar import Agent, MeaningCapsule, MultipolarBroker
from .narrative import narrative_timeline
from .runtime import Conductor, Limits, export_run
from .spiraltorch_bridge import new_proposal
from .telemetry import extract_telemetry
from .types import ContractError, Task, canonical


@dataclass(frozen=True)
class CosmicResult:
    complete: bool
    stop_reason: str
    report_path: Path
    receipt_path: Path
    capsules: int
    structural_credit_events: int


def write_json(path: Path, obj: Any) -> None:
    path.write_text(json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=2,
                               allow_nan=False) + "\n", encoding="utf-8")


def run_cosmic(task: Task, fast: Worker, careful: Worker, out: Path,
               *, limits: Limits | None = None, speculation: bool = True,
               resume: bool = False) -> CosmicResult:
    """Run closed-world validation; export differentiated experiment evidence.

    Default CLI supplies scripted workers. With a separately configured local
    server, this routine can accept a real Worker using the same patch contract.
    """
    if fast.name == careful.name:
        raise ContractError("Workers must be distinct")
    if out.exists() and any(out.iterdir()) and not resume:
        raise ContractError("Output directory must be empty unless --resume is passed")
    out.mkdir(parents=True, exist_ok=True)
    all_ids = frozenset(task.by_id)
    broker = MultipolarBroker([
        Agent("controller", all_ids, all_ids),
        Agent("observatory", all_ids, frozenset()),
        Agent(fast.name, all_ids, all_ids),
        Agent(careful.name, all_ids, all_ids),
    ])
    # A source index is read-only; it is not an oracle and never changes task facts.
    evidence = EvidenceIndex(task)
    with Ledger(out / "ledger.sqlite", task) as ledger:
        report = Conductor(ledger, fast, careful, limits, speculation=speculation,
                           router=BudgetRouter(), broker=broker).run()
        export_run(ledger, report, out)
        events = ledger.events()
        audit = ledger.audit()
    failures: dict[str, list[str]] = {}
    for event in events:
        kind, payload = event["kind"], event["payload"]
        if kind == "verification_failed":
            conflict = broker.record_conflict(payload["claim_id"], event["hash"], payload["reason"])
            failures.setdefault(payload["claim_id"], []).append(conflict.id)
        if kind == "verified":
            for conflict_id in failures.pop(payload["claim_id"], []):
                broker.resolve_conflict(conflict_id, event["hash"])
        if kind not in {"patch_committed", "verified"}:
            continue
        if kind == "patch_committed":
            entries = [(op["claim_id"], op["value"], tuple(op["evidence_ids"]),
                        "candidate", event["actor"])
                       for op in payload["ops"]]
        else:
            entries = [(c["id"], c["value"], tuple(c["evidence_ids"]),
                        "verified", "closed-world-checker") for c in payload["changes"]]
        for idx, (cid, value, refs, state, actor) in enumerate(entries):
            slot = task.by_id[cid]
            evidence.validate_refs(refs, {slot.source} if slot.source else set())
            capsule = MeaningCapsule(
                uid=f"capsule_{event['seq']}_{idx}", sender="controller",
                receiver="observatory", claim_id=cid, value=value, state=state,
                evidence_ids=refs, audit_hash=event["hash"],
                note=f"projection_from_{actor}"[:300],
            )
            result = broker.deliver(capsule)
            if not result.accepted:
                raise ContractError("Internal capsule policy violation")
    credits = derive_structural_credit(task, events)
    telemetry = extract_telemetry(events, report)
    projection = broker.export()
    write_json(out / "council.json", projection)
    (out / "capsules.jsonl").write_text("".join(canonical(c) + "\n" for c in projection["capsules"]),
                                        encoding="utf-8")
    write_json(out / "credit_events.json", [c.to_dict() for c in credits])
    write_json(out / "telemetry.json", telemetry)
    write_json(out / "narrative_timeline.json", narrative_timeline(events))
    write_json(out / "evidence_index.json", {
        "index": evidence.manifest(),
        "query_preview": [h.to_dict() for h in evidence.search(task.goal, authorized=set(task.sources))],
        "caveat": "Lexical overlap is not semantic relevance or factual verification",
    })
    write_json(out / "spiraltorch_refract_proposal.json", new_proposal().to_dict())
    write_json(out / "session_receipt.json", {
        "project": "Spiral_Cosmic_Conductor", "version": "0.2.0",
        "task_fingerprint": task.fingerprint,
        "complete_within_supplied_fixture": report["complete"],
        "stop_reason": report["stop_reason"],
        "event_head_sha256": audit["head_sha256"],
        "audit_replay_matches": audit["state_replay_matches"],
        "capsules": len(projection["capsules"]),
        "conflicts_recorded": len(projection["conflicts"]),
        "structural_credit_events": len(credits),
        "evidence_claim": "closed_world_only",
        "device_execution": "NOT_EXECUTED",
        "real_model_performance": "NOT_ASSESSED",
        "ebrt_gradients": "NOT_EXECUTED",
    })
    # A local manifest aids reproducibility; it is neither a signature nor an external attestation.
    manifest: dict[str, str] = {}
    for path in sorted(out.iterdir()):
        if path.is_file() and path.name not in {"SHA256SUMS", "ledger.sqlite-wal", "ledger.sqlite-shm"}:
            manifest[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    write_json(out / "artifact_manifest.json", {"sha256": manifest,
                  "claim": "local_integrity_manifest_no_external_attestation"})
    return CosmicResult(report["complete"], report["stop_reason"], out / "report.json",
                        out / "session_receipt.json", len(projection["capsules"]), len(credits))


def verify_bundle(run_dir: Path) -> dict[str, Any]:
    """Verify local artifact digests. These are not signatures."""
    from .types import strict_json
    manifest_path = run_dir / "artifact_manifest.json"
    if not manifest_path.is_file():
        raise ContractError("No artifact manifest to verify")
    data = strict_json(manifest_path.read_text(encoding="utf-8"))
    expected = data.get("sha256")
    if not isinstance(expected, dict) or not expected:
        raise ContractError("Invalid artifact manifest")
    for name, sha in expected.items():
        if not isinstance(name, str) or name in {"", ".", ".."} or "/" in name or "\\" in name:
            raise ContractError("Invalid manifest path")
        p = run_dir / name
        if not p.is_file() or p.is_symlink() or not isinstance(sha, str):
            raise ContractError("Missing or invalid manifest artifact")
        if hashlib.sha256(p.read_bytes()).hexdigest() != sha:
            raise ContractError(f"Artifact digest mismatch: {name}")
    return {"manifest_valid": True, "files_checked": len(expected),
            "claim": "local_integrity_no_external_signature"}
