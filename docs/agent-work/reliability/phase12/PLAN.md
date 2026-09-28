# Phase 12 - a measured via opening, and a matched control

Status: in progress (2026-09-28). Phase 11 is the accepted predecessor
([RESULT.md](../phase11/RESULT.md)); this phase's outcome is in
[RESULT.md](RESULT.md).

## What phase 11 left on the table

Phase 11 gave the sweep a read-only answer about zone copper and used it to
**rank** candidates the existing families had already generated. Astra accepted
that as an opt-in advisory capability with no board closure claimed, and the
natural next slice is the one the ranking cannot reach: a family that *places*
the layer transition at a spot the zone geometry says has room, instead of
leaving the via to the router and hoping.

## Research first: what actually controls a via?

The brief is explicit that a waypoint is not assumed to control via placement.
Reading the transaction path settles it:

* `AgentSession._build_plan` expands a waypoint whose layer differs from the
  head's into `make_via(x, y)` + `switch` + `line(x, y)` — the via action carries
  **the waypoint's own coordinate**;
* `core_action.make_via` pre-checks `pad_block_reason(..., for_via=True)`, then
  `fix_route(x, y, force_finish=True, arrive_tol_mm=<via radius>,
  require_via=True)`. So the guarantee is "the committed route ends with a via
  within one via radius of the requested point", and the pre-check models pads
  and holes — never zones.

That is enough to build on, with two conditions the family has to respect: the
arrival tolerance is a real tolerance, and the *approach* is routed on the start
layer where the router cannot see pours at all.

## Contract

1. **A measured opening, not a guess.** `zone_coverage.find_openings()` samples a
   cross-layer pair's own corridor (bounded by pitch and sample count), queries
   both copper faces a through via touches, and offers a point only when:
   every covering zone resolved (an unfilled/unknown zone is never offered), no
   foreign-net pour's copper is closer than the board's own clearance + via
   radius on either face, no foreign track/via/pad from the caller's existing
   obstacle observation is closer than that margin, and **the whole approach
   from the start endpoint to it is clear on the start layer** — the router
   cannot see pours, so a straight approach through one is not fixable by
   choosing a different via spot.
2. **Generic.** No coordinates, net names, layers or rule values in the code or
   in the public docs. Everything comes from the board's own rules and the
   query's own measurements.
3. **Bounded.** One batched query per pair for the samples of both faces,
   `max_openings` returned, `band_mm` apart. The family is silent rather than
   unbounded when nothing qualifies.
4. **Opt-in, off by default.** `RunnerConfig.zone_gap_via` defaults to 0;
   turning it up changes what a sweep tries, so a run must ask for it. The
   campaign driver exposes `--zone-gap-via`.
5. **A candidate, never a legality claim.** The opening is measured clearance;
   the transactional native DRC remains the only authority on whether the
   copper may be kept, and the rationale string says exactly that.
6. **No engine or wire change.** Phase 11's query is enough. Anything that
   needed a new native accessor would come back to Astra as a proposal first.
7. **A matched bounded control.** The trial runs the same edge set, budgets,
   ordering and baseline twice: once with the family enabled and once with it
   disabled. Attempt state starts fresh in both arms so no inherited plan key
   can answer for the new family, while board/project/rules provenance is still
   recorded. Per-edge coverage, candidate counts, DRC classes, closures and
   timing are reported for both arms.
8. **Promotion only through the immutable store**, and only if something
   actually closes: fresh child, full whole-board native DRC, terminal
   partition, complete pinned CLI gates against the canonical original. With no
   closure the accepted pointer stays untouched and the report says why.

## Evidence

* Finder: `pcb_world/agent/zone_coverage.py` (`ZoneOpening`, `find_openings`).
* Family: `pcb_world/agent/scheduler.py` (`zone_openings` →
  `kind="zone_gap_via"`); wiring and budget in `pcb_world/agent/runner.py`
  (`zone_gap_via*`, `_zone_openings`).
* Transaction path fixed and pinned: `pcb_world/agent/session.py`
  (`restart` step after a via), `tests/agent/test_zone_point_query.py`.
* Trial driver: private `phase8_route.py` (`--zone-gap-via*`), coverage join in
  private `phase12_routing/phase12_coverage.py`.
* Phase gate: `bash tools/reliability/check_phase.sh --strict`; patch proof:
  `python tools/reliability/check_engine_patches.py`.
