# Phase 17 - where a plan stops

Status: in progress (2026-09-28). Phase 16 is the accepted predecessor
([RESULT.md](../phase16/RESULT.md)); this phase's outcome is in
[RESULT.md](RESULT.md).

## What phase 16 left

The through-via screen now asks every layer a via spans and fails closed. That
closed the one place the campaign knew a question was being asked on the wrong
geometry. The larger open question is different: of the connections the scan
offers and the harness takes on, **where does a plan actually stop?** The
taxonomy can say that an edge is `connection_not_verified` — the plan did not
close it and the copper was restored — but not whether the route never opened at
its anchor, never got a line down, died on its via, or ran every step and simply
did not connect. Those are four different problems with four different fixes,
and the run state could not tell them apart.

## Contract

1. **Read the stored evidence, do not re-derive it.** Same-generation attempt
   history is the input; a replay against a harness that has changed since is a
   different experiment, not the recorded one.
2. **Name the failing step at the source.** `AttemptRecord` gains
   `failed_step_kind` and `failed_step_index`, populated from the session's own
   step list by the runner, serialized with the record, and read back as
   "not recorded" (empty kind, index `-1`) from any checkpoint that predates
   them. Additive and backwards compatible: no reader can mistake "no failure"
   for a failure at step 0.
3. **Quantify coverage honestly.** The analysis distinguishes an edge that was
   attempted on this generation from one it was not, and reports the copper
   state (`restored` / `loaded_unverified` / `retained_unknown`) rather than
   collapsing them.
4. **Aggregates are publishable; geometry is not.** Step kinds are harness
   vocabulary, so the taxonomy reports the board-wide profile. Net names,
   coordinates, edge geometry and rule values stay in the private tree.
5. **One bounded current-harness trial**, zero planner requests, on the affected
   nets, from the exact accepted board, to replace reconstruction with recorded
   evidence for as many affected edges as the window reaches.
6. **Promotion only through the immutable store**, and only for a candidate the
   gate *accepted* — closing a connection is necessary, not sufficient: a plan
   that closes it and adds a relevant violation is refused and its copper rolled
   back. Otherwise the pointer stays put and the report says what remains.
7. **No engine or wire changes, no rule guessing, no rule relaxation**, no
   footprint moves, no zone deletion, no commit, stage or push.

## Evidence

* Fix: `pcb_world/agent/scheduler.py` (`AttemptRecord.failed_step_kind`,
  `failed_step_index`, `to_dict`, `from_dict`) and `pcb_world/agent/runner.py`
  (`_record`).
* Tool: `tools/reliability/failure_taxonomy.py` gains
  `EdgeAttempt.failed_step_kinds`, the stopping-point counts on the
  `connection_not_verified` detail, and a board-wide `failed_step_kinds`
  aggregate in the public summary.
* Tests: `tests/agent/test_runner_scripted.py` (a stopped plan names its step;
  a completed plan names none), `tests/agent/test_scheduler_unit.py` (round trip
  and legacy/unusable values), `tests/agent/test_failure_taxonomy.py` (the
  category detail, the per-edge aggregation, the public aggregate).
* Private evidence: `phase17_routing/` (analysis, replay, trial, notes).
