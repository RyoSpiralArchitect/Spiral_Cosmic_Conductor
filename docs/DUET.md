# Duet: explicit two-participant collaboration

Implementation: `conductor/duet.py`. License: AGPL-3.0-only.

This is a separate, opt-in **intervention** API. It does not change the existing
text/transmission/emergence workflows. It does not call a model, discover keys,
download weights, or certify correctness. Public artifacts use the existing
`SharedMemory` and `MultipolarBroker`; no private context is projected.

## Modes

| Mode | Order | Second turn's extra input |
| --- | --- | --- |
| `peer_duet` | bro, lil | None; both use the same frozen starting context |
| `lead_bro` | bro, lil | bro's candidate |
| `lead_lil` | lil, bro | lil's candidate |
| `braid` | Alternate from the last completed duet speaker | First turn's candidate |

`bro`/`lil` are positions in the supplied participant tuple, not model rankings.
A round has two participant turns by definition. Another round requires an
explicit host `start_round`; there is no autonomous loop or fixed round ceiling.
The host chooses the mode, purpose and public context. This is not yet a learned
role-switching policy or an evaluated collaboration improvement.

## Lifecycle (API sketch, not a live-run recipe)

```python
from pathlib import Path
from conductor.duet import DuetSession

with DuetSession(Path("intervention.sqlite"), "episode") as session:
    session.begin("Original source text", {"goal": "Preserve disagreement"})
    session.start_round("peer_duet", "propose", "Independent readings",
                        expected_revision=session.revision)
    request = session.request()  # returns data; no dispatch
    # A caller may later supply a response from a separately authorized worker.
    # session.submit(request, {"text": ..., "uses": [...], "disposition": "candidate"})
```

The response has exactly `text`, `uses`, `disposition`. `uses` contains distinct
exact artifact refs from the request's documents, not arbitrary citation strings.
These are declared relationships, not verified semantic dependencies. A refusal
is retained and holds the round rather than being converted into an answer.

Reservations bind worker, purpose, read set, context, payload and revision.
They are persisted before a future caller dispatches, consumed once, and block
redispatch when unresolved. Reopening an episode never silently retries a call.
`hold_pending` records explicit interruption; a subsequent round is a new host
decision. Generic targeted reservations cannot bypass an active duet's order or
inject the first peer's output into the second peer's read set.

`record` is a **trusted-host extension point** for subsequent phase/routing/memory
modules. Worker responses cannot author control records. This is an application
protocol, not isolation from hostile Python code or authentication of a worker.

Source, policy, requests and outputs are immutable in this lane. New outputs use
new slots. Memory defaults have no application content/event ceilings; optional
`MemoryLimits` are explicit and frozen on reopen. Real provider limits would still
apply, but no provider integration is activated by this change.

## Offline tests

`python -m unittest discover -s tests -p test_duet.py -v`

Covers read-scope independence, leading/braided handoffs, private-context omission,
refusal, one-use/stale/forged tickets, replay, long content, concurrent revisions,
and attempted authority/scope escalation. Scripted strings only; no model run.
