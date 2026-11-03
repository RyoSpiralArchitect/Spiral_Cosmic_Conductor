# Spiral_Cosmic_Conductor 🌀

**v0.2.0 · transactional multimodel reasoning runtime · Python 3.10+ · local-first**

Coordinate separate reasoning agents through **versioned public artifacts, scoped proposals, and reversible decisions**. Preserve where an idea came from, return a problem to its producer, and keep useful alternatives without merging agents' private memories or treating their judgments as proof.

> **Status:** research alpha. The reviewed Duet / Breath / Reflux / branching-memory stack is integrated as an **opt-in, host-controlled Python API**, not an autonomous model runner. Its tests establish protocol behavior, not improved reasoning. No real-model experiment of this new stack has run. Historical provider/text observations remain separately documented and unchanged.

## Three paths, distinct claims

| Path | Entry point | What it establishes |
| --- | --- | --- |
| Structured reasoning | `python3 -m conductor cosmic-demo` / `cosmic-run` | Scoped patches, deterministic checking against supplied facts, dependency invalidation and repair |
| Text handoffs and observation | `experiments/` and the [text memory guide](docs/TEXT_MEMORY.md) | Source-linked generations, source comparison, captured/replayed transmission and post-hoc observation; semantics remain unassessed |
| New intervention library | `from conductor.intervention import ...` | Explicit duet modes, thinking phases, targeted return routes and retained decision branches; returns requests rather than invoking providers |

`cosmic-demo` has **not** silently become a Duet demo. The new library does not activate itself in the existing CLI or modify the observation-only emergence probe.

## First run: no model required

Run from the repository root:

```bash
python3 -m conductor cosmic-demo --out runs/first
python3 -m conductor audit runs/first
python3 -m unittest discover -s tests -v
```

The demo uses scripted workers: `fast` intentionally misreads a fact, the host checker rejects it, and `careful` repairs only the affected dependency branch. Another branch stays untouched. No model is downloaded and no public service is contacted. To compare the non-speculative policy, use a fresh directory:

```bash
python3 -m conductor cosmic-demo --no-speculation --out runs/no-speculation
```

The [2026-11-03 focused review](docs/INTERVENTION_REVIEW_2026_11_03.md) added 19 adversarial regressions and fixed four process/data-flow defects. The integrated baseline passed **261 tests** in the Python 3.10 / 3.12 / 3.13 CI matrix, alongside the existing scripted simulation/replay checks. This is engineering evidence, not a model benchmark or an independent security certification.

## Duet, Breath, Reflux, memory

```text
frozen source + explicit host policy
             |
     Duet: separate candidates
             |
     Breath: compare or recompose
             |
     Reflux: return a declared issue to its actual producer
          /                         \
 new candidate                 retained alternative branch
          \                         /
     selected decision memory + current condition evidence
             |
     next explicit round, hold, or yield unassessed candidates
```

| Mechanism | Current behavior | Important limit |
| --- | --- | --- |
| [Duet / Brotherhood](docs/DUET.md) | `peer_duet`, `lead_bro`, `lead_lil`, `braid`; independent peer views or explicit leading handoffs; one-use durable reservations | The host chooses modes and context; no learned routing policy |
| [Breath](docs/BREATH.md) | `receive`, `expand`, `compare`, `recompose`, `yield`, `hold`; evidence-linked transitions and explicit resume | Model text does not change phase; `yield` is not source-checked release |
| [Reflux](docs/REFLUX.md) | Distinguishes annotation errors, source conflicts, counterexamples, review mismatches and productive differences; critics can themselves be corrected | Findings are declared assessments. New output still needs rechecking |
| [Branching memory](docs/BRANCH_MEMORY.md) | Evidence-linked decisions; non-destructive `fork / rewind / merge`; reopening conditions; selected-context projection | Merge retains alternatives, not consensus. Search is lexical; reconsideration does not auto-adopt |

The review closed incomplete-round comparison escapes, stale targeted rechecks, loss of reviewing-role identity after correction, and omission of current reopening evidence from the next round. The current memory projection includes only conditions named by selected nodes, including their latest unknown/unsatisfied assessments; it does not send all branch history.

A minimal **scripted** use of the new API:

```python
from pathlib import Path
from tempfile import TemporaryDirectory
from conductor.intervention import DuetSession, BreathController

with TemporaryDirectory() as directory:
    with DuetSession(Path(directory) / "duet.sqlite", "example") as session:
        session.begin("Different opinions may remain.", {"goal": "Keep alternatives"})
        breath = BreathController(session)
        breath.signal("start", "Read independently", expected_revision=session.revision)
        breath.open_round("peer_duet", "propose", "Two readings",
                          expected_revision=session.revision)
        outputs = []
        for text in ("Keep two proposals.", "Make agreement optional."):
            request = breath.request()  # Data only: no model/network call.
            outputs.append(session.submit(request, {
                "text": text, "uses": [], "disposition": "candidate"
            }))
        breath.signal("candidates", "Compare both", refs=tuple(outputs),
                      expected_revision=session.revision)
        breath.signal("yield", "Return alternatives without certifying them",
                      refs=tuple(outputs), expected_revision=session.revision)
        assert breath.report()["phase"] == "yield"
        assert breath.report()["semantic_status"] == "unassessed"
```

For all four mechanisms together, read the [stack guide](docs/INTERVENTION_STACK.md) and the [offline integration test](tests/test_branch_memory.py). A future provider runner must preserve reservation/submit ownership, refusal, pending-call recovery, capture and replay. **No live intervention CLI is supplied yet.**

## Next experiment: correction without erasing alternatives

The [next experiment plan](docs/INTERVENTION_EXPERIMENT_PLAN.md) is **DRAFT / NOT RUN**. The first proposed comparison starts from identical public artifacts and the same declared finding: fixed-writer revision versus returning that issue to its producer. It measures target correction and collateral preservation separately. Duet scheduling and selected-memory effects are separate later contrasts, not inferred from an all-features-on win.

Before any live run: implement and qualify the new transport/capture bridge offline, freeze tasks and rubrics, separate intervention judgments from evaluation, and obtain explicit execution/budget approval. This README and the plan do not authorize a run.

## Existing local and text workflows

The structured CLI can use a separately operated local server implementing the llama.cpp-compatible chat-completions wire format:

```bash
cp examples/models.local.example.json examples/models.local.json
# Configure your own server's literal loopback IP, explicit port and model IDs.
python3 -m conductor cosmic-run \
  --task examples/capacity.task.json \
  --models examples/models.local.json \
  --out runs/local
```

This command performs real local inference when a server is available; it is distinct from the scripted examples. Input artifacts may contain sensitive text, so choose a retention policy before supplying private documents.

Historical experiments retain their own explicit execution configuration:

| Workflow | Guide | Offline starting point |
| --- | --- | --- |
| Structured provider pilot | [Provider pilot](docs/PROVIDER_PILOT.md) | `python3 -I -S -B experiments/provider_pilot.py --mode simulated --out runs/provider-simulation` |
| Public text handoff | [Text memory](docs/TEXT_MEMORY.md) | `python3 -I -S -B experiments/text_memory_pilot.py --out runs/text-simulation` |
| Source fidelity and repair | [Source alignment](docs/SOURCE_ALIGNMENT.md) | `python3 -I -S -B experiments/source_alignment_pilot.py --out runs/source-check` |
| Faithful / creative transmission | [Transmission comparison](docs/TRANSMISSION_COMPARISON.md) | `python3 -I -S -B experiments/transmission_comparison.py --out runs/transmission-sim` |
| Post-hoc lineage observation | [Emergence probe](docs/EMERGENCE_PROBE.md) | `python3 -I -S -B experiments/emergence_probe.py --out runs/emergence-sim` |

Use fresh output directories. Each guide describes its replay path and explicit live prerequisites. The open transmission configuration leaves application content/memory/token/time/repair ceilings unset; provider limits still apply. The new library likewise does not invent hidden content ceilings. Optional resource limits must be explicit and recorded.

Source alignment releases a **candidate assessed by a model**, not verified truth. Valid IDs, hashes and adoption declarations establish reference structure, not semantic correctness. The historical emergence observer reads frozen prefixes only after generation and is never fed back into generation.

## Integration map and boundaries

| Family | Implemented here | Not claimed |
| --- | --- | --- |
| Cosmic Conductor / Brotherhood | Scoped central commit, explicit Duet collaboration and Breath phases | Autonomous semantic policy or general multi-agent advantage |
| Narrative / ND-RAG | Evidence index, public chronology, decision branches and scoped lexical recall | Full semantic RAG, cross-episode private-memory merging, compatible `.nar` filesystem |
| Multipolar / PRSN | Separate contexts, public projections, refusals and conflict records | Cryptographic isolation or distributed consensus |
| EBRT / Reflux | Structured dependency invalidation; separate free-text return-to-producer protocol | Native EBRT gradients, token-local edits or suffix regeneration |
| Tri-Phase / A2E | Structured call/cost/deadline policy and observed bytes/tokens/latency | Calibrated meaning/attention scores or measured joules |
| SIG / ALR-like boundary | Host-owned authority, untrusted text treated as data, explicit source checks | General semantic attack detection or production sandboxing |
| SpiralTorch / SpiralK | Typed backend-plan proposal and capability-receipt shape | Actual WGPU/HIP dispatch or a GPU controller |

**InfiniteRoom remains intentionally excluded.** Historical names are not evidence of a complete port; see the [legacy inventory](docs/LEGACY_INVENTORY.md), supplemented by the current intervention guides.

Authority belongs to trusted host code. Models propose artifacts, not permissions. Direct hostile Python can bypass application wrappers and is outside the threat model. `verified` applies only to the structured ledger's supplied facts and operations. Free-text `adopted`, `yield`, and condition `satisfied` remain host decisions or assessments, not truth labels.

Reflux preserves old artifacts and produces a new candidate. This is artifact-level targeting, not a guarantee of minimal textual changes. Whether an actual model preserves unrelated meaning is an experimental question.

The structured demo exports `ledger.sqlite`, `events.jsonl`, `TRACE.md`, `answer.txt`, projections, telemetry and integrity manifests. Its `audit` command checks that bundle; intervention sessions instead use `session.memory.audit()` and their typed APIs. Hashes/replay are local-integrity mechanisms, not signatures or trusted timestamps. Missing usage is not zero cost; `relative_cost_units` are not currency or energy.

## Design and evidence

Start with [Architecture](docs/ARCHITECTURE.md), [Module map](docs/MODULE_MAP.md), [Provenance](docs/PROVENANCE.md), the [intervention stack](docs/INTERVENTION_STACK.md), and the [focused review](docs/INTERVENTION_REVIEW_2026_11_03.md). The older [structured evaluation protocol](docs/EVAL_PROTOCOL.md) and new [intervention plan](docs/INTERVENTION_EXPERIMENT_PLAN.md) describe different experimental paths.

## License

**GNU Affero General Public License version 3 ONLY** (`AGPL-3.0-only`). Copyright © 2026 Ryo Higa / SpiralReality. This is **not** an `-or-later` grant. See [LICENSE](LICENSE) and the [official text](https://www.gnu.org/licenses/agpl-3.0.txt).

The repository contains an authorized relicensing of the earlier Spiral Conductor Runtime prototype plus new code. External projects are design references, not vendored implementations. Verify permissions before importing external sources.
