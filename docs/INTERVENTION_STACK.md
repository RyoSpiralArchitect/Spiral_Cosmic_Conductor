# Duet / Breath / Reflux / branching memory

The four intervention PRs (#6-#9) are merged. The reviewed code baseline is
`e6fc623ba96d866bd8db985a527e1e89f24934f7`. This is an opt-in Python library;
existing structured reasoning, transmission comparison and observation-only
emergence experiments remain unchanged. InfiniteRoom remains excluded.
License: AGPL-3.0-only.

| API | Role | Contract |
| --- | --- | --- |
| `DuetSession` | Separate participant candidates | Independent peer or explicit leading/braided views; immutable public artifacts and one-use reservations |
| `BreathController` | Thinking phases | Host-owned transitions, both participants retained, no incomplete-round escape or old-output recheck |
| `RefluxController` / `Finding` | Return a declared issue to its producer | Actual author/receipt binding, repeatable critic correction, separate annotation repair, no automatic semantic diagnosis |
| `BranchMemory` | Retained decision paths | Non-destructive fork/rewind/merge, explicit reopening conditions, selected public context |

```python
from conductor.intervention import (
    DuetSession, BreathController, RefluxController, Finding, BranchMemory,
)
```

Importing this module does not create a database or dispatch a model. See the
[README's scripted example](../README.md) and the individual guides:
[Duet](DUET.md), [Breath](BREATH.md), [Reflux](REFLUX.md),
[branching memory](BRANCH_MEMORY.md).

```text
frozen source + host policy
  -> independent/leading/braided candidates
  -> explicit comparison
  -> declared finding + host return plan
  -> new candidate or retained alternative branch
  -> selected decision nodes + current reopening evidence
  -> next explicit round, hold, or yield unassessed candidates
```

## Reviewed invariants

A gap between two duet turns is not a completed round. Hold may retain an
unfinished round, but resume-to-comparison cannot skip its other participant.
A targeted recheck must refer to an output reserved during the current
recomposition, not a reused older candidate.

A `revise_review` output remains eligible for another review-mismatch correction;
ordinary revisions do not acquire reviewer status. Findings and corrections are
still unassessed. An annotation correction does not replace the original body.

Selected decision memory includes the latest public assessments of its named
reopening conditions and their content evidence, including unknown/unsatisfied
revocations. Unrelated conditions and unselected branch content are not silently
projected. Reconsideration produces candidates, never automatic adoption.

## What remains separate

There is no new live intervention CLI or automatic policy. The host chooses
modes, findings, transitions and memory selections. A future runner must reserve,
dispatch and submit through the correct owning API, and qualify capture/replay
before live use. Direct hostile Python is outside the application boundary.

No post-hoc observer output is wired into generation. New `yield` results are
unassessed, not source-checked release. Tasks requiring source fidelity still
need the existing source-comparison gate. Hashes/replay establish local integrity,
not authenticated authorship. No benchmark, calibrated meaning/attention measure,
EBRT gradient execution or SpiralTorch GPU execution is claimed.

## Evidence and next work

The [focused self-review](INTERVENTION_REVIEW_2026_11_03.md) documents four fixed
defects, nineteen added regressions, and the 261-test integrated CI baseline on
Python 3.10/3.12/3.13. Historical simulations/replays also passed. This was not an
independent security audit and involved no new live generation.

The [next experiment plan](INTERVENTION_EXPERIMENT_PLAN.md) separates offline
runner qualification, a small mechanism pilot, and a matched correction study.
It is DRAFT / NOT RUN and is not execution authorization.
