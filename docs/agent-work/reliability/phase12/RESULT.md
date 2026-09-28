# Phase 12 - result

Status: **accepted as an opt-in harness capability; routing trial inconclusive.** The measured via-opening family is implemented, tested
and wired; the matched trial found no opening to offer on the edges it reached,
so nothing closed and the accepted pointer is unchanged. Two defects found on
the way (one in the transaction path, one carried forward from phase 11) are
fixed and pinned.

Contract: [PLAN.md](PLAN.md).

## What changed

| file | change |
|---|---|
| `pcb_world/agent/session.py` | **fix:** a layer-changing waypoint now re-opens the route after its via (`restart` step), so the plan can keep routing on the new layer; the phase model follows the same rule |
| `pcb_world/agent/zone_coverage.py` | `ZoneOpening` + `find_openings()` (measured via openings); `foreign_distance_mm` on the verdict; malformed/short/extra row answers now fail unknown instead of silently dropping a point |
| `pcb_world/agent/scheduler.py` | `zone_openings` parameter and the `kind="zone_gap_via"` family |
| `pcb_world/agent/runner.py` | `zone_gap_via*` config (default 0, off), `_zone_openings()` with its own cache and run evidence |
| `tests/agent/test_zone_point_query.py` | openings, same-layer/older-engine silence, and the end-to-end "the via lands at the measured opening" test |
| `tests/agent/test_zone_prefilter_unit.py` | the malformed-row regressions and the family's scheduler contract |
| `tools/reliability/check_phase.py` | native floor 129 -> 132 |
| `HISTORY.md`, `docs/agent-work/reliability/phase5-kicad/CHECKPOINT.md` | privacy redaction (see below) |
| `docs/agent-work/reliability/phase12/{PLAN,RESULT}.md`, `CHANGELOG.md` | this phase |

No footprint moved, no zone deleted, no clearance relaxed, no gate substituted,
no engine or wire file touched.

## Research: what actually controls a via

The brief forbids assuming a waypoint controls via placement, so this was read
out of the transaction path and then measured.

`AgentSession._build_plan` expands a waypoint whose layer differs from the
head's into `make_via(x, y)` + `switch` + `line(x, y)` — the via action carries
**the waypoint's own coordinate**. `core_action.make_via` then pre-checks
`pad_block_reason(..., for_via=True)` and calls
`fix_route(x, y, force_finish=True, arrive_tol_mm=<via radius>,
require_via=True)`. So the guarantee is "the committed route ends with a via
within one via radius of the requested point", and the pre-check models pads and
holes, never zones.

Two things follow, and the first was a defect.

1. `make_via` *finishes* the route, and the plan's trailing `line` to the same
   point then ran against an idle session — a `KeyError` on an unmapped head
   layer, rolled back. Every layer-changing waypoint therefore failed unless the
   via itself closed the connection. No test in the tree had ever passed a
   waypoint on a different layer, which is why it survived. The session now emits
   a `restart` step (`start_route` at the via's point on the new layer) after
   each layer change, and models the phase the executor actually sees.
2. The arrival tolerance is real, and the approach is routed on the start layer
   where the router cannot see pours at all — so a clear via spot is worthless
   if the straight approach to it crosses a foreign pour.

Measured after the fix, on synthetic boards: a layer-changing waypoint places its
via at exactly the requested coordinate (error 0.0000 mm) across six positions on
the corridor, and a blocked approach fails the via step cleanly
(`failed_steps=['via']`, no via, board restored). Those are now
`test_a_measured_opening_places_the_via_where_it_was_measured` and the probe
behind it.

## The family

`zone_coverage.find_openings()` samples a cross-layer pair's own corridor
(bounded by pitch and sample count), queries both copper faces a through via
touches in one batched call, and offers a point only when:

* every covering zone on both faces resolved — an unfilled or unknown zone is
  never offered as an opening;
* no foreign-net pour's copper is closer than the board's own clearance + via
  radius on either face;
* no foreign track/via/pad from the caller's existing obstacle observation is
  closer than that margin;
* the whole approach from the start endpoint to the point is clear on the start
  layer — the router cannot see pours, so a blocked approach is not fixable by a
  different via spot.

Qualifying points sort own-net-pour contact first, then measured clearance, are
kept `band_mm` apart, and are capped by the configured count. Each becomes one
`kind="zone_gap_via"` candidate beside the two direct plans, named by its own
geometry, with a rationale that says the opening is measured and not proved
legal. The family is off by default (`RunnerConfig.zone_gap_via = 0`) for the
same reason the phase-11 ranking is: it changes what a sweep tries.

## Carried-forward review fix

`ZoneCoverage.verdicts` zipped unresolved indices with engine rows, so a short
IPC answer left entries as `None` and the final comprehension silently dropped
those points — they vanished from `candidate_risk` and its evidence. Now a row
count that is not exactly one per query marks every point of the batch unknown
with the reason; a `None` row marks only that point unknown; and the return path
guarantees one verdict per point, in order, filling anything left over as
unknown rather than omitting it.

`test_a_short_or_malformed_row_answer_is_unknown_never_a_dropped_point` covers
short, long, holed and well-formed answers, and asserts the returned length and
`points_checked` always match the request. The corrected net-independent row
cache and the opt-in default are unchanged.

## Privacy

The phase-11 review asked for the public/private boundary to be settled before
any publication. The coordinate examples it named (and two more of the same kind
in `docs/agent-work/reliability/phase5-kicad/CHECKPOINT.md`) are redacted in
place with an explicit `[coordinates redacted]` marker pointing at the private
evidence — an intentional, documented exception to the append-only convention,
recorded in the phase-12 entry in `HISTORY.md`.

Still exposed, flagged rather than silently edited: earlier phases' public
documents carry board-specific rule values (for example the board's minimum
clearance and track/hole rules) in `phase3/`, `phase4-recovery/`,
`phase5-kicad/`, `phase10/`, `docs/agent-work/reliability/RESULT.md` and
`HISTORY.md`. Those were part of phase 10's accepted evidence and rewriting six
accepted documents is not this worker's call. Astra should either accept them as
already-reviewed content or authorise a follow-up redaction pass.

## The trial

Two pieces of evidence, both bounded, from the exact accepted generation
(re-hashed unchanged afterwards).

### 1. A whole-board sweep with the family enabled

`phase12_routing/arm_on`: zero planner requests, the phase-10 balanced budgets,
the 26 nets of the 43 refusal edges pinned, a 640 s budget. 275 records over ~16
pairs in 581.9 s, **0 accepted**, every record `restored`, `best_board_sha256`
unchanged. The family never fired: the selector's pairs for those pinned nets
were same-layer pairs, for which a layer transition is not a thing, so
`zone_gap_via` produced no candidate and `metrics["zone_gap_via"]` stayed empty.
That is a selection limitation of this trial, not of the family.

### 2. A matched per-edge trial

`phase12_routing/phase12_trial.py` ran the cross-layer refusal edges, driving the
same candidate generator and the same transactional session the runner uses, with
each arm on its own wall-clock budget so a slow arm cannot starve its control.

| metric | value |
|---|---:|
| cross-layer refusal edges targeted | 2 of 16 (bounded by the per-arm budget) |
| measured openings found | 0 on both edges |
| arm ON candidates tried / accepted | 3 / 0 |
| arm OFF candidates tried / accepted | 3 / 0 |
| outcomes, both arms | `drc_regression` on every candidate |
| wall clock | 577.0 s |

The two arms are identical because the family found nothing to add; the honest
diagnosis is in the measurement itself. Both edges are short, pour-dense hops
(one is about 0.05 mm across layers) whose anchors already sit inside or beside a
foreign pour: no corridor sample cleared the margin on both faces and had a clear
approach on the start layer, so the family correctly declined rather than
offering a spot it could not stand behind. That is the same physical limit
earlier phases recorded by hand for that hop - there is no legal via spot to
choose - and it is exactly the case where a measured opening cannot help.

## Verification

| command | exit | result |
|---|---:|---|
| `bash tools/reliability/check_phase.sh --strict` | 0 | 475 unit + 132 native, no skips |
| `python tools/reliability/check_engine_patches.py` | 0 | 3 patches apply to the pin and reproduce the engine tree, 6 files byte-equal |
| `python tools/check_separation.py` | 0 | 4/4 checks; wire copies identical |
| `git diff --check` | 0 | clean |
| accepted-generation sha256 | - | `6c4f8ab81b83...f593477cf1b`, unchanged; pointer untouched |

## Limits and risks

* **The trial is inconclusive, not negative.** Two of sixteen cross-layer edges
  were reached inside the budget and neither offered an opening. The family's
  value on edges that do have a clear approach and a clear via spot is
  unmeasured; that needs a longer window (each matched pair of arms costs about
  five minutes on this board).
* **The selection limitation is real and unfixed:** a whole-board sweep pinned by
  net does not guarantee cross-layer pairs, so a future trial should select edges
  (cross-layer ones) rather than nets, or add an explicit priority-edge control
  to the runner.
* **A clear opening is still only a measurement.** The route to it can be
  blocked, the other endpoint's approach can be blocked, and the DRC decides
  everything; the family never claims otherwise and never suppresses a plan.
* **The session fix widens what executes.** Plans with layer-changing waypoints
  previously failed at the trailing line and rolled back; they now continue. That
  is the intent, and it can only convert failures into attempts, but it changes
  the copper a sweep can lay, so it is covered by the strict gate and by the new
  end-to-end test.
