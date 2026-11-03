# Reflux: return a declared issue to its actual producer

Implementation: `conductor/reflux.py`. Requires Duet and Breath.
License: AGPL-3.0-only. This is an intervention path, not an observer.

| Declared finding | Host routing action |
| --- | --- |
| `annotation_error` | Annotation-only response to the target author; retain body |
| `source_conflict` | Reconsider that author's source interpretation |
| `counterexample` | Reconsider that author's concrete proposal |
| `review_mismatch` | Return to the actual reviewing role, not the original writer |
| `productive_difference` | Preserve through a branch, not an automatic repair call |

The recipient comes from the target's persisted author/request receipt, never a
model-selected recipient. Targets must be actual participant outputs, not frozen
source/policy or arbitrary controller records. Findings need exact public target
and evidence refs, a registered reporter and a rationale. Category and rationale
remain declared assessments: routing is deterministic; semantic diagnosis is not.

## Explicit lifecycle

1. In compare, the host `propose(Finding(...))` records a plan.
2. The host changes Breath to recompose and `request(plan)` reserves one return.
3. `submit` publishes a **new** unassessed candidate; originals remain intact.
4. The candidate goes back through comparison/source checks. Submission does not
   assert resolution. `report` explicitly retains `requires_recheck`.

Annotation-only candidates must be JSON with the exact original `target` ref and
nonempty string `notes`. No replacement of the original body occurs. This checks
structure, not whether the annotations are semantically correct.

A plan is reserved at most once, including refused or interrupted calls. A later
attempt requires another explicit host plan. A productive-difference plan is
consumed by the separate branching-memory mechanism without a repair-model call.

Explicit `origin_lane != "intervention"` findings are rejected. Existing post-hoc
observers are untouched and never wired here. This provenance label is a trusted
host contract, not a defense against a host relabeling observer text. Feedback
used during generation must not subsequently be presented as independent eval.

## Offline tests

`python -m unittest discover -s tests -p test_reflux.py -v`

Tests target ownership, critic correction, annotation-only invariance, explicit
phase changes, old/independent output retention, refusals, stale/forged plans,
reopen with pending calls, one-use routing and observation-lane exclusion.
No live provider integration or performance claim is included.
