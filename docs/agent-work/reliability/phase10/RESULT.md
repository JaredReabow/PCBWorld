# Phase 10 - result

Status: **ready for review.** Nothing promoted; the accepted pointer is unchanged.
Contract: [PLAN.md](PLAN.md).

## What changed

| file | change |
|---|---|
| `pcb_world/agent/observations.py` | `NetPair.offered_key`; a substituted offer carries the edge the scan drew |
| `pcb_world/agent/scheduler.py` | `clearance_extent_probes` and the `drc_extent` family; `AttemptRecord.offered_pair_key`; `offered_edge_key` |
| `pcb_world/agent/runner.py` | configured clearance/probe/extent budgets; `_refusal_unanswered` uses them and accounts for the session-computed families; `_hole_escape_seeds`; every record carries the offered key |
| `tools/reliability/failure_taxonomy.py` | `capped_unattempted` -> `unattempted_cap_observed`; the history join prefers the offered key over component membership |

Tests: `tests/agent/test_coverage_and_clearance.py` (extent family, hole seeds,
offered-key wiring, selection with the configured budgets) and
`tests/agent/test_failure_taxonomy.py` (offered-key attribution, direct
attempts, legacy history, the reframed category).

No footprint moved, no protected copper shrank, no rule was relaxed, no gate was
substituted, and `patches/engine/` is byte-identical to the pin.

## The rename

`capped_unattempted` is now `unattempted_cap_observed`, in the tool, the tests,
the meaning table and the phase-9 report. The category was always run-level
evidence - the run state keeps scan counters, not a per-net breakdown - so the
old name asserted something the evidence cannot support. The counts are
unchanged and still sum to the edge count: 26 as found, 11 after the campaign,
135 total.

## Attribution

Records now carry `offered_pair_key`: the offered connection an attempt stands
for, while `pair_key` stays the geometry actually attempted. The join resolves an
edge by its own pair key, then by the offered key, then by native component
membership, and counts what it cannot attribute instead of guessing. On the
refreshed taxonomy: 29 records attributed by offered key, 1 198 substitution
records bound to this generation, 28 attributed, 1 170 unattributed - the last
figure is dominated by records written before the field existed, which is exactly
what it should be.

Measured effect: **two previously-unattempted connections whose ratsnest anchor
carries no copper received their first attempt** (`copper_absent_at_offered_anchor`
26 -> 24 attempted, and the totals still sum to 135). Before this phase those
attempts were made but could not be attributed to any offered edge.

## The diagnosis that aimed the families

Forty closed-but-refused plans were replayed and read through the gate's own
violation rows and the board's item inventory: **Clearance violation 164, Hole
clearance violation 92**, on copper layers 0/2/4/6, against **zone (256), via
(163) and track (93)** items. The dominant shape is the *plan's own via* against a
**foreign copper zone** - the moving item is the via, the obstruction is a pour.
The applicable rules on this board (clearance, track, hole-to-hole, via size)
are `[rule values redacted]` - the private evidence holds them.

## The campaign

`phase8_route.py` from the exact accepted generation, zero planner requests,
prior history merged from every same-generation run (2 471 records kept, 4 075
dropped as other-digest).

| segment | routing time | stop reason | own records | distinct pairs | refusal-derived records | refusal edges reached | closures |
|---|---:|---|---:|---:|---:|---:|---:|
| `run_refusal1` | 417.3 s | interrupted (calibration) | 111 | 8 | 94 | 5 of 43 | 0 |
| `run_refusal2` | 962.6 s | `time_limit_headroom` | 354 | 33 | 233 | 12 of 43 | 0 |
| **campaign** | **1 379.9 s** | | **465** | **39** | **327** | **16 of 43** | **0** |

`run_refusal1` ran the deeper `--candidate-limit 16 --drc-probes 12` setting and
was stopped after 417 s when the per-pair cost (~54 s) made the 1 500 s campaign
budget reach too few of the 43 edges; `run_refusal2` ran the balanced setting
(12 candidates, 8 probes) and is the segment that spent the rest of the budget.
Neither is a discarded run: the first is reported here and its records were
carried into the second.

Candidate classes actually evaluated, by record:

| class | records | pairs |
|---|---:|---:|
| `drc_clear` | 124 | 18 |
| `via_free` | 74 | 19 |
| `drc_extent` | 45 | 12 |
| `via_jog` | 42 | 13 |
| `drc_escape` | 31 | 8 |
| `drc_avoid` | 11 | 5 |

Against phase 9's **39 refusal-derived evaluations over 7 pairs**, this is **327
over 16 refusal edges**. The new extent family was evaluated on 12 of them, and
the hole-derived family on 19.

**No connection was accepted in any segment.** 95 of the 465 records closed the
connection and were refused by the gate (`closed_before_refusal`), and every
record's copper state is `restored` - the rollback path did its job 465 times.
`best_board_sha256` stayed
`6c4f8ab81b83f2cb18044028c2a8252da595a57f85317a31c50e1f593477cf1b`, the accepted
generation is byte-identical, and no promotion gate was run because there was no
candidate board to promote.

## Verification

| command | exit | result |
|---|---:|---|
| `bash tools/reliability/check_phase.sh --strict` | 0 | 462 unit + 116 native, no skips |
| `python tools/reliability/check_engine_patches.py` | 0 | patches reproduce the pin, byte-equal, 6 files |
| `python tools/check_separation.py` | 0 | 4/4 checks passed |
| `git diff --check` | 0 | clean |
| `phase9_taxonomy.py --out-dir phase10_taxonomy --history ...` | 0 | accepted generation `unchanged: true`, categories sum to 135 |
| `phase10_refusal_diagnosis.py` (40 replays) | 0 | 256 native violations classified and attributed to items |
| `phase8_visual.py` | 0 | top/bottom PNG + four copper SVGs, all non-empty, CLI 9.0.8 |

## Limits

* No closure, and the honest reason is in the diagnosis: the refusals are
  dominated by the plan's **own via** against a **foreign zone**. Neither new
  family models a zone's boundary - `nearest_obstacles` reads tracks, vias and
  pads, and the engine's zone accessor carries no net identity - so the plans
  that close the connection land a via in a pour and the gate refuses it. The
  next phase needs a zone-aware placement question (which zone covers this point,
  and is it this net's) rather than another waypoint family.
* The board's native DRC is not perfectly deterministic at this size: the run
  recorded "0 added / 9 resolved" findings on an unmodified board. Promotion still
  requires a fresh whole-board pass, and a delta has to be read with that in mind.
* `via_free` / `via_jog` are the families the *session* computes; selection
  counts them by kind and by the run's own search evidence, which is coarser than
  the key-exact test the pure generator gets.
* A search that finds no lawful via spot is reported as an unproductive sample,
  never as proof that none exists.
