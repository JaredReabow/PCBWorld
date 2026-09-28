# PLAN — phase 3: corrected integration, honest budgets, real V3 progress

Phase 3 answers the review of phase 2 (a prioritised list of eight findings) and
the measured V3 bottlenecks from that phase's pilot, in one bundle. Phase 1 stays
accepted and phase 2's artefacts stay as they were; the private V3 baseline, the
engine reference build and the user's own board files are untouched.

Milestone: a real V3 connection closing under **full native DRC acceptance**, then
a fair broader pass - not a software-only success.

## Review findings addressed

| # | Finding | Where it is fixed |
|---|---|---|
| 1 | `LayerResolver` sampled the whole board (~12 s) and `_choose_layer` could pick another net's layer; `endpoint()` matched pads by X/Y only; the 0.25 mm heuristic declared real pairs "unsupported" | `observations.LayerResolver.build` reads `KiCadEngine.layer_map` and verifies it on samples (0.01 s); `_anchor_layer` resolves the anchor from the ratsnest layer, with a net-checked fallback for spans-copper anchors; `session.endpoint`/`_human_pad_layer` convert the pad's `PCB_LAYER_ID` and match layer; `COINCIDENT_EPS_MM = 0.02` is a statement about the anchors, not about the engine |
| 2 | Per-net/max-pair caps applied before filtering; one dead pair could stall a run; an empty queue read as "completed" | `scan_net_pairs` returns a `PairScan` (what was offered, what was skipped and why); the queue is round-robin across nets with ordinary connections before degenerate bridges; exhausted pairs are retired with a reason; completion is decided by the engine's own unrouted count |
| 3 | Planner totals reset on resume; the request ceiling could be exceeded inside one retry loop; `reasoning_content` was executed as an answer; the CLI defaulted to a token cap measured as too small | Per-instance request offset; `OpenAICompatiblePlanner.set_budget` enforced before every HTTP attempt; only `content` is an answer; a truncation escalates the output budget once instead of re-sending; CLI default 8000 |
| 4 | No real interrupt/deadline; work continued after an unverified probe | A safety stop (`session_unverified`, `session_quarantined`) outranks any budget; nothing is applied after an unverified probe snapshot; budgets are checked inside retries; the CLI reports and checkpoints instead of hanging past the limit |
| 5 | `_save_best` overwrote the only checkpoint before recording its hash; inferred sidecars were not hashed or copied; the final DRC could regress and still be promoted | Stage → verify (sidecars byte-identical, board non-empty) → atomic promote → manifest; `resolve_inputs` resolves, hashes and copies `<board>.kicad_pro`/`.kicad_dru`; the promoted board is reopened in a fresh engine; a final-DRC regression blocks promotion |
| 6 | Candidate ranking used metrics the session did not populate; the planner could answer about another pair; a stale plan was executed with a fresh token; a 2-coordinate waypoint crashed the record | `added_length_mm`/`vias_added`/`changed_nets`/`steps_applied` come from the transaction's own pre/post geometry; `_bind_planner_request` refuses another tool, another pair or a malformed waypoint; the board digest is captured with the observation and compared after inference; `attempt_plan_key` accepts 2- or 3-value waypoints |
| 7 | `routing_target` was made advisory without a native regression; `DrcGate.seed` could attach an old context to a new key | Native advisory-target regression (`test_integration_gaps`), `unverifiable_properties()` documents it; `seed` re-checks `context_identity` and drops the seed on a mismatch |
| 8 | Reporting implied jitter was harmless by counts alone | RESULT states what was and was not established, and the reopen verification records the promoted board's own DRC total |

## Non-goals

Whole-board completion; relaxing clearances, drills or rules; accepting count
tolerances; claiming a scoped check equals full coverage; publishing private V3
artefacts.

## Status

Implemented; see [RESULT.md](RESULT.md) and [CHECKPOINT.md](CHECKPOINT.md).
