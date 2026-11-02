# Breath: replayable reasoning phases

Implementation: `conductor/breath.py`. Requires Duet. License: AGPL-3.0-only.

Breath is a host-owned state machine, not a biological measurement, model emotion,
or meaning score. It records explicit transitions in the same public memory:

```text
receive --start--> expand --candidates--> compare
compare --counterexample--> expand
compare --recompose--> recompose --candidates/recheck--> compare
compare --yield--> yield
nonterminal --hold--> hold --explicit resume--> expand/compare
```

Each transition includes its old/new phase, event, reason and exact public refs.
Comparison retains both outputs of a completed duet round and cannot consume that
same round twice. Content-dependent transitions require evidence. A targeted
recomposition can return through `recheck` with a candidate. These are host
assessments of process, not proof that the content improved.

`open_round`/`request` admit duet generation only during expand/recompose. They
return requests, never call providers. Unresolved reservations must be answered
or explicitly held before changing phase. Hold is not an implicit retry. Yield
is terminal for this phase history and means **yielded unassessed candidates**,
not verified truth, consensus, or successful source alignment.

When source fidelity is required, the caller must still pass candidates through
the existing TextConductor/source-policy gate. This module does not bypass or
replace it. Existing observation-only experiments do not import this controller.
Direct access to the underlying host APIs remains trusted application code, not
a security boundary against a hostile host.

```python
from conductor.breath import BreathController
breath = BreathController(session)
breath.signal("start", "Explore alternatives", expected_revision=session.revision)
breath.open_round("peer_duet", "propose", "Independent readings",
                  expected_revision=session.revision)
request = breath.request()  # no model call
```

## Offline tests

`python -m unittest discover -s tests -p test_breath.py -v`

Checks legal/illegal transitions, evidence requirements, preservation of both
candidates, no repeated comparison, explicit hold/resume, pending-call exclusion,
reopen/replay, targeted recheck and inability of model text to move the phase.
