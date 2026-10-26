# Observing where a new relation first becomes visible

This probe starts from the same recorded Mistral draft in both arms, changes only a public review-guidance artifact for the critic/reviser, and hands each resulting candidate to another creator. It records where an idea appears in public text and how the next output uses it. It cannot locate a latent internal moment, establish worldwide novelty, or prove emergence from a single pair.

The inherited source memo, abstract teacher, creative task brief, model order and generation settings remain fixed. The `original` arm applies the existing teacher as usual. The `purpose_aware` arm interprets it against the task's allowed transformations: a fictional metaphor is not automatically a factual error, while novelty is not automatically good. The intervention contains no concrete ideas, passages or desired answer from the earlier outputs.

The historical draft is imported only after reconstructing the original proposer request and matching its recorded SHA-256. The draft text also matches its recorded artifact hash. Review guidance is outside the proposer's read scope, so importing the same draft does not invent a claim that the proposer saw the new guidance. Each arm has its own public memory but the same episode identifier; arm names are capture labels, not generation hints.

## Route and observation

Both arms run Gemini critic → OpenAI reviser → Gemini source auditor, with the existing OpenAI repair/recheck controller when needed. A released candidate goes to a Mistral creator, who writes an actual fictional scene or setting and declares which incoming units were carried, developed, recombined, merely mentioned, opposed or not found. A held source candidate is not silently forwarded.

Observation packets are frozen at each accepted boundary: shared draft, each critique, each revision, any accepted repair, and each continuation. They contain exact text units with offsets/hashes, earlier history, the units actually visible to that generator, and the current output. They do not contain later outputs or the other arm. The common draft is observed once.

Only after all generation finishes does a separate Gemini observer process these prefixes. Its results are never returned to a generator, added to generation memory, used for release, or used to select another generation. An observer failure therefore cannot alter completed generations. This is a separate observation role, not an independent evaluator model: Gemini is also the critic and source auditor.

The observer separates:

- relation: retained, rephrased, elaborated, recombined, introduced, assessment, uncertain;
- novelty: none, surface expression, new relation, uncertain;
- later use: carried, developed, recombined, mentioned only, opposed, not found, uncertain.

It must cover every target unit and every tracked prior unit. The host verifies coverage, real references, visibility boundaries and anchored uptake. Semantic labels remain `unassessed`. A citation or producer adoption declaration is not proof of development; the generated passages remain available for inspection. Original nine-component critique annotations are retained separately from these lineage labels.

## Run

The [configuration](../examples/emergence-probe.json) pins the public seed receipt and explicitly lists the arms and providers. It inherits the seed experiment's null content/memory/repair limits and specifies null output-token/timeout/response limits. No arbitrary numerical ceiling or automatic retry is added; provider limits still apply. The initial draft is reused without an API call. The configured finite handoff route is the experiment being observed, not a hidden repair/call budget.

    python3 -I -S -B experiments/emergence_probe.py --out runs/emergence-sim
    python3 -I -S -B experiments/emergence_probe.py --mode replay --replay runs/emergence-sim --out runs/emergence-replay

For an authorized live run using already configured credentials:

    python3 -I -S -B experiments/emergence_probe.py --mode live --out runs/emergence-live --stop-file runs/emergence.stop

`--credentials-from-zshrc` imports literal existing assignments without executing shell startup code. Ctrl-C interrupts execution; a stop file is checked before dispatch. Each run requires a new/empty directory. Operator-interrupted captures are retained as aborted observations, not resumable jobs.

The capture retains input configuration, seed receipt, raw requests/responses, reservations, API attempts, memory events, generated text, frozen observation packets, observer results, timing, source archive and integrity manifest. Replay compares requests, response records, boundaries, clock values and results without API calls. Capture hashes provide local integrity, not authenticity signatures.

The two arms run sequentially, with one sample each. Compare visible transformations and their later use; do not interpret a higher novelty count as better work or as an estimated causal effect of guidance. A concrete next scene is itself requested by the common continuation prompt, so producing a scene alone is not evidence of spontaneous emergence.
