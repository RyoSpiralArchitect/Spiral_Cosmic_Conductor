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

`test_recorded_live_exchanges_replay_with_identical_requests` checks the entire
request body against each recorded SHA-256 before returning its captured response.
All 8 executions must reproduce their completion status, call count, results,
patch rejections, and dependency invalidations through the real ledger and router.
This also verifies that moving the corrected prompt into the shared builder
preserves the request bytes observed in the 28-call corrected pilot. These tests
are offline replays of earlier evidence, not fresh live generations. The injected
case re-applies the explicit host fault while preserving the correct raw response.

The other tests exercise loopback HTTP, simulated provider transports, bounded
calls, malformed responses, credential preflight, and literal-only shell-variable
import. A simulation uses a scripted fixture interpreter and cannot establish
model quality. No test contacts a public provider.

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
- [Mistral JSON mode](https://docs.mistral.ai/studio/conversations/structured-output/json_mode)
- [Gemini OpenAI compatibility](https://ai.google.dev/gemini-api/docs/openai)
