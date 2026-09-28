# Phase 9 — result

Status: **ready for review.** Nothing promoted; the accepted pointer is unchanged.
Contract: [PLAN.md](PLAN.md). Aggregate taxonomy: [TAXONOMY.md](TAXONOMY.md).

## What changed

Three files in the harness, all general:

| file | change |
|---|---|
| `tools/reliability/failure_taxonomy.py` | new — hash-bound taxonomy of every outstanding connection |
| `pcb_world/agent/scheduler.py` | `clearance_search_probes` + the `drc_clear` family; refusal-derived candidates ordered ahead of the contextual families; `AttemptHistory.records_on` / `worked_on` |
| `pcb_world/agent/observations.py` | `scan_net_pairs(..., fresh=...)` coverage pass + `coverage_promoted` |
| `pcb_world/agent/runner.py` | `RunnerConfig.coverage_first`, `_next_pair` ranking, `_refusal_unanswered` selection |

No footprint moved, no protected copper shrank, no rule was relaxed and no gate
was substituted. `patches/engine/` is byte-identical to the pin.

## The taxonomy

135 outstanding edges on board `6c4f8ab8…` (digest `7af2e79c66dd27e8`), each
classified exactly once, totals summing to 135. 639 disconnected component pairs
on the same board — the edge count and the work count are different numbers and
are reported separately.

As found: 35 `drc_regression` (a plan closed the connection and the native gate
refused the copper), 42 `connection_not_verified`, 26
`copper_absent_at_offered_anchor`, 6 `unnamed_net_at_offered_anchor`, 26
`unattempted_cap_observed`. **79 attempted, 56 not.**

After this phase's segments: 43 / 49 / 26 / 6 / 11 — **94 attempted, 41 not.**
The only category that moved is the one coverage could move.

## Campaign

`phase8_route.py` from the accepted pointer, zero planner requests, prior history
from the complete same-generation record set, `--incremental-drc`.

| segment | routing time | stop reason | new records | distinct pairs | closures |
|---|---:|---|---:|---:|---:|
| `run_cov2` | 1 318.2 s | `time_limit_headroom` | 596 | 116 | 0 |
| `run_cov3` | 461.3 s | `time_limit_headroom` | 96 | 16 | 0 |
| **campaign** | **1 779.5 s** | | **692** | **132** | **0** |

Two further runs are reported separately because they are not campaign segments:

| run | routing time | what it is |
|---|---:|---|
| `run_cov1` | 450.4 s | a post-fix calibration segment stopped by the driver's default 25-attempt stall patience; the campaign proper raised that to 300 |
| `run_refusal1` | 276.7 s | targeted verification: 42 records, 0 refusal-derived evaluations — it found the selection gap below |
| `run_refusal2` | 283.8 s | targeted verification: 39 refusal-derived evaluations (`drc_clear` 28, `drc_escape` 6, `drc_avoid` 5) |

An earlier segment (about 5 minutes) was aborted and discarded: coverage was
using the narrower real-attempt measure, so pairs with a long sweep history
looked untouched. After the correction, 49 of 49 new records landed on pairs
with no history at all.

**No connection closed in any segment.** `best_board_sha256` stayed
`6c4f8ab81b83f2cb18044028c2a8252da595a57f85317a31c50e1f593477cf1b` and the
accepted generation is byte-identical, re-verified after the run.

## What the campaign did change

Coverage worked, and it is measurable on the board rather than asserted:

* the same 1 318 s window attempted **3** distinct pairs before the correction and
  **116** after it;
* 15 previously-unattempted connections received their first attempt on this
  board (`unattempted_cap_observed` 26 → 11; attempted 79 → 94).

Two real defects were found by running it, not by reading it:

1. **Coverage counted the wrong history.** `attempts_for_pair` excludes sweep
   members, and using that as the coverage measure made a pair with six
   evaluated-and-discarded plans look untouched. Fixed with `records_on`, which
   counts every record bound to the generation; pinned by
   `test_coverage_counts_a_sweep_member_as_work_done`.
2. **Refusal-derived plans were never evaluated.** The runner truncates to its
   candidate limit, and pour/obstacle plans preceded the refusal-derived ones, so
   across 1 318 s **36 pairs carried recorded violations and zero refusal-derived
   candidates were evaluated**. The evidence families now precede the contextual
   ones, and a pair with an *unanswered* refusal outranks an untouched one in the
   selector. `run_refusal2` then evaluated 39 of them.

## Verification

| command | exit | result |
|---|---:|---|
| `bash tools/reliability/check_phase.sh --strict` | 0 | 451 unit + 116 native, no skips |
| `python tools/reliability/check_engine_patches.py` | 0 | patches reproduce the pin, byte-equal |
| `python tools/check_separation.py` | 0 | 4/4 checks passed |
| `git diff --check` | 0 | clean |
| `phase9_taxonomy.py` | 0 | accepted generation `unchanged: true`, 135 categories sum to 135 |
| `phase8_visual.py` | 0 | top/bottom PNG + four copper SVGs, all non-empty |

## Limits

* No closure. The taxonomy says the connection-level evidence improved and the
  board did not.
* `unattempted_cap_observed` (named `capped_unattempted` when this phase ran) is
  run-level evidence about the scan's bounds, not proof about one edge; phase 10
  renamed it so the label states that weaker claim (see
  [TAXONOMY.md](TAXONOMY.md) § *Limits*).
* Substitution records cannot be attributed back to the edge they were offered
  for, because this phase's run state did not store that link. Phase 10 added
  `offered_pair_key` to attempt records; phase-9 records still cannot be
  attributed and are counted rather than guessed at.
* The scoped DRC pass was used in-run; every mutation shape it produced is in the
  differentialled set, and promotion still requires a fresh child, a full native
  DRC, the terminal partition and the complete pinned CLI.

## Next leverage point

The refusal group is now the largest and best-evidenced: 43 edges close the
connection and are refused, all on clearance or hole-clearance classes, and the
refusal-driven search that addresses them has now been exercised but only for
~5 minutes on 7 pairs. The next phase should spend its budget there — with the
selector already preferring unanswered refusals — rather than on further
coverage.
