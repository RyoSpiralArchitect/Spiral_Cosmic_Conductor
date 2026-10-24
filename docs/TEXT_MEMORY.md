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

The host joins output units with newlines and binds source, draft, and output spans to their exact artifact versions and hashes. Spans store offsets, a quote hash, and at most 120 preview characters; full quotations are resolved from scoped historical artifacts, avoiding repeated copies of long text. Raw provider responses are preserved separately. A revision unit may be a paragraph; its ID is not proof that it is exactly one linguistic sentence.

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

A separately planned follow-up can explicitly fork a **sealed source/draft-only capture** with --resume-from and a matching one-case --cases file. It accepts no partial review, checks the original manifest and episode contract, preserves the original files, and records the imported predecessor separately from its at-most-two new calls. This is an explicit new experiment, not an automatic retry or a continuation that hides failed calls. Replay carries its own frozen seed database and does not need to read the original capture path.

This pilot changes guidance, prior-answer exposure, output protocol, and state handling together. It tests an executable mechanism and supplies concrete outputs for inspection. It does not isolate the causal effect of abstraction, numeric components, or persistence. The second case is a new synthetic probe, not a statistically independent generalization benchmark.

## Files and checks

- conductor/shared_memory.py: durable artifact contract and bounded views.
- conductor/text_workflow.py: abstract teacher, role inputs, response validation, and trace construction.
- experiments/text_memory_pilot.py: opt-in transport, budgeting, capture, and exact offline replay.
- tests/test_shared_memory.py, tests/test_text_workflow.py, tests/test_text_memory_pilot.py: stale/concurrent writes, scope and episode separation, atomicity, reference validity, interruption receipts, and failure replay.

API responses remain candidates. The deterministic claim ledger and its verification semantics are unchanged.
