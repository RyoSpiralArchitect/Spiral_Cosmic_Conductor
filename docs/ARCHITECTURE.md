# Architecture / contracts (alpha)

## Separation of concerns

1. **Ledger owns claim truth status**: a single transactional SQLite owner serializes proposals. It records `missing → candidate → verified` or `candidate → invalidated → candidate` and blocks stale grants.
2. **Agents own drafts, not authority**: model output is a JSON patch; it cannot promote itself to verified, select a new source, alter the dependency DAG, access a private context, or grant a new role.
3. **Verifier owns the closed-world consistency check**: source facts are supplied externally and assumed for this limited fixture. Algebraic operators (`add/sub/mul/ge/le/eq/and`) are deterministic.
4. **Controller owns routing and budget**: one fresh cheap call, escalation to a separate repair worker after rejection, deadline and cost reservation. A model cannot control the budget by its own telemetry.
5. **Multipolar broker owns projection boundaries**: per-agent read/write policy, explicit refusal, durable conflict records; agents do not share implicit private memories. The authoritative grant is still the ledger ticket.
6. **Evidence index owns document identity only**: SHA-256 digests, bounded lexical previews, strict source-scope checks. Indexing is not evidence authentication or semantic RAG.
7. **Structural backward credit owns a diagnostic projection**: on failed deterministic validation, it maps root and affected dependency descendants. This is not an implementation of the separate EBRT differentiable algorithm.
8. **Runtime backend plan is a proposal**: a typed local SpiralTorch plan must be validated by a trusted adapter before any future device execution. No GPU operation occurs in the current application.

## Failure boundaries

- A malformed or overly broad model patch consumes its one-use grant and is rejected atomically.
- A changed version in the read set triggers stale-read rejection.
- A bad upstream fact invalidates already-built dependent candidates but retains unrelated branches.
- All context sent to optional local models is restricted to the target's dependency closure and explicit input sources.
- Candidate claims are never represented as externally verified answers.
- Once a call has been reserved it costs budget even if a model times out. Hard process-kill deadlines are not provided.

## What must be independently evaluated

* Superiority to strong single-model and debate baselines.
* Local correction rates on tasks that are **not** predetermined arithmetic DAGs.
* Correctness under adversarial source content and actual multi-worker parallelism.
* Validity of any meaning density proxy against quality and cost; no proxy is shipped.
* EBRT native gradient integration and SpiralTorch execution/dispatch receipts.
* Reproducibility and model comparison under pinned weights, seeds, exact hardware and timing order.

## Evolution plan

**Next minimal experiment:** replace scripted workers with two local open-weight models, freeze identical tasks and call budgets, run single-model vs full-retry vs local-rollback conditions, and report both recovery accuracy and total tokens/latency. Promotion gate: matched data, fixed sampler config, no contamination, robust n, negative results retained.

**Then:** implement a narrow external EBRT adapter with typed public trajectory, actuator, restart token, and explicit gradient boundary; tests must prove zero-control identity, isolated rollback, and receipt replay.

**Only after that:** SpiralTorch bridge reads authenticated kernel capability truth; chosen numerical op is executed through a separately tested adapter with parity against a CPU reference, including negative capability and rollback tests.
