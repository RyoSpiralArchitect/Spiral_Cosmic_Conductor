# Spiral_Cosmic_Conductor 🌀

**v0.2.0 · transactional multimodel reasoning runtime · Python 3.10+ · local-first**

A small, executable research nucleus for coordinating **separate reasoning agents over a shared, versioned claim ledger**, without merging their private memories, surrendering verification to the models, or rewriting a complete answer after every error.

> **Status:** integrated alpha, not a benchmark win, autonomous AGI system, GPU controller, or production security boundary. The core uses deterministic structured tasks and an explicit verifier. The application CLI connects to a separately operated loopback inference server; an opt-in experiment harness also supports three cloud providers.

## First run

```bash
python3 -m conductor cosmic-demo --out runs/first
python3 -m conductor audit runs/first
python3 -m unittest discover -s tests -v
```

The demo intentionally makes a scripted `fast` worker misread a fact. The validator detects it, invalidates just the dependent claims, and a separate `careful` worker repairs the answer. A different branch stays untouched. The workers are **scripted test doubles**, not live LLMs. Nothing is downloaded, and no public service is contacted.

To explore the non-speculative policy:

```bash
python3 -m conductor cosmic-demo --no-speculation --out runs/no-speculation
```

To connect an explicitly configured local server that implements the llama.cpp-compatible chat-completions wire format:

```bash
cp examples/models.local.example.json examples/models.local.json
# Edit localhost endpoint and model identifiers to match your own running servers.
python3 -m conductor cosmic-run \
  --task examples/capacity.task.json \
  --models examples/models.local.json \
  --out runs/local
```

No API key is required for the scripted demo or tests. Only literal loopback IP endpoints with explicit ports are accepted by the optional local model adapter. Generated artifacts contain input data; do not feed it sensitive documents without a retention plan.

For a bounded OpenAI / Mistral / Gemini observation pilot, start with the offline transport simulation:

```bash
python3 -I -S -B experiments/provider_pilot.py --mode simulated --out runs/provider-simulation
```

The [provider pilot guide](docs/PROVIDER_PILOT.md) describes the explicit live command, required environment variables, recorded failures and repairs. Both chat adapters supply a per-dispatch operation and evidence contract; the model still generates the value and the ledger verifies it. The unit suite also replays 45 recorded provider responses without credentials or live calls.

For free-text handoffs with a small persistent public memory:

    python3 -I -S -B experiments/text_memory_pilot.py --out runs/text-simulation
    python3 -I -S -B experiments/text_memory_pilot.py --mode replay --replay runs/text-simulation --out runs/text-replay

The [text memory guide](docs/TEXT_MEMORY.md) explains the abstract teacher, episode isolation, source/critique/revision links, and bounded live pilot. Each role reopens its episode's SQLite memory. An adopted finding must point to actual output text, but a valid link does **not** establish that the finding was correctly addressed. These text artifacts remain semantically unassessed and do not enter the verified claim ledger.

## Actual runtime boundaries

```text
                source facts / question / dependency DAG
                                 │
                 [ND-RAG-style read-only evidence index]
                                 │
      [Multi-polar broker]    [Budget router: Tri-Phase-inspired]
          scope/refusal               │
                    └────────┬────────┘
                             ▼
                    Cosmic Conductor
                             │
             scoped one-use authority ticket
                             │
             fast model / careful model
                             │  JSON patch (not final truth)
                             ▼
             SQLite ledger / revision check
                             │
                     deterministic verifier
                         ┌───┴────┐
                      accept    conflict event
                         │          │
                         │     dependency rollback
                         │          │
                         └──────┬───┘
                                ▼
                public MeaningCapsule projection
                structural credit / event timeline
                telemetry / integrity receipts
                                │
                  optional SpiralTorch plan
                   (proposal only, NOT executed)
```

**Execution authority is centralized.** Workers are replaceable and have grant-scoped abilities. `verified` means only **consistent with the supplied structured facts and operations**, not externally true. Conflicts remain explicit, and refusal is a valid status in the multi-polar protocol.

## What is integrated today

| Family / historical idea | Present implementation | Boundary |
|---|---|---|
| Cosmic Conductor + Brotherhood | dynamic worker identity, role-scoped proposal grants, audited central commit | Runnable |
| Narrative Kernel / ND-RAG | evidence provenance index and non-generative event chronology | Runnable subset; not full semantic RAG |
| Multipolar / PRSN boundary | `MeaningCapsule`, private per-agent contexts, scoped delivery/refusal, explicit conflict records | Runnable in-process protocol; not cryptographic isolation |
| EBRT | deterministic backward **dependency** invalidation + repair event trace | Runnable structural adapter; differentiable EBRT not imported |
| Tri-Phase | strict call/cost/deadline budget routing, escalation after failure | Runnable policy; meaning density and energy not calibrated |
| A2E | explicit token/byte/cost/latency observation with missing metrics marked `null` | Runnable telemetry adapter; joules not measured |
| SIG / ALR-like boundary | untrusted source text treated as data; model cannot verify, grant, execute, or render unverified answer | Runnable narrow controls; no general safety guarantee |
| SpiralTorch / SpiralK | typed backend-plan proposal and capability-receipt gate | **Not executed**; separate integration required |

**Excluded intentionally:** the InfiniteRoom family. No source code, runtime dependency, or implementation of it is bundled.

## Run artifacts

`runs/first/` contains `ledger.sqlite`, `events.jsonl`, `task.json`, `report.json`, `answer.txt`, `TRACE.md`, `capsules.jsonl`, `council.json`, `credit_events.json`, `evidence_index.json`, `narrative_timeline.json`, `telemetry.json`, `spiraltorch_refract_proposal.json`, `session_receipt.json`, `artifact_manifest.json`. The `audit` command checks ledger event-chain replay and the local artifact manifest. Neither is a digital signature or trusted timestamp.

## Engineering boundaries

- No LLM benchmark improvements claimed. No current integration with EBRT's native gradient machinery or SpiralTorch's WGPU/HIP dispatcher.
- Raw source facts belong to the task creator. Lexical retrieval scores are **not truth** or embedding-based semantic similarity.
- A worker can propose only within a time-limited grant; the authoritative verifier is deterministic host code.
- Scopes and audit logs are application mechanisms, not a sandbox, cryptographic attestation, or a guarantee that a local model server is trustworthy.
- Each agent's private ContextGraph equivalent stays process-private. Do not mistake this for protection from hostile code running in the same Python process.
- All numbers in `relative_cost_units` are abstract policy units, not currency or energy.

For design contracts, integration priorities, source provenance, and a candid assessment of what has/hasn't been tested, read [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md), [`docs/MODULE_MAP.md`](docs/MODULE_MAP.md), and [`docs/PROVENANCE.md`](docs/PROVENANCE.md). The [historical module inventory](docs/LEGACY_INVENTORY.md) records names that are not yet executable plugins. The [real-model evaluation protocol](docs/EVAL_PROTOCOL.md) states how a future performance claim must be earned.

## License

**GNU Affero General Public License version 3 ONLY** (`AGPL-3.0-only`). Copyright © 2026 Ryo Higa / SpiralReality. This is **not** an `-or-later` grant. See [`LICENSE`](LICENSE) and the [official GNU AGPL v3 text](https://www.gnu.org/licenses/agpl-3.0.txt).

This new repository contains an authorized relicensing of the earlier Spiral Conductor Runtime prototype plus new adapter code. External repositories are listed as design references and are **not vendored** here. Verify permissions before importing third-party sources.
