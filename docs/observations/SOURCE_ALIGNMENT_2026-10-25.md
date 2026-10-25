# Live source-comparison observation, 2026-10-25

The source-check → repair → source-recheck pipeline returned all three provider responses, but the result was **held**, not released. The repairer declared two issues addressed while returning a byte-identical candidate. The recheck exceeded the host's 80-character reason bound, so it was rejected without publishing an assessment or result text. This is evidence of bounded control and an observable failure, not semantic improvement.

The [public receipt](../../examples/observations/source-alignment-2026-10-25.json) contains the original synthetic memo, both candidates, all three returned response envelopes, accepted assessment, repair trace, final report, usage, and capture hashes. It is an observation, not a teacher or gold annotation. Runtime commit: `5462a1e3260269f11fc78461fbef22f7e8798ad8`.

## Scope and accounting

The input was the completed 22-unit, 1,300-character candidate from the previous text-memory live run. Its original memo has ten source units. Both were imported from a sealed checkpoint; neither the old critique nor the manual simulation annotations entered the auditor packet. Source preservation, one repair, three calls per episode, 60 seconds per call, and 8,192 output tokens per call were frozen before dispatch.

| Phase | API attempts | Outcome |
|---|---:|---|
| Initial conductor | 1 | Gemini HTTP error; held before any assessment |
| Explicit diagnostic calls using the same request bytes | 2 | HTTP 400; the second retained the list-shaped error body and exposed schema complexity |
| Repaired wire schema | 3 | Gemini check, OpenAI repair, Gemini recheck all returned; host rejected the last assessment |
| Offline replays | 0 | Initial failure and repaired trajectory reproduce exactly |

There were six new API attempts, with no automatic retries. Together with eight earlier text-memory attempts, the cumulative total is fourteen. Usage is known for the three returned responses only: 8,485 prompt tokens and 4,872 completion tokens. Usage for the three pre-fix failures is unknown, not zero.

The first implementation (`f6132be`) sent outer row-count constraints alongside bounded nested reference arrays. Gemini rejected that schema as producing too many decoder states. The fix omits only outer row-array counts in Gemini's wire schema; ID enums, nested reference limits, and exact host coverage remain. HTTP status and a fixed error category are now replayable without saving arbitrary error text. Google's [schema complexity documentation](https://ai.google.dev/gemini-api/docs/structured-output#limitations) describes this failure class.

## What the models did

| Step | Observed behavior |
|---|---|
| First Gemini comparison | Covered 10 source and 22 candidate units. Host blockers were M01 and M05. All candidate units were rated compatible. |
| OpenAI repair | Declared both blockers addressed and cited C01/C05/C11/C21. The complete candidate's bytes and SHA-256 remained unchanged. |
| Second Gemini comparison | Returned complete row coverage, but three reasons had 98, 84, and 85 characters against the 80-character bound. The host rejected the entire assessment. |
| Final conductor state | `held`, three calls charged, `result_text=null`, no `check_1` publication. Candidate and repair trace remain in history. |

The final report has no current valid assessment, so its empty blocker list must not be read as “no semantic issues.” The earlier accepted assessment and the rejected raw response are preserved separately.

## Operator interpretation, separate from model output

The auditor now labeled the optional introduction, survey, and preparation-role units as additions. However, it rated them compatible and did not flag the unresolved name-introduction or extra-helper ambiguity. Those ambiguities require reading multiple units together: C07 prohibits collecting names while C14 mentions an optional name introduction; C02 rules out extra help while C20 introduces an optional preparation role without specifying who holds it. Oral introduction is not necessarily collection, and the facilitator might hold both roles, so these are unresolved readings rather than demonstrated violations.

The first comparison treated the rephrasing of “45 minutes only” as `changed`, while its own candidate-side explanation said the meaning was retained. It also treated a scheduling elaboration as a source change. The recheck added further `changed` labels for paraphrases. This shows that the detector's status calibration needs separate evaluation; a conservative host gate does not make those judgments accurate.

The repairer's `addressed` declarations did not describe actual changes. The conductor still performed the required independent recheck. It ultimately held on a formatting-contract violation, not because it successfully detected the substantive ambiguities. The text, guidance, and limits were not tuned and rerun until a passing result appeared.

These observations qualify the orchestration mechanism and identify detector/repair limitations. They do not establish precision, recall, a causal benefit from the new stage, or general capability. All free-text artifacts remain `semantic_status=unassessed`.
