# Phase 17 - result

Status: **ready for review.** The attempt record now names the step a plan
stopped at, the taxonomy reports those counts as an aggregate, and a bounded
current-harness trial produced the first records that can answer "where do plans
fail" directly instead of by replaying a plan against code that may have moved
on. **Zero accepted, zero promoted**: 24 of the trial's plan evaluations did
close the connection and were rejected by the DRC before any copper was kept, so
the accepted pointer is unchanged.

Contract: [PLAN.md](PLAN.md).

## The finding that shaped the phase

The 49 `connection_not_verified` edges of the phase-11 taxonomy have 1 136
same-generation attempt records between them. Every one of those records was
written **before** the harness that exists now: a bounded replay of a recorded
plan whose reconstruction said "the route could not be re-opened after the via"
showed every step succeeding on the present harness, and the plan then closed
the connection and was refused by the DRC. The board digest proves the copper is
unchanged; it says nothing about the code that ran on it. A failure analysis
built only on stored history can therefore describe a step sequence that no
longer exists — which is why this phase put the stopping point **on the record**
instead of inferring it later.

## What changed

| file | change |
|---|---|
| `pcb_world/agent/scheduler.py` | `AttemptRecord.failed_step_kind`, `.failed_step_index`; written by `to_dict`, read by `from_dict` as "not recorded" for any value that is not a plain integer |
| `pcb_world/agent/runner.py` | `_record` names the first step in the session's own step list that did not succeed |
| `tools/reliability/failure_taxonomy.py` | `EdgeAttempt.failed_step_kinds`; the stopping-point counts on the `connection_not_verified` detail; a board-wide `failed_step_kinds` aggregate in the private report and the public summary |
| `tests/agent/test_runner_scripted.py` | a stopped plan names its step and its index; a completed plan names none |
| `tests/agent/test_scheduler_unit.py` | the field round-trips through the checkpoint, and a legacy or unusable value loads as "not recorded" |
| `tests/agent/test_failure_taxonomy.py` | the category detail, the per-edge aggregation from records, and the public aggregate |
| `docs/agent-work/reliability/phase17/{PLAN,RESULT}.md`, `HISTORY.md`, `CHANGELOG.md` | this phase |

No engine or wire file was touched, no footprint moved, no zone was deleted, no
clearance was relaxed and no gate was substituted.

## The fix, and what it is not

The fields are **evidence, not policy**: nothing reads them to decide anything,
so no behaviour changes for a run that does not ask. They are additive and
backwards compatible in both directions — a checkpoint written before them
loads, and an older reader ignores the new keys.

The pairing is the point. `steps_applied` counts the steps that succeeded, and
the session stops at the first failure, so `failed_step_index == steps_applied`
for any record that stopped. Two numbers that must agree are one number and a
check: the trial's 84 stopped records satisfy it exactly (0 violations).

An empty kind with index `-1` means "no failed step recorded" — every step
succeeded, or the checkpoint predates the field. The taxonomy counts those in a
bucket named for the evidence rather than for a cause, because the record alone
cannot separate the two and the analysis must not guess.

## The bounded trial

Exact accepted board, fresh run state, no prior history transplanted,
`max_model_requests = 0` (zero planner calls), priority-netted to the nets the 49
edges belong to, bounded at 1 500 s of soft budget and 400 attempts.

| | |
|---|---:|
| stop reason | `time_limit_headroom` |
| attempts | 19 |
| plan evaluations | 109 |
| accepted | **0** |
| distinct pairs attempted | 13 |
| records | 109 |
| records naming their failing step | 84 |
| unrouted edges before → after | 135 → 135 |
| best board SHA256 | equal to the accepted generation |

**Coverage: the window reached 7 of the 49 edges** (7 distinct nets, all
same-layer), producing 42 records that name their failing step. The other 42
named-step records are plans on pairs outside the 49. Reach is the window's
limit, not a property of the board: a pair attempt costs roughly 30-60 s because
every candidate is measured by a whole-board native DRC, and the phase kept the
authoritative pass rather than switching to the incremental one.

### Where plans stop, from the records themselves

All 109 trial records, by the step the runner recorded:

| recorded stopping point | records | what it means |
|---|---:|---|
| `start` | 12 | the route could not be opened at the anchor |
| `line` | 61 | the route was open and the line did not land |
| `via` | 11 | the via could not be placed |
| none, `connection_not_verified` | 0 | every step ran and the connection was still not reached |
| none, `drc_regression` | 24 | every step ran, the connection **closed**, and the native gate refused the copper |
| none, endpoint identity | 1 | the anchor had copper but no verified same-net anchor |

The last two are the interesting pair. A `drc_regression` record is not a plan
that failed — it is a plan that *worked* and was refused, and its copper state is
`restored`. Before this phase both it and a route that never opened were the same
`routing_failed` line in the history.

### The reconstruction agrees with the record, where both exist

The private analysis reads each record twice: it rebuilds the step sequence the
session would emit (from the plan's waypoints plus the pair's own start and
target layers) and compares it with `steps_applied`, and it reads the recorded
failing step. On the 42 records where both answers exist, **42 agree and 0
disagree**.

That is a fair check on the reconstruction and nothing more. It does not
retro-validate the 1 136 pre-field records, because those were written by a
different harness — which is precisely what the replay showed.

## Promotions

**None.** Nothing was accepted, so the promotion ladder did not run and the
accepted pointer is unchanged; the accepted generation still hashes to
`6c4f8ab81b83...f593477cf1b`, re-checked after the trial.

Closing a connection is not the gate — being accepted is. Twenty-four plan
evaluations *did* close the connection and were rejected by the native DRC
(`drc_regression`, `closed_before_refusal`, `copper_state: restored`): a
candidate that closes a connection and adds a relevant violation is not a
promotion candidate, and its copper was rolled back rather than kept. The
remaining 85 evaluations never closed it: 12 stopped at the start step, 61 on a
line and 11 on a via, and one pair was refused before any plan ran because its
anchor carried no verified same-net copper. So the phase produced no promotion
and no closure that survived its gate — but it did produce 24 plans that closed
and were refused, which the pre-phase history could not have told apart from
plans that never connected.

## Verification

| command | exit | result |
|---|---:|---|
| `bash tools/reliability/check_phase.sh --strict` | 0 | 494 unit + 138 native, no skips |
| `python tools/check_separation.py` | 0 | 4/4 checks; wire copies identical |
| `python tools/reliability/check_engine_patches.py` | 0 | 3 patches, 6 files byte-equal to the pin |
| `git diff --check` | 0 | clean |
| private analysis over the same-generation history | 0 | 49/49 edges attempted, 1 178 records (1 136 stored + 42 from the trial), 0 unattempted |
| private bounded trial | 0 | 19 attempts, 109 plan evaluations, 0 accepted, 0 promoted; 24 closed-before-refusal |
| accepted-generation sha256 | - | `6c4f8ab81b83...f593477cf1b`, unchanged |

## Limits and risks

* **A run state's provenance is a version, not a guarantee.** The new fields make
  *future* records self-describing. Every existing checkpoint still cannot say
  where a plan stopped, and no amount of reconstruction makes it able to.
* **The stopping point is the session's step boundary, not the cause.** "The via
  could not be placed" is where the plan stopped, not why: the via may have been
  refused for clearance, for a foreign plane, or because the line into it never
  got there. The taxonomy's categories and the DRC classes remain the evidence
  for cause.
* **Seven of forty-nine edges in a 25-minute window** is a sample of the current
  harness on same-layer pairs. The eight cross-layer edges were not reached at
  all, so the recorded distribution says nothing about them.
* **`line` dominates the recorded sample** (61 of 84 named steps), which is
  consistent with the reconstruction's older `blocked_before_via` majority, but
  the two are not the same measurement and neither is a proof about the board.
* **The engine pin and the rule surface are untouched.** No rule value was read,
  guessed or relaxed.
