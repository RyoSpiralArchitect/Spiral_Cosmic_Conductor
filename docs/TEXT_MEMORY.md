# Public text memory and abstract guidance

The structured runtime verifies supplied arithmetic/logical tasks. Free text needs a separate contract: a completed generation or a well-formed reference cannot establish semantic correctness.

This implementation connects a proposer, critic, and reviser through one small SQLite database per episode. It stores only explicitly published artifacts. It does not merge agents' private contexts, retrieve old answers, train weights, or search an unrestricted memory pool.

## Teacher versus episode state

The abstract teacher is a pure, argument-free function in conductor/text_workflow.py. It supplies six principles with desirable, undesirable, and neutral behaviors:

1. Preserve source constraints and distinguish design choices.
2. Preserve unknowns and distinguish assumptions from established facts.
3. Supply concrete procedures and account for limited resources.
4. Preserve participants' choices and make additional burdens explicit.
5. Link differing views to subsequent changes; retain unresolved disagreement.
6. Link claimed changes to evidence in the actual output.

It contains no task-specific numbers, source quotations, old generations, or worked good/bad answers. Both example tasks receive exactly the same teacher hash. Tests reject a set of known case-specific literals. Those tests establish absence of those strings, not semantic proof that guidance is independent of the earlier research questions.

Task-specific material belongs to the current episode: the supplied memo, newly generated draft, newly generated review, revision, and trace. The critique may legitimately refer to that episode's concrete source. It is not silently promoted into a teacher for another episode.

The nine components retain their earlier meaning:

    [positive.+, positive.null, positive.-,
     negative.+, negative.null, negative.-,
     neutral.+, neutral.null, neutral.-]

Polarity concerns the source's intent and constraints; change concerns addition, retention, or omission relative to that source. null means retention, not unknown. The critic supplies element labels; the host derives counts. Several components may be active in one finding. Counts are not a quality score. Uncertain recommendations can remain on hold.

## Durable shared state

SharedMemory stores a frozen episode/role/slot/limit contract and an append-only sequence of publication batches.

- Every artifact has an episode ID, slot ID, version, content hash, author, and parent references.
- Reads and writes use the existing broker's explicit role scopes. Model responses cannot change the contract.
- Commits use SQLite transactions and an expected memory revision. Two replies from the same snapshot cannot both silently overwrite state.
- A batch can publish the revision and trace atomically. Malformed or incomplete traces leave neither artifact committed.
- Parents must exist, match their hashes, and be current and transitively fresh. Updating a source preserves history but blocks stale descendants from fresh dispatch.
- An artifact cannot list its own previous version as a parent; version history already records that relationship. Otherwise updating a slot would immediately make itself stale.
- Default bounds: 64 events, 16,000 characters per artifact, 1 MB of event payloads, and 32,000 characters per worker view. Oversized views fail rather than silently truncate.
- Private broker context is neither read nor exported. No cross-episode lookup or automatic retrieval is implemented.

The single controller is trusted to select the episode, role, and allowed artifacts. This is a cooperative in-process policy, not authentication against hostile Python code or processes with database access. Event hashes detect accidental corruption and mismatched replay; they are not signatures or an external trust anchor. One conservative global revision serializes commits; this is not a distributed memory service.

## Observable revision decisions

The proposer returns text. The host assigns punctuation-based source segment IDs with exact Unicode codepoint offsets. Drafts also split at line boundaries so a bullet list does not become one enormous citation. This is a transparent segmentation rule, not a linguistic sentence parser.

The critic returns six findings, one per abstract principle, each carrying:

- A finding ID and principle ID.
- Existing source IDs and optional draft IDs. Empty draft IDs represent a source-side omission.
- One to four labeled elements and a recommendation.

The reviser returns named output units R01, R02, … and one disposition for **every** finding: adopt, reject, or hold, with a short public rationale. Each adopted finding must reference at least one actual output unit. Unknown IDs, duplicate dispositions, missing findings, and invented authority fields fail closed.

The API JSON Schema includes collection bounds and the source/draft/finding IDs allowed by that particular snapshot. Host validation still rechecks them. A captured response with nineteen draft references in one finding remains a rejection fixture; the implementation does not silently trim it to fit the eight-reference limit.

Revision IDs and output references use only the R01–R32 namespace in the wire schema. The host additionally checks sequential IDs and requires every reference to name an actually returned unit. Request serialization preserves schema property order so revision sentences precede their decisions. Sorting the schema keys would place decisions first; OpenAI documents that Structured Outputs follow schema key order ([official guide](https://developers.openai.com/api/docs/guides/structured-outputs#key-ordering)). Both guards have offline coverage and one resumed live completion below; their individual causal effects are unmeasured.

The host joins output units with newlines and binds source, draft, and output spans to their exact artifact versions and hashes. Each review or trace has a reference table containing the full artifact references once. Spans store an artifact ID, offsets, and a quote hash; full quotations are resolved from scoped historical artifacts, avoiding repeated copies of text and reference metadata. Raw provider responses are preserved separately. A revision unit may be a paragraph; its ID is not proof that it is exactly one linguistic sentence.

Trace decisions link to source/draft spans through their finding ID and the immutable review reference. A shared output-span table stores each revision unit once, even if all six decisions cite it. The full chain is trace → review finding → source/draft spans, and trace decision → output IDs → revision spans.

Every decision has structural_status=linked and effect_status=unassessed. A model may point to a real sentence while misrepresenting its meaning. adopt is therefore the model's declared disposition, not automatic issue resolution. Holds remain explicit in held_finding_ids; rejected findings retain their reasons.

## Bounded experiment

The default fixtures contain the earlier synthetic creative-session source and a new synthetic tool-shelf source. **No previous draft, critique, revision, or case-specific teacher is loaded.** Both tasks start fresh.

The pilot runs Mistral proposer → Gemini critic → OpenAI reviser. Each role closes and reopens the same episode database, so later input really comes from persisted shared state. The model context is a bounded, explicitly selected view; the database transaction is not held during a network call.

Offline mechanism:

    python3 -I -S -B experiments/text_memory_pilot.py --out runs/text-simulation

Explicit, opt-in live invocation using existing environment credentials:

    python3 -I -S -B experiments/text_memory_pilot.py --mode live --out runs/text-live

Alternatively, import literal key assignments from ~/.zshrc without executing it:

    python3 -I -S -B experiments/text_memory_pilot.py --mode live --credentials-from-zshrc --out runs/text-live-from-zshrc

Offline replay against unchanged implementation and inputs:

    python3 -I -S -B experiments/text_memory_pilot.py --mode replay --replay runs/text-live --out runs/text-live-replay

The two live commands above are alternatives, not a sequence. Every invocation needs a new/empty directory. A new directory is not authorization for another live batch.

The maximum is six call attempts: two cases × three stages. Each call reserves its budget before transport, has a 3,072-token output cap and a 60-second HTTP timeout, and has no implicit retries. A failed stage ends that episode; the other independent episode may continue. Unused capacity is not spent on replacement calls. HTTP timeouts are not a hard process-kill deadline.

The plan freezes source hashes, teacher, cases, model identifiers, stages, and limits before the first call. Raw requests/responses, reservations, failed replies, usage including unknown counts, per-role views, public events, source archive, and local manifests remain inspectable. Do not overwrite a failed capture.

A separately planned follow-up can explicitly fork a **sealed draft or review checkpoint** with --resume-from and a matching one-case --cases file. A draft checkpoint permits at most two new calls; a review checkpoint permits one. The importer checks the manifest and episode contract, rebuilds the imported workflow in a temporary database, and requires an identical normalized review and event history. Partial reviews are rejected. It preserves the original files and records imported stages separately from new calls. This is an explicit new experiment, not an automatic retry or a continuation that hides failed calls. Replay carries its own frozen seed database and does not need to read the original capture path.

This pilot changes guidance, prior-answer exposure, output protocol, and state handling together. It tests an executable mechanism and supplies concrete outputs for inspection. It does not isolate the causal effect of abstraction, numeric components, or persistence. The second case is a new synthetic probe, not a statistically independent generalization benchmark.

## Files and checks

- conductor/shared_memory.py: durable artifact contract and bounded views.
- conductor/text_workflow.py: abstract teacher, role inputs, response validation, and trace construction.
- experiments/text_memory_pilot.py: opt-in transport, budgeting, capture, and exact offline replay.
- tests/test_shared_memory.py, tests/test_text_workflow.py, tests/test_text_memory_pilot.py: stale/concurrent writes, scope and episode separation, atomicity, reference validity, interruption receipts, and failure replay.

API responses remain candidates. The deterministic claim ledger and its verification semantics are unchanged.

## Initial live observation, 2026-10-25

Seven separately accounted API attempts produced two drafts and one complete review that was admitted after a local storage-format repair. An OpenAI revision returned eleven output units but all six adoption decisions cited draft IDs (Dxx), not the generated revision IDs (Rxx). The host rejected the whole revision/trace transaction. Raw response fixtures preserve both the critic reference overflow and this output-namespace error.

At this initial checkpoint, the revised wire schema/order guards and the complete persistence/trace path passed offline tests and simulation, but a committed live revision/trace was not yet demonstrated. The failed response also repeated a critic's positive assessment of optional introductions and surveys as source retention, although those activities were draft additions. Shared provenance made this inspectable; it did not establish semantic correctness or show that abstract guidance improved quality.

### Resumed live observation, 2026-10-25

One separately authorized OpenAI call at runtime commit d90cdbdc6670f2aa6a8843e6979c868cd8f64364 resumed the same sealed source/draft/review checkpoint. It returned 22 output units (1,300 characters including line separators), six adopt decisions, and 24 valid output references to 20 distinct units. Revision and trace committed atomically in event 4. All 80 source/draft/output span references resolved to their recorded versions and quote hashes. Exact request/outcome replay passed with zero API calls. Total live attempts across the initial pilot and this follow-up: eight, with no automatic retries.

This demonstrates a completed resumed path using the earlier Mistral draft and locally admitted Gemini review; it is not a new uninterrupted three-provider run. The same teacher and review were used, and the previous failed revision was not supplied as input. Both the R-only schema and property ordering changed together, so this single comparison does not isolate their individual effects.

Semantic issues remained visible after structural completion. R14 retained optional name-only introductions alongside R07's no-name-collection constraint; whether oral introductions are collected or recorded is unresolved. R20 retained an optional preparation role although R02 says no additional helper will be requested. F04 accepted the added introductions/survey as imposing no new burden, and R08/R19 still state a change-tracking intention without a concrete issue-to-change record. All six effects remain unassessed. The successful handoff makes these tensions inspectable, not automatically corrected.
