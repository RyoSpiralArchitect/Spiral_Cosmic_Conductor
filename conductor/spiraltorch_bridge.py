# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 Ryo Higa / SpiralReality
"""Conservative typed control proposal for SpiralTorch's execution plane.

This module does not import or execute SpiralTorch. It validates receipts and
serializes suggestions only; runtime execution requires an explicit adapter.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .types import ContractError, check_id


@dataclass(frozen=True)
class RefractProposal:
    target: str
    backend: str
    operation: str
    precision: str
    reason: str
    authorized: bool = False
    executed: bool = False

    def __post_init__(self) -> None:
        check_id(self.target)
        if self.backend not in {"cpu", "wgpu", "hip"}:
            raise ContractError("Unknown backend")
        if self.operation not in {"matmul", "layer_norm", "attention", "softmax"}:
            raise ContractError("Unknown operation")
        if self.precision not in {"f32", "f16", "bf16"}:
            raise ContractError("Unknown precision")
        if len(self.reason) > 250 or self.authorized or self.executed:
            raise ContractError("Proposals cannot self-authorize or claim execution")

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "status": "NOT_EXECUTED"}


def validated_backend_ready(receipt: dict[str, Any], proposal: RefractProposal) -> bool:
    """Check declared *type* of capability evidence, not its authenticity.

    No untrusted JSON receipt can establish proof by itself. Only a trusted local
    SpiralTorch adapter can authenticate the recorded dispatch sentinel.
    """
    if not isinstance(receipt, dict):
        raise ContractError("Invalid capability receipt")
    if receipt.get("backend") != proposal.backend or receipt.get("status") != "ready":
        return False
    required = ("runtime_dispatch_sentinel" if proposal.backend in {"wgpu", "hip"}
                else "static_host_contract")
    return receipt.get("proof_type") == required and receipt.get("trusted_local_adapter") is True


def new_proposal(target: str = "reasoning_probe") -> RefractProposal:
    return RefractProposal(target, "cpu", "matmul", "f32",
                           "Example control-plane proposal; not dispatched")
