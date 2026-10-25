# Faithful and emergent transmission on one route

The comparison uses the same source memo, teacher, role instructions, model order, schema constructors, public memory, source comparison, and repair controller. Only the task brief and permitted source transformation differ:

- Faithful: hand the original conditions and unresolved choices to an implementation colleague; preserve all source units.
- Emergent: hand a fictional creative seed to another creator; transform the source material explicitly. The supplied case selects explore mode with no protected source units.

Both travel through proposer → critic → reviser → source auditor → repair/recheck when needed. Providers are configured in [the experiment input](../examples/transmission-comparison.json), with Mistral/Gemini/OpenAI for the first three roles and Gemini/OpenAI for checking/repair. There is no case-specific routing branch.

## No application ceiling in this configuration

The supplied configuration sets character, unit, reference, memory, output-token, timeout, call-count and repair-count maxima to null. Model requests omit max_tokens/max_completion_tokens. The transport reads the provider's full returned body. Optional positive limits can still be supplied explicitly, and are recorded/frozen rather than overridden by another fixed maximum.

Provider context/output limits, availability and defaults still apply. A truncated or refused response is retained and reported; it is not silently accepted or retried. There is no claim of infinite provider capacity.

Nonempty typed fields, valid/sequential IDs, complete coverage, existing references, role scopes, immutable source/policy, atomic publication and optimistic revisions remain protocol requirements. The number of source rows or findings is derived from supplied source/principle data, not a preset output quota. The historical pilots retain their bounded default profiles for reproduction.

Rounds use numbered memory families rather than preallocated slots. A valid source assessment can release a candidate. Unresolved unchanged/repeated candidates are rechecked, then held as candidate_cycle. Errors and explicit interruption also terminate visibly. No duration or number-of-rounds criterion is silently substituted.

## Run and inspect

Simulation and replay:

    python3 -I -S -B experiments/transmission_comparison.py --out runs/transmission-sim
    python3 -I -S -B experiments/transmission_comparison.py --mode replay --replay runs/transmission-sim --out runs/transmission-replay

Explicit live run with already configured environment credentials:

    python3 -I -S -B experiments/transmission_comparison.py --mode live --out runs/transmission-live --stop-file runs/transmission.stop

The optional --credentials-from-zshrc imports literal assignments without executing shell startup code. Ctrl-C interrupts the active process; the stop file is checked before the next provider dispatch. Neither resumes or retries an existing capture. Every invocation requires a new/empty output directory.

Each case has its own public memory and exact source import. The shared teacher never incorporates another case's generated answer. Raw requests/responses, API attempts, failures, committed artifacts, source snapshots, monotonic timing, source-code archive and hashes are retained. Normal and provider-failure captures replay without network; operator-interrupted captures are retained as aborted observations, not resumable jobs.

## Read differences without turning links into scores

results.json includes, for each original unit, the critic's declared draft links, the reviser's declared output links, and every accepted source-check judgment. These are model-declared correspondences. An unreferenced source unit is not proven absent; an addressed declaration is not proof of a change. The nine-component totals are annotation counts, not a quality score.

Compare full generated text as well as source retention, additions, transformations, uncertainty, dispositions, and the final release/hold state. The intent and transformation policy are deliberately different; a single sequential pair does not estimate a causal effect or detector accuracy. All free-text artifacts remain semantically unassessed.
