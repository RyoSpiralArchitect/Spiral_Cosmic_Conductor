# Focused intervention review: 2026-11-03

## Scope and result

PRs [#6](https://github.com/RyoSpiralArchitect/Spiral_Cosmic_Conductor/pull/6),
[#7](https://github.com/RyoSpiralArchitect/Spiral_Cosmic_Conductor/pull/7),
[#8](https://github.com/RyoSpiralArchitect/Spiral_Cosmic_Conductor/pull/8) and
[#9](https://github.com/RyoSpiralArchitect/Spiral_Cosmic_Conductor/pull/9) were
reviewed and merged bottom-up using normal merges. The integrated code baseline
is `e6fc623ba96d866bd8db985a527e1e89f24934f7`.

This was an assistant-authored focused adversarial **self-review**, not an
independent security audit or exhaustive verification. The checks target
untrusted replies, legitimate host API sequences, stale state, repeated
correction, and missing context under the documented trusted-host boundary.
Direct malicious Python with access to the store is outside that boundary.

Nineteen new regression cases were added: 8 Duet, 4 Breath, 3 Reflux and 4 memory.
Four process/data-flow defects were fixed, with failing tests observed before
the fixes. No live model/provider generation, model download or real credential
inspection was performed during this review. Tests ran in GitHub Actions against
the repository and its PR merge trees; this report does not claim a new local
checkout test run.

## Findings and fixes

### D: Duet protocol checks, no blocking defect found

Added [eight tests](../tests/test_duet_review.py) for cross-episode ticket reuse,
first-peer reply-receipt leakage, malformed-response atomicity, forged visible
reference digests, held-ticket replay after reopening, concurrent one-use
admission, altered payloads and refusal persistence. All passed without a
production code change. This supports those cases only, not general sandboxing.

Reviewed feature head: `37e6a152d7460166baa40fd3490038bf0d15a59b`.
Merge: `01196e1473c005b2aee08aed999153c7225a44e8`.
Green [CI 41557928086](https://github.com/RyoSpiralArchitect/Spiral_Cosmic_Conductor/actions/runs/41557928086).

### B1: incomplete duet could escape to comparison

Between the two turns, no request was pending, but the round was still active.
A host could call `recheck` or `hold -> resume_compare` and bypass the second
participant. Breath now prevents an active round from leaving generation except
to hold/resume generation. It does not silently manufacture a second answer.

### B2: recheck could reuse an old proposal

`recheck` previously accepted any proposal ref, including one from before the
current recomposition. It now requires a successful non-duet candidate whose
request was reserved after entering the current recomposition. Entering another
recomposition cannot recycle a previous correction as newly performed work.

The [four Breath regressions](../tests/test_breath_review.py) all failed before
the fix in [CI 41557974706](https://github.com/RyoSpiralArchitect/Spiral_Cosmic_Conductor/actions/runs/41557974706).
Fix head: `c06dec441b42b05210c56b2770487e9c7b41293b`.
Merge: `3840bc618d33b5da57444bf2624b256ff07647f7`.
Green [CI 41558194961](https://github.com/RyoSpiralArchitect/Spiral_Cosmic_Conductor/actions/runs/41558194961).

### R: corrected critic lost its reviewing-role identity

A first critic correction was recorded with purpose `revise_review`, which was
not allowed as the target of another `review_mismatch`. Reflux now explicitly
retains that purpose in `REVIEW_PURPOSES`. Ordinary `revise_example` and other
nonreview outputs do not gain reviewer status. The target is still bound to its
actual author and request receipt.

The [three Reflux regressions](../tests/test_reflux_review.py) include the
repeated-critic failure reproduced in [CI 41558242127](https://github.com/RyoSpiralArchitect/Spiral_Cosmic_Conductor/actions/runs/41558242127),
a negative role check, and consumed-plan replay rejection.
Fix head: `1b3e6e61b42ed75f71b21a3fafe2cbbe8dd6094a`.
Merge: `30b9393fabe75ebcab169bb2dc6bbc2dc6c993f5`.
Green [CI 41558453954](https://github.com/RyoSpiralArchitect/Spiral_Cosmic_Conductor/actions/runs/41558453954).

### N: reopened decision lost the reason for reopening at handoff

`reconsider()` could find a held node after its condition changed, but
`open_round()` sent only that node and its original evidence. The next model
would not see the current condition assessment or the new evidence.

Selected nodes now bring the latest assessments of their named `reopen_on`
conditions and the corresponding primary evidence. Unknown/unsatisfied revocations
are included as such. Unrelated conditions and superseded satisfied evidence are
not automatically projected. The node's disposition stays unchanged; the host
can explicitly select a held node without pretending the condition is true.

The [four memory regressions](../tests/test_branch_memory_review.py) include three
projection failures observed in [CI 41558327815](https://github.com/RyoSpiralArchitect/Spiral_Cosmic_Conductor/actions/runs/41558327815)
and a read-only check. That red run also contained the inherited Reflux error;
it must not be counted as a fourth memory defect.
Fix: `0a95ab28f16f1f7f061a05bb3aa878e5302d69b5`.
Reviewed-main synchronization: `79039328f88ab25961f28864f2ebfbe311d18277`.
Final merge: `e6fc623ba96d866bd8db985a527e1e89f24934f7`.

## Integrated verification

[CI 41558928212](https://github.com/RyoSpiralArchitect/Spiral_Cosmic_Conductor/actions/runs/41558928212)
passed the Python 3.10, 3.12 and 3.13 matrix on the integrated PR9 candidate.
The 3.13 log explicitly reports **261 tests, OK**. Its checked merge ref was
`69931f69896706739e8b40c6f5a112e313220536`, merging head `7903932` into reviewed
main `30b9393`. The integrated source tree is
`fd8172381b7cd4346e6f87d6c48ddc55bead1de1`.

The existing CI also passed scripted structured demos and bundle audit, public
text-memory simulation/replay, source-check simulation/replay, faithful/creative
transmission simulation/replay, and the emergence observation simulation/replay.
These are offline fixtures and captured-response replay, not new model runs.

The final PR9 integration was checked as reviewed main plus exactly its six
added files; it did not overwrite upstream Breath/Reflux fixes. Normal merges
preserved feature and review history. Historical experiment code, configurations
and captured observations were not rewritten by the intervention review.

## Remaining boundaries

- The host still supplies mode changes, diagnoses, condition assessments and
  context selection. No automatic semantic controller has been evaluated.
- Reflux targets an artifact/producer; it does not guarantee a minimal text edit.
- A stored branch surviving unchanged does not prove semantic preservation in a
  later handoff. `adopted`, `satisfied` and `yield` do not mean verified truth.
- Post-hoc-observer origin labels reject the wrong lane but cannot prevent a
  malicious host from relabeling data. Existing observers remain unconnected.
- The new lane still needs a reviewed provider/capture/replay runner before a
  real-model experiment. Existing source-fidelity gates remain separate.

See the [next experiment plan](INTERVENTION_EXPERIMENT_PLAN.md). This review and
its green CI are not authorization to run that plan.
