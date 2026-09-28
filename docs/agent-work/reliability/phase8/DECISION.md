# Phase 8 — a native call reaped at the run's own deadline

Status: diagnosed, fixed, tested. The private campaign that exposed it is
recorded in its own workspace; this note is the generic lesson and the code it
changed.

## What was observed

A bounded deterministic campaign stopped with `session_quarantined`. The failing
attempt was a long two-step connection — real copper, closed by the plan — and
its record in `run_state.json` said only:

```json
{"reason": "drc_unavailable", "copper_state": "retained_unknown",
 "outcome": "unsupported", "committed": false, "steps_applied": 2}
```

Everything that would have named the cause was dropped when the attempt was
serialised: the session's `evidence["exception"]`, the `evidence["rollback"]`
detail, and the `restore_exception` inside it. The concrete next step was
"capture the nested evidence", not "relax the gate".

## Mechanism

`RoutingRunner._native_timeout_s()` bounds every native call by the run's own
remaining budget:

```
min(engine_call_timeout_s, time_limit_s - elapsed_s)
```

The loop checked `time_limit_s` only at the top of an iteration, so an attempt
that started just inside the limit could cross it mid-flight. The acceptance DRC
is dispatched *after* the plan has applied copper — and a whole-board pass on a
frozen board measured **41 s** (the same figure the profile and
`_apply_candidates`' docstring already carry, against 0.2–0.3 s for the scoped
incremental pass). Dispatched with a few seconds of allowance, it was reaped at
its deadline by the client's absolute-deadline handling:

```
EngineServerCrashed: owned engine operation 'call' exceeded its 3.399 s
deadline; child reaped
```

The reaping kills the owned engine child, and that child *is* the checkpoint the
transaction needs. The rollback's `restore` is then dispatched against a dead
child, where the deadline has already expired:

```
EngineServerCrashed: owned engine operation 'call' exceeded its 0.000 s
deadline; child reaped
```

`_restore_and_verify` returns `restored=False` with `restore_exception`, the
session reports `copper_state="retained_unknown"`, and the quarantine is the
right answer: this session can no longer prove it removed copper it believes it
placed. Fail-closed — but on a clock accident rather than on anything about the
board.

### Not an engine defect

The deadline path reaps the child with `kill()`; the native crash tracer never
runs. The engine server's own crash log for that run is **0 bytes**, and the same
campaign's earlier segment ended with the same `EngineServerCrashed` shape from a
plain read (`get_pad_groups`) rather than from a native fault. Both are budget
arithmetic, not a C++ defect — so no engine patch was regenerated and the pinned
build is untouched.

## Reproduction

On a disposable copy of the accepted generation, one fresh engine, the exact
transaction, two deadline regimes:

| Regime | Deadline | Result |
|---|---|---|
| generous | 300 s (production cap) | acceptance DRC completes: refused — 9 added relevant + 7 added connectivity findings — and the rollback is **verified** (`copper_state="restored"`) |
| exhausted | 3.4 s, the run-budget clamp | `drc_unavailable`; `restore_exception` on the rollback; `copper_state="retained_unknown"`; session quarantined |

The exhausted attempt record reproduced the campaign's recorded record
field-for-field on every value the run state kept (`added_length_mm`,
`steps_applied`, `vias_added`, `closed_before_refusal`, `committed`, `outcome`,
`reason`, `copper_state`) — and, before the fix below, dropped the same two nested
fields. The copied generation was byte-identical afterwards and a fresh engine
reopened it at the unchanged geometry digest, with unchanged track/via/unrouted
counts: a reaped child takes its own in-memory copper with it, it does not write
to the board.

The diagnosis also does not depend on the exact split of the last attempt. The
loop can start an attempt with at most `time_limit_s - elapsed_s` of budget left,
and the campaign's last recorded progress put that at ~10 s: whatever sub-second
allowance the acceptance DRC actually got, it was below the 41 s the pass needs.

## What changed

1. **A bounded engine lease, so the soft deadline cannot truncate a transaction.**
   The run has two time budgets and they buy different things. The *soft*
   scheduling budget (`time_limit_s`) decides whether to start work; the *hard*
   operational ceiling (`engine_call_timeout_s`) bounds each individual call. A
   lease sits between them: while one is held, every native call is bounded by
   `min(engine_call_timeout_s, lease remaining)` and **not** by what is left of the
   soft run budget, so a transaction whose DRC or rollback turns out bigger than
   its history still finishes and is verified. A lease is held for the whole
   engine-touching part of an iteration — the digest, the scan, the outstanding
   count, and the attempt they select — and for the run's closing verification of
   the board it is about to report. One *window* is opened per unit of work and
   nesting cannot extend it, so a sweep cannot compound its overrun.

   **The overrun bound is two windows, not one.** The closing verification opens
   its *own* window after the attempt's has closed, so a run that crossed its soft
   deadline inside an attempt and then spends time on the closing DRC overruns by
   the sum of the two. The iteration's two windows cannot both lie past the
   deadline — `_headroom_note` refuses to start an attempt once the deadline is
   spent, so only the window that actually crossed it does — which leaves at most
   one iteration window plus the closing window: the run's overrun past
   `time_limit_s` is **up to two individually bounded lease windows**, and
   `metrics["engine_leases"]["max_overrun_s"]` reports the total actually observed
   (compare it with `max_window_s`, the largest single window, which it can
   exceed). Saved-artifact gates run outside any lease under whatever remains of
   the soft budget, so they add no overrun of their own.

   `RunnerConfig.transaction_lease_s`
   is the floor for one window (default 300 s); it is widened to
   `engine_call_timeout_s` and to `LEASE_MEASURED_FACTOR` (2×) the worst
   whole-board DRC or whole attempt measured so far; 0 disables the lease and
   restores the plain clamp. A lease that *expires* is a hard-bound violation, not
   a scheduling decision: the call is bounded at zero and the existing
   fail-closed quarantine follows, exactly as for a call past its own operational
   timeout.
2. **The run stops at a boundary instead of being killed at one.**
   `RunnerConfig.attempt_headroom_s` (floor, default 30 s) and
   `attempt_headroom_factor` (default 1.25) define a scheduling requirement
   `max(floor, factor × measured)` where `measured` is the worst whole-board DRC
   (timed once at startup as `metrics["source_drc_seconds"]`) or whole attempt
   (`metrics["max_attempt_seconds"]`) seen so far. The loop evaluates it before the
   scan and again before the attempt and stops with
   `stop_reason="time_limit_headroom"` plus the arithmetic in
   `metrics["time_limit_headroom"]`; the sweep stops starting further candidates
   the same way, so nothing unlimited is launched. When instead an attempt is
   already in flight and crosses the soft limit, the lease lets it finish and the
   loop stops right after it with `stop_reason="time_limit_completed_attempt"`.
   Both are wall-clock; a caller's fake run clock cannot inflate the measurements.
   Nothing is weakened: the guard only refuses to *start* work, and the fail-closed
   quarantine is unchanged for every state that still cannot be proved.
3. **A failure keeps its own words.** `AttemptRecord.failure_exception` and
   `AttemptRecord.rollback_detail` are written from the session's evidence and
   serialised with the rest of the record. The exception is bounded
   (`EXCEPTION_TEXT_LIMIT`) because a crash message carries a stderr tail, and the
   rollback detail is compacted to the fields that decide whether a restore was
   proved (`compact_rollback_detail`). Older checkpoints load unchanged: a missing
   key is "not recorded", never an error.
4. **`RunnerConfig.priority_nets`.** A trial brief can name geometry it wants
   attempted while there is still budget to verify it. The scan's round-robin is
   preserved inside both groups, so pinning cannot starve the queue.
5. **Inherited history is labelled, not guessed.** `run_state.json` keeps
   `metrics["transplant"]` (`inherited_records`, `prior_run`, the generation digest
   and a note that only later records came from this run), so an aggregation can
   separate this cycle's outcomes from the ones it inherited.

## Tests

* `tests/agent/test_fault_injection.py` — a reaped acceptance DRC keeps its cause,
  reports `retained_unknown`, quarantines, and is distinguishable from a live child
  that answers "no" (`restore_exception` vs `restored=False`).
* `tests/agent/test_runner_scripted.py` — the saved run state carries the
  exception and the rollback detail for a reaped acceptance DRC; the headroom
  guard stops the run with the arithmetic recorded; a pinned net is offered first
  without losing the queue; **the lease regression**: an attempt begins with
  apparently enough headroom, its acceptance DRC needs far more than any previous
  one and crosses the soft deadline — the DRC was dispatched with the lease's
  allowance, the rollback finishes, the record reads `copper_state="restored"`
  with a proved restore, no record reports `retained_unknown`, and the run stops
  with `time_limit_completed_attempt` and starts nothing else. Its negative
  control runs the identical board, DRC and clock with only `transaction_lease_s=0`
  and shows the same overshoot reaping the live checkpoint; a third case shows a
  call past `engine_call_timeout_s` still quarantining; two more pin the allowance
  itself (soft budget vs lease vs hard ceiling) and the no-extension property of
  nesting. A further case puts the *scan* under the lease by spying on
  `scan_net_pairs`: with 5 s of soft budget left the scan's allowance is the
  lease's, not 5 s. And the **two-window total**: an attempt crosses the soft
  limit on its own (20 s of acceptance DRC against a 10 s limit) and the closing
  DRC then spends 55 s in a window of its own — both answer, nothing is reaped, and
  `max_overrun_s` is 65 s, the total actually observed, which is greater than the
  60 s `max_window_s`.
* `tests/agent/test_scheduler_unit.py` — the new fields survive the checkpoint
  round trip and the compacted text stays bounded.
* `tests/agent/fake_engine.py` — `restore_exception` (no child to ask),
  `drc_fail_after_calls` (let the baselines answer, reap the acceptance pass), and
  the deadline accounting double: `allowance_provider` (the runner's own callback),
  `advance_clock`, `drc_duration_s` / `drc_duration_after_calls`, and `reaped`,
  which makes every later call fail the way a dead socket does.

`bash tools/reliability/check_phase.sh --strict` → exit 0 with the new counts.

### On the board

The lease is exercised on the frozen board by one short targeted run (no routing
campaign): a soft limit the attempt is certain to cross, a headroom floor and
factor lowered on purpose so the attempt starts with little budget left. It ended
`stop_reason="time_limit_completed_attempt"` with the note *"the attempt was
allowed to finish past the run's time limit … stopping at this transaction
boundary instead of truncating its verification"*, `engine_leases =
{granted: 5, expired: 0, max_overrun_s: 49.656}` — 49.66 s is what that run
actually reached, not a bound; the run predates `max_window_s`, so its largest
single window is not recorded — a proved rollback on the attempt record, and the
closing DRC completed. An earlier attempt at the same exercise,
before the lease covered the loop's own reads, died from inside `get_pad_groups`
with `EngineServerCrashed … 0.000 s deadline` — the same shape the previous segment
of the campaign recorded, and the reason the lease now covers the scan and the
reads, not only the transaction.

### Reading a trial's history

A trial may transplant an earlier run's records. Those numbers are *inherited*, not
produced by the trial, and a run state can therefore contain outcomes the new run
never had. `metrics["transplant"]["inherited_records"]` says how many leading
records are the prior run's — compare `records[inherited:]` (or count after that
offset) for a current-cycle figure, and never describe the whole file as this
run's. Concretely: one trial's `run_state.json` holds exactly one
`retained_unknown`, and it is the inherited record; that trial's own attempts
produced none.

## Limits

* The headroom is a measured guess and stays a guess; it is now a *scheduling*
  heuristic rather than the guarantee. The guarantee is the lease, and the two are
  complementary: the headroom keeps the run near its budget, the lease keeps an
  attempt that outgrows it verifiable. Tightening the lease below the work it has
  to contain converts the failure back into a hard-bound quarantine.
* The lease bounds the overrun, not the wall-clock, and the bound is **two
  windows**: a run may finish up to two individually bounded lease windows past
  `time_limit_s` — the iteration window that crossed it plus the closing
  verification's. Each window's floor is `transaction_lease_s` (default 300 s),
  widened by the measurements; `metrics["engine_leases"]` reports `max_window_s`
  (the largest single window) and `max_overrun_s` (the total observed, which can
  exceed it). Operators who need a hard wall-clock stop set `transaction_lease_s=0`
  and accept the old clamp.
* The promotion path is not leased. A transaction accepted in the final seconds of
  a run can still fail its fresh-child artifact gate with the deadline expired and
  be refused rather than promoted (`artifact_verification_failed`) — fail-closed,
  but the accepted copper is lost to the clock. A startup whose soft limit is below
  the acceptance gate's own cost fails the same way before routing begins.
* A whole-board acceptance DRC is still uninterruptible by design. The scoped
  incremental pass (0.2–0.3 s) removes the cost problem but changes how the
  acceptance delta is measured, so it stays opt-in behind
  `tools/reliability/drc_incremental_differential.py`.
* Recovery after a reaped child is not attempted. The session cannot prove the
  state through the engine it lost, and re-opening the board file is a different
  claim from a verified restore; the quarantine stays.
