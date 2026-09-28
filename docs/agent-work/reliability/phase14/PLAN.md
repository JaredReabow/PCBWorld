# Phase 14 - refusal-aware ordering, and a budget the opening cannot lose to

Status: in progress (2026-09-28). Phase 13 is the accepted predecessor
([RESULT.md](../phase13/RESULT.md)); this phase's outcome is in
[RESULT.md](RESULT.md).

## What phase 13 left on the table

Phase 13 screened all sixteen cross-layer refusal edges and found measured
openings on four. Its matched trial offered an opening on each of those four, but
**two were never executed**: the two `direct_*` candidates sit ahead of the
family in the generated order, and on that copper each direct transaction costs
about as much as a whole arm budget, so the plan generated *for* the refusal
never got a turn. The screen is cheap; the ordering is what decides whether its
answer is ever used.

## Contract

1. **An opt-in generic ordering policy.** With
   `RunnerConfig.zone_gap_via_priority` on, a pair whose **exact** edge carries
   same-generation prior DRC-refusal evidence has its `zone_gap_via` candidates
   moved ahead of the direct candidates that the same evidence names, before the
   sweep's `candidate_limit` truncation. The move is minimal and deterministic:
   each opening that sits after the first already-refused direct candidate is
   moved to just in front of it, one at a time, so the non-refused direct plan,
   every other family, and the original order inside each group keep their
   positions. Nothing is removed and `candidate_limit` semantics are unchanged.
2. **Evidence, not proximity.** It fires only when the pair's exact edge key
   carries refusal evidence **on the current board digest**; a record measured on
   another generation is not evidence about this copper and is ignored rather
   than inherited. Substitution attempts still bind to the edge they were made
   for, through the recorded offered key.
3. **Clean connections are untouched.** No evidence about the edge, no opening
   candidate, or no evidence naming a direct plan all return the generated list
   unchanged. Other families never enter the path.
4. **A board-generation stamp on the screen.** `screen_zone_openings.py` reports
   the SHA256 of the board (and of the project and rule file when given) instead
   of `null`, because a screen is evidence about exactly one generation.
5. **A trial where the opening cannot lose to the budget.** Matched ON/OFF arms
   on all four openings, fresh engine and session per arm, the same board, rules
   and budgets, each *candidate* with its own wall-clock allowance so an offered
   opening is either executed or explicitly reported as an untested bounded
   timeout. Zero planner requests.
6. **Promotion only through the immutable store** and only if something closes:
   fresh child, whole-board native DRC, terminal partition, complete pinned CLI
   against the canonical original, no lost terminal connections. Otherwise the
   accepted pointer stays put.
7. **No engine or wire changes**, no footprint moves, no zone deletion, no rule
   relaxation, no commit, stage or push. Public docs stay free of board
   coordinates and rule values.

## Evidence

* Policy: `pcb_world/agent/runner.py` (`zone_gap_via_priority`,
  `_refusal_evidence`, `_refusal_ranked`).
* Screen provenance: `tools/reliability/screen_zone_openings.py`
  (`sha256_file`, `board_sha256`/`project_sha256`/`rules_sha256`).
* Tests: `tests/agent/test_zone_prefilter_unit.py` (exact edge, stale
  generation, no refusal, unseen plan, substitution, truncation, other
  families, default off) and `tests/agent/test_zone_point_query.py` (the screen
  tool's provenance end to end).
* Trial: private `phase14_routing/phase14_trial.py`.
* Phase gate: `bash tools/reliability/check_phase.sh --strict`; patch proof:
  `python tools/reliability/check_engine_patches.py`.
