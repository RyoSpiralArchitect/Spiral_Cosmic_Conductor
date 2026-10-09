# Evaluation protocol v0.1 (preregister before real-model runs)

## Primary comparison

The current scripted test demonstrates mechanism only. The first real-model evaluation must pin a task corpus, held-out split, model snapshots, decoding options, process/hardware versions, stop conditions, maximum calls, and cost accounting **before seeing outcomes**.

Compare under matched model/cost configurations:

| Arm | Definition |
|---|---|
| A | One strong model, one answer attempt |
| B | Full-context retry after failed verification |
| C | Fixed proposer → critic → writer workflow |
| D | Cosmic Conductor: grant-scoped claims and local revalidation |
| E | D plus genuine EBRT public-trajectory adapter (only after independently tested) |
| F | E plus SpiralTorch control-plane execution (only after numerical parity tests) |

## Required reporting

- Correct solved tasks / full evaluated tasks and confidence intervals.
- Recovery rate conditional on first-stage error; also report cases where repair introduced errors.
- Total model calls, *measured* prompt/completion tokens or missing counts, wall-clock latency including controller overhead, output bytes, CPU/GPU utilization where actually measured.
- Local invalidation size, full retry tokens, state corruption attempts, role-scope violations.
- Negative/inconclusive cases and all failed preflight gates. Never infer joules from abstract budget units.

## Evaluation hygiene

- Hold out new error forms and long-horizon tasks; no cherry-picked one-shot examples.
- Include a sham/random routing control when testing any new proposed semantic or phase metric.
- Use at least one stronger independent verifier where closed-world arithmetic is inappropriate.
- Report native model behavior separately from simulated/scripted agents.
- Snapshot models and configs by immutable revision and content hashes. Never put model weights or private task data into the repository by default.
