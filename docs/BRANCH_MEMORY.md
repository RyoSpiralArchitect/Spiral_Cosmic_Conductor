# Narrative branching memory

Implementation: `conductor/branch_memory.py`. Requires the preceding intervention
stack. License: AGPL-3.0-only.

This adds decision history to the existing public-memory store, not a second
SQLite store, a compatible `.nar` filesystem, a vector database, or private-memory
merging. It keeps questions, hypotheses, evidence, counterexamples and decisions
with explicit content refs, rationale, disposition and optional reopening
conditions. `adopted` is a host decision for a task, never a verified truth label.

## Operations

- `remember` creates an immutable, evidence-linked node. `attach` places an
  existing node on a branch. These are two explicit transactions; an unattached
  node is retained if attachment is not performed or fails.
- `fork` copies references to a frozen branch head. Subsequent changes do not
  change the parent or sibling.
- `rewind` appends a new head pointing at a same-branch historical state; it does
  not delete old nodes, decisions or events.
- `merge` retains the union of selected branches' nodes and dispositions. It
  never turns contradictory/held/rejected nodes into a consensus or acceptance.
- `condition` records an evidence-linked host assessment. When every named
  reopening condition is currently satisfied, `reconsider` returns old held or
  rejected nodes as **candidates for reconsideration**. No automatic adoption,
  dispatch, verification or branch mutation follows.
- `search` is untruncated lexical retrieval scoped to one branch with optional
  kind/disposition filters, not semantic similarity or relevance proof.

Source text and actual model-output artifacts can support a node. Other narrative
summaries or control notes are not accepted as primary content evidence. Nodes
remain assessments, and condition satisfaction is not automatically inferred.

## Integration

`fork_from_reflux` consumes a productive-difference plan once and requires its
target output to appear in the parent branch. No repair call is reserved.

`open_round(branch, selected_nodes, ...)` projects the explicitly selected nodes,
their primary content refs, and the latest public assessments/evidence of their
named `reopen_on` conditions into a new Breath/Duet round. Unknown/unsatisfied
revocations are included as such. Only selected nodes' conditions are added;
unrelated conditions, superseded assessments, unselected nodes and whole branches
are not automatically sent. A host can explicitly select a held node, but this
does not change its disposition or assert that its conditions are satisfied.

Same-episode public refs are required. Cross-episode retrieval/import is
intentionally not implemented. The convenience import `conductor.intervention`
exposes the four APIs without activating existing CLI commands or observation
experiments.

## Offline tests

```bash
python -m unittest discover -s tests -p 'test_branch_memory*.py' -v
```

Includes a network-disabled integration test: independent duet -> comparison ->
productive-difference Reflux -> retained fork -> selected-memory projection ->
changed leader -> explicit hold. Other tests cover reopen, stale writes,
non-destructive rewind, conflict-preserving merge, scope separation, condition
revocation, no summary-as-primary-evidence laundering, and transmission of the
current reopening evidence without unrelated conditions. No real generation.

The [focused review](INTERVENTION_REVIEW_2026_11_03.md) records the original missing
condition-projection defect and its failing-before/passing-after regression
checks. See the [next experiment plan](INTERVENTION_EXPERIMENT_PLAN.md) for the
separate, not-yet-run semantic evaluation.
