# Duet-Reflux: correction without erasing alternatives

**Status: DRAFT / NOT RUN. No live execution is authorized by this document.**

Design baseline: reviewed integration commit
`e6fc623ba96d866bd8db985a527e1e89f24934f7` (PRs #6-#9).
The [review report](INTERVENTION_REVIEW_2026_11_03.md) records offline evidence.
The package version remains 0.2.0; this is not a model-result or release claim.

## 1. Question and current instrument

Can returning a declared problem to its actual producer improve correction while
preserving useful independent alternatives, compared with having a fixed writer
revise the shared result?

The first experiment conditions on an explicit, shared finding. It does **not**
test whether a model can discover defects, choose an optimal role, or autonomously
operate the controller. Duet modes, Breath transitions, Reflux plans, condition
assessments and memory selection are currently trusted-host operations.

Implemented: scoped public requests, durable reservations, four duet modes,
evidence-linked phases, producer-targeted returns, critic correction, retained
branches, and selected-memory projection with current condition evidence.
Not implemented for this new lane: a provider runner, experiment-level capture and
request replay, automated finding generation, independent semantic scoring, or a
live intervention CLI. Existing provider runners are not drop-in proof that the
new lane is connected. Reflux creates a new **artifact**, not a token-local edit.

## 2. Entry gates before generation

### G0: offline integration and transport qualification

The current unit/contract suite is the first gate, not the complete experimental
instrument. A subsequent implementation PR must add a new runner without changing
historical observation paths. Before live use it must demonstrate, with scripted
transports:

- exact mapping from DuetRequest to provider payload, without extra private,
  other-branch, future-output or evaluator content;
- durable reservation before dispatch, one response admission, rejection of stale
  replies, explicit refusal/hold and no silent retry after interruption;
- response submission through the correct controller, including Reflux's special
  annotation validation rather than bypassing it through generic Duet.submit;
- frozen source, policy, branch selection and host-decision script;
- raw request/response capture, transport status, usage and monotonic timing,
  with credentials redacted and unknown usage retained as unknown;
- network-free replay of normal, malformed, refused, interrupted and held paths,
  reproducing request hashes and public event history;
- explicit stop/interruption behavior and an operator-approved resource policy.

These runner checks are **required future work**, not capabilities established by
this planning PR. Do not start a real generation or even a credential-dependent
preflight on the strength of a green unit suite alone.

### G1: four sentinel episodes, mechanism only

After a separate explicit execution approval, propose four small cases:
(1) source-condition error with an unrelated valid alternative; (2) an erroneous
critique that needs correction twice; (3) annotation-only error whose original
body must survive; (4) useful disagreement followed by a permission update and a
later revocation. These are disclosed designed fixtures, not naturally sampled
failure rates.

Run one frozen route per case. Record all output text, reference validity, host
transitions, refusals/holds and any failure. Passing requires no protocol breach
and exact replay of what occurred, including failure. Semantic failure is retained
as an observation, not repaired until a favorable outcome appears. This pilot
qualifies the instrument; four cases cannot establish model superiority.

### G2: one preregistered comparison, not the entire feature space

Only after G1 and a separate budget approval: freeze twelve held-out repair tasks
(four source-conflict, four counterexample, four review-mismatch cases), three
paired repetitions and two arms: **72 planned episodes**. This is a proposed
small-sample design, not a power calculation. Do not run it automatically after
G1. Fix task files, rubrics, run order, exclusions and analysis before reading
outcomes. Engineering fixtures and development examples are not held-out tasks.

Use all twelve tasks in the primary report; record outcome totals by task family.
Future scale-up needs an explicit sample-size rationale and a new frozen plan.

## 3. Primary contrast C1: fixed writer versus producer-targeted return

For each task/repetition, freeze a shared starting bundle: original source,
transformation policy, two public candidates, any critique, and the same finding
with exact target/evidence refs. If the starting bundle is model-generated,
capture it once before branching and disclose the shared generation and cost.
Never choose the best draft independently in each arm. A designed faulty draft
is permitted only when labeled as an injected-error condition.

| Arm | Post-finding work |
| --- | --- |
| C1-Fixed | A fixed writer receives the finding and shared bundle, and proposes a replacement working result. A common final writer then produces the handoff. |
| C1-Reflux | The target's recorded producer receives the same finding plus its scoped target/context and proposes a correction. The same final writer combines the correction with retained alternatives. |

Both arms have two post-finding generation opportunities, the same final-writing
brief, the same source policy, the same model pool and the same checking policy.
Refusal or transport/contract failure stops or holds according to the frozen
rules; do not pad failed arms with dummy calls to equalize spend. Record actual
calls and tokens. The final writer may not see the other arm or evaluator labels.

This is a **routing-and-context package** contrast: recipient and read scope may
both differ. It does not isolate routing alone. Counterbalance which model acts
as each participant across task/repetition blocks and retain that assignment in
the manifest. A same-model-two-roles control or a later recipient-by-scope
factorial is needed before attributing a win specifically to heterogeneous
models, producer identity, or context reduction. Do not claim these separately
from C1.

Use one predeclared host decision script, not discretionary mid-run steering.
Give the identical finding/evidence to both arms. Findings may be fixture-supplied
for this first comparison; the resulting claim is explicitly conditional on a
supplied diagnosis. An online detector, with false alarms and its own costs,
requires another experiment. Include misleading/unnecessary findings as a
separate negative-control set before making a detector-robustness claim.

### Primary endpoint

For each planned episode: did the final handoff correct the designated defect
**and** preserve every predeclared unrelated source condition/alternative required
by that task? Report this joint success fraction over all planned episodes,
alongside each component separately. An absent final handoff is not a success;
retain whether it was refused, held, aborted, malformed or a provider failure.
A producer's `uses`/adoption declaration is not a semantic success label.

Artifact immutability is an engineering endpoint. Semantic preservation in the
new handoff must be assessed separately; an untouched stored branch does not
prove that the final writer used or represented it faithfully.

## 4. Separate later contrasts

These are not implicitly authorized or pooled into C1.

| Contrast | What changes | What must stay fixed |
| --- | --- | --- |
| C2: Duet schedule | A fixed lead schedule versus a predeclared lead change after the same evidence event | Models, event, common source, number of turns and final-writing policy; cross over model-to-role assignment |
| C3: decision memory | Selected branch nodes with latest relevant condition/evidence versus a flat public-history presentation | Same primary source and update evidence available to both arms, same subsequent task and generation opportunities |

Peer independence versus sequential handoff also changes information visibility;
label that as a separate contrast, not pure role switching. Breath's transition
correctness is principally checked offline. Comparing a valid controller with an
invalid stale-recheck implementation is not evidence of a cognitive advantage.

For C3, include satisfied, withdrawn, unknown and unrelated-condition cases. The
control must receive the latest update too; giving only one arm new facts would
confound memory organization with evidence availability. Report correct revival
of a held option, inappropriate revival after revocation, and missing rationale
separately. Conditions remain host assessments unless the fixture has an explicit
external answer key. No branch automatically becomes adopted or verified.

Creative exploration belongs to a separate stratum: judge whether distinct useful
relations survive and affect a later continuation. Do not turn number of novel
phrases, annotation counts, output length or agreement into a quality score.
Faithful transfer and creative transformation have different success criteria.

## 5. Evaluation without feedback leakage

Freeze the evaluation rubric before generation. Evaluate final artifacts only
after both arms finish, with arm labels and run order hidden where possible.
Intervention critics/diagnostic models cannot be reused as supposedly independent
evaluators. A different role name is not evaluator independence. If the same model
or provider is used, disclose that dependence and treat its judgments as secondary.

The primary source-based rubric should be applied by a human reviewer blind to
arm labels; an independent second reviewer should cover all primary successes
and disputed cases before a performance claim. Record disagreements and
adjudications without rewriting outputs. Where staffing is unavailable, report
uncalibrated model judgments and examples, not the primary semantic success rate
as established fact.

Do not feed evaluation labels back into generation, routing, memory selection or
retry decisions. Keep the historical emergence observer unchanged. Intervention
feedback is part of the treatment; post-hoc evaluation is not.

## 6. Measurements and denominators

| Dimension | Required report |
| --- | --- |
| Semantic repair | Target correction, collateral preservation, joint success; numerator and all-planned-episode denominator; per-family results |
| Alternative retention | Required alternatives represented in final text, unresolved differences still explicit, any forced-consensus failure |
| Protocol reliability | Reservations, attempts, admitted responses, malformed/refused/held/aborted/transport-failed episodes; no success-only filter |
| Resource use | Actual input/output tokens when returned, request/response bytes, per-call and end-to-end monotonic wall time; unknown usage count |
| Rework | New generated text/tokens, changed artifact IDs, affected branches and scope; do not call artifact targeting token-local recomputation |
| Observation cost | Diagnostic, checking, final evaluation and common-prefix costs reported separately from generation and in an all-in total where known |

Cost units are not money or joules. Use actual configured prices only after
verification and retain the price basis; no energy claim without measurement.
Latency comparisons require counterbalanced arm order and comparable warm-up;
server queuing/provider changes are recorded confounders, not controller speedups.

Analyze paired task-level outcomes. Repeated generations from one task are not
independent new tasks. Report per-task paired differences and uncertainty at the
task-cluster level when appropriate; do not infer general superiority from a
small pilot or a single favorable seed. Include complete-pair analysis only as a
secondary view alongside all scheduled attempts and the failure breakdown.

## 7. Freeze, budgets and provenance

Before authorization, create an immutable run manifest containing:

- exact implementation/runner commits, task/rubric/config hashes and corpus split;
- role-to-model mapping, provider endpoint identity, requested and returned model
  identifiers, decoding settings and seed support; disclose mutable aliases;
- shared-prefix receipt/hash, host decision script, target/evidence read sets,
  arm assignment, randomized/counterbalanced order and planned repetition IDs;
- call/resource budget, price basis when applicable, explicit output/timeout
  settings (including null/unset), stop policy, retention and export policy;
- judge identities, blinding procedure, rubric and prespecified primary analysis.

Call opportunities describe the chosen experimental topology, not a universal
content ceiling. Do not secretly reinstate text/reference/memory caps. Provider
limits and defaults remain real constraints and must be recorded. Truncation is
an outcome, not a complete answer. A separately approved emergency cost/time stop
may abort a run, but may not silently truncate and count it as a valid completion.

Retain raw public outputs and exact links locally, excluding credentials. Export
only reviewed non-sensitive artifacts. Event hashes establish integrity, not
external authenticity. Unknown costs remain unknown. No automatic retries or
outcome-conditioned budget expansion. An explicitly approved rerun receives a new
attempt identity; the earlier failure remains in the report.

## 8. Stop and interpretation rules

Stop for source/policy mutation, unauthorized context projection, duplicate
admission, post-hoc evaluation leakage, unreplayable state, or a requested operator
stop. Preserve partial evidence; fix the runner and use a new protocol version
before resuming collection. A provider failure or semantically poor candidate is
retained, not erased or reclassified as an engineering success.

A successful result would support a narrow claim about the chosen routing/context
package on the frozen tasks and models. An equal, worse or mixed result is equally
reportable. It may show that fixed revision is sufficient, that the producer
anchors on its own error, that added coordination costs dominate, or that selected
memory omits useful context. None of these outcomes establishes or refutes all of
SpiralReality, general emergence, model consciousness, or a universal reasoning OS.

**Next implementation target:** the offline-qualified intervention runner and its
matched C1 capture/replay contract. **Next live action:** none until separately
authorized after the missing runner and evaluation setup are reviewed.
