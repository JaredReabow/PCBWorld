# Phase 10 — attribute the refusals, then aim two families at them

Status: in progress (2026-09-28). Phase 9 is the accepted predecessor
([RESULT.md](../phase9/RESULT.md)).

## What phase 9 left on the table

Phase 9's taxonomy found the largest well-evidenced group on the frozen V3 board:
**43 edges whose plan closes the connection and whose copper the native gate
refuses**, every one of them on clearance or hole clearance. It also found the
reason the refusal-driven search had barely been exercised — the selector could
not see a pair whose only remaining plans came from families the *runner*
computes with the board in hand — and two accounting gaps that made the numbers
harder to trust than they had to be:

* `capped_unattempted` was named for a claim the evidence cannot support. The run
  state keeps run-level scan counters, not a per-net breakdown, so "this edge was
  withheld by the cap" is not checkable. The checkable statement is "unattempted,
  in a run that reported its bounded scan withheld pairs somewhere".
* a substitution record is filed under the geometry it substituted *in*, and the
  old run state did not record which offered edge it stood for, so 1 804 records
  could only be counted, never attributed.

## Contract

1. **Rename, do not renumber.** `capped_unattempted` becomes
   `unattempted_cap_observed`, in the tool, the public taxonomy, the report and
   the tests. The counts and the sum to the edge count are unchanged.
2. **Record the offered edge.** Attempt records gain `offered_pair_key`: the
   offered connection the attempt stands for, while `pair_key` stays the geometry
   actually attempted. The schema is backwards compatible (an absent key means
   "not recorded", never an error), the join prefers the exact offered key over
   component membership, and a run that predates the field is counted as
   unattributed rather than guessed at.
3. **Two generic, bounded, class-specific families.**
   * clearance: `drc_extent` places a waypoint past the *observed obstacle's own
     extent* on the pair's own layer, at the board's own minimum clearance,
     instead of at a multiple of a clearance the refusal never measured;
   * hole/via: `_hole_escape_seeds` seeds the free-via search from the *actual
     drilled geometry* near a hole-class refusal — hole radius + via radius + the
     board's hole-to-hole rule — while `pad_block_reason` stays a prefilter and
     the native DRC stays the only authority on whether a via may be kept.

   Neither family names a net, a coordinate or a board. Both are bounded by a
   shared per-pair budget, neither relaxes a rule, neither substitutes a gate,
   and neither moves a footprint or shrinks protected copper.
4. **Selection follows the configured budgets.** `_refusal_unanswered` is
   computed with the run's *actual* `drc_candidate_limit`, `drc_clearance_probes`,
   `drc_clearance_mm` and `drc_extent_probes`, not the generator's defaults:
   raising a limit used to change what a run evaluated without changing which
   pair it selected. The families the session computes (a free via spot) are
   measured by the pair's own records *and* by the run's own search evidence, so
   a search that ran and found nothing counts as evaluated, and an unproductive
   family is labelled unknown, never impossible.
5. **Diagnose before aiming.** Before the campaign, a bounded sample of the
   closed-but-refused plans is replayed and read through the gate's own violation
   rows and the board's item inventory: exact native class, position, layer, net
   names, the *physical items* the finding names, and the applicable design
   rules. The two families above are justified by that evidence, not by the
   category name.
6. **One bounded deterministic campaign**, zero planner requests, from the exact
   accepted generation, with the prior same-generation history carried forward.
   Records report how many of the 43 refusal edges were reached, which candidate
   classes were evaluated, and whether any connection was actually accepted.
7. **Promotion only through the immutable store**: fresh child, full native DRC,
   terminal partition, complete pinned CLI against the canonical original. If
   nothing closes, the accepted pointer stays where it is and the report says why.

## Evidence

* taxonomy tool: `tools/reliability/failure_taxonomy.py`
* unit contracts: `tests/agent/test_failure_taxonomy.py`,
  `tests/agent/test_coverage_and_clearance.py`
* phase gate: `bash tools/reliability/check_phase.sh --strict`
