# Provider wiring repair and observation pilot

This is an experimental adapter around the existing `run_cosmic` worker contract.
The application CLI still defaults to scripted or explicitly configured loopback workers.

The harness uses existing environment credentials for OpenAI, Mistral, and Gemini.
It sends only the supplied synthetic task view and patch contract to fixed provider
HTTPS endpoints. It never sends ledger authority tickets, executes returned tools,
follows redirects, or retries HTTP requests. Credentials are not saved to artifacts.

```sh
python3 -I -S -B experiments/provider_pilot.py --mode simulated --out runs/provider-simulation
python3 -I -S -B experiments/provider_pilot.py --mode live --out runs/provider-live
```

Live mode is a deliberate opt-in and may incur provider charges. Set the existing
`OPENAI_API_KEY`, `MISTRAL_API_KEY`, and `GEMINI_API_KEY` in the process environment.
For an explicitly authorized local shell configuration, `--credentials-from-zshrc`
imports only literal assignments for these names; it never sources shell code.

The fixed pilot contains three natural runs, rotating the initial proposer through
Mistral Small 2603, GPT-4.1 mini 2025-04-14, and Gemini 2.5 Flash. A fourth run replaces
the first correct Mistral `workers=4` proposal with the host-authored value `3`.
The exact original model content is retained beside the injected patch. This is
fault injection, not an observed Mistral factual error. Models may still make
unmodified protocol errors in any run.

Each run permits at most 9 calls, 1,024 reserved output tokens per call, two attempts
per claim, 25 seconds per call, and 180 seconds overall. The pilot has a 36-call cap.
The controller can route a failed proposal to the other configured provider; that
is a separately recorded call, not an invisible transport retry. `cost_units` are
abstract routing units, not provider prices or measured model quality.

## What the first observation found

On 2026-10-10, the original prompt completed 0/4 runs in 17 calls. It frequently
returned the correct numeric value with `revise_claim` for a missing claim. The
ledger rejected those operations with `claim_does_not_exist`.

A second, separate 28-call pilot added `target_contract`: the required operation,
claim ID, and source-reference list, derived from the existing dispatch state.
It supplied no expected answer and did not normalize or rewrite the returned
operation. All 4 runs completed. OpenAI still proposed dependency claim IDs as
source evidence once; the ledger rejected it and Gemini completed that slot.
The injected condition invalidated only `workers`, `capacity`, and `can_deliver`;
OpenAI repaired them. The independent site branch was retained.

| Case (initial proposer → repair worker) | Original prompt | Explicit dispatch contract |
|---|---|---|
| Mistral → OpenAI | Incomplete, 2 calls | Complete, 6 calls |
| OpenAI → Gemini | Incomplete, 9 calls | Complete, 7 calls |
| Gemini → Mistral | Incomplete, 4 calls | Complete, 6 calls |
| Mistral → OpenAI, host fault injection | Incomplete, 2 calls | Complete, 9 calls |

The shared `conductor.adapters.worker_messages` builder now supplies that same
contract by default to both the local chat adapter and this harness. It chooses
`add_claim` for `missing`, `revise_claim` for existing or invalidated claims,
`[slot.source]` for fact evidence, and `[]` for derived evidence. It neither
computes the answer nor alters returned operations. Unsupported multi-target or
critic dispatches are rejected before transport; the controller uses single-target
proposer and repairer dispatches. Ledger grants and validation remain authoritative.

Use `--legacy-dispatch-contract` to reproduce the original prompt condition.
Keep each execution in a fresh directory. Model nondeterminism means the same
counts are not guaranteed on a new run.

## Offline regression evidence

`tests/fixtures/provider_pilot_2026-10-10.json` retains all 45 returned JSON texts
from the two original runs, provider token counts, request hashes, expected
outcomes, and hashes of the original captures. It excludes credentials, provider
response IDs, local paths, and ledger authority tickets. The source captures were
checked against their original integrity manifests before extracting this fixture.

`test_recorded_live_exchanges_replay_with_historical_request_hashes` reconstructs
the historical message encoding (without the newer value specification and failure
feedback, using the original `json_object` response format) and checks it against
each recorded request SHA-256 before returning its captured response.
All 8 executions must reproduce their completion status, call count, results,
patch rejections, and dependency invalidations through the real ledger and router.
The current prompt contains additional repair metadata, so these hashes describe
the historical prompt condition, not byte identity with today's live requests.
These tests are offline replays of earlier evidence, not fresh live generations. The injected
case re-applies the explicit host fault while preserving the correct raw response.

The other tests exercise loopback HTTP, simulated provider transports, bounded
calls, malformed responses, credential preflight, and literal-only shell-variable
import. A simulation uses a scripted fixture interpreter and cannot establish
model quality. No test contacts a public provider.

## Post-merge repair qualification (2026-10-13)

A fresh pilot on merged commit `ad1b8f3` completed only 2/4 cases in 29 calls,
although all 83 tests and CI passed. Mistral omitted a fact's evidence ID, and
OpenAI changed the correct demand value while repairing it. In the injected case,
Mistral also confused per-worker capacity with total capacity. These value errors
were detected by the verifier, and the cases stopped at their existing limits.

The injected case exposed a host inconsistency: the ledger accepted `current`
as evidence for derived `capacity`, but capsule export rejected it. The exception
left the pilot summary at `running` / 20 calls and prevented final manifest creation.
Offline replay of all 29 responses reproduced the same exception on the merged source.

The follow-up repair makes these boundaries explicit:

- `Slot.evidence_ids` defines permitted references for the target slot. The ledger
  rejects missing fact references or references outside that target before commit;
  dependency source visibility does not authorize evidence for a derived slot.
- `target_contract.value_spec` supplies a fact source/field pointer or a derived
  operation/dependency list, without supplying the expected answer. Scoped host
  feedback identifies the last rejection or failed verification and its root claim.
  A later committed proposal supersedes old feedback.
- A pilot exception or interruption finalizes the actual reserved call count,
  `aborted` status, affected case, and partial integrity manifest before propagating
  the error. Exception text is omitted. Output storage must remain writable; abrupt
  process termination such as SIGKILL cannot execute this finalization.

A first live qualification of these changes completed 3/4 cases in 27 calls.
Artifact export succeeded for all four, including the incomplete injected case.
However, OpenAI still returned duplicate evidence IDs once and an unauthorized
fact-source ID for a derived repair once. Both outputs were rejected without
normalization; the latter exhausted the target's two attempts.

The default cloud pilot therefore also sends a per-dispatch `json_schema` response
format with `strict: true`. The schema allows exactly one operation and fixes the
operation, target ID, and evidence list to the existing dispatch contract. It leaves
the integer/boolean/string value and brief reason to the model. It does not encode
an expected answer, compute one, or rewrite responses. The local chat adapter keeps
its existing JSON-mode setting because local-server schema support is not assumed.
The ledger still checks every response, and provider rejection/refusal/truncation
is recorded without fallback or transport retry. The plan records the response format.

A separate live qualification on `c3661ed` accepted that schema on all three pinned
models and completed 4/4 cases in 27 calls, with zero HTTP/parse failures and zero
patch rejections. The ordinary cases took 6 calls each. The injected case took 9:
Mistral's initial candidates used the injected workers=3, yielding capacity=225 and
can_deliver=false; verification invalidated those three linked claims, and OpenAI
repaired them to 4, 300, and true. No extra attempts or budget were added.

| Case | Target-evidence/feedback repair, JSON mode (`8f55ba8`) | Same repair with response schema (`c3661ed`) |
|---|---|---|
| Mistral → OpenAI | Complete, 6 calls | Complete, 6 calls |
| OpenAI → Gemini | Complete, 7 calls | Complete, 6 calls |
| Gemini → Mistral | Complete, 6 calls | Complete, 6 calls |
| Mistral → OpenAI, host fault injection | Incomplete, 8 calls | Complete, 9 calls |

All completed cases verified `workers=4`, `per_worker=75`, `demand=240`,
`capacity=300`, `can_deliver=true`, and `site_name="Beppu"`. Models generated JSON
patches and short reasons; the host's final text rendered only the verified target
values (`can_deliver` and `site_name`). These runs exercised shared-state proposals,
deterministic verification, dependency invalidation, and provider routing.

Both new live captures were replayed offline against their matching frozen source:
all 54 request bodies and all 8 final claim states matched, including the failed
response and incomplete case. All 8 ledger audits and artifact manifests passed.
Exchange token totals include the failed response: the first condition used 25,451
input / 1,611 output tokens; the schema condition used 26,655 / 1,432. These are
provider-reported counts, not prices; runtime reply totals omit the failed response.
One pilot per condition does not estimate a completion rate or isolate a causal
model-quality effect. The unchanged per-claim and per-run limits can still stop
future runs that produce wrong values or unavailable responses.

`provider_failures_2026-10-13.json` preserves four actual responses from the failed
pilot. Regression tests check early rejection, unchanged bad output, scoped repair
instructions, independent value verification, and successful artifact export even
when a recorded bad repair leaves the task incomplete. Failed historical runs are
not relabeled successful by these counterfactual tests.

These are observations on one closed-world arithmetic fixture, not evidence of
open-ended reasoning quality, a provider ranking, general safety, or benchmark gains.
The program already knows how to verify each operation. Accepted values are only
consistent with the supplied task facts. Three-provider debate, a model-authored
final essay, and automatic semantic task decomposition are not implemented here.

Each output contains a plan and code hashes, raw generated JSON text, applied
patches, usage, ledger events, per-run replay receipts, and integrity manifests.
Inspect both the original and revised conditions; do not relabel the failed first
pilot as successful. The `NOT_ASSESSED` performance field in core receipts is
intentional: the experiment exercises real providers without assessing general
model performance.

Protocol references consulted for this pilot:

- [OpenAI Chat Completions](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create)
- [OpenAI Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs)
- [Mistral custom Structured Outputs](https://docs.mistral.ai/studio/conversations/structured-output/custom)
- [Gemini OpenAI compatibility](https://ai.google.dev/gemini-api/docs/openai)
