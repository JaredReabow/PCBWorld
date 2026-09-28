# Phase 22 — bounded alternate-anchor doglegs

Status: assigned. Phases 18–21 are accepted diagnoses; no new board promoted.

## Question

Does allowing a route to escape an anchor in a different direction before
turning toward the other disconnected component produce a credible candidate
where phase 21's straight segment did not?

## Contract

1. Use the exact 49-edge, component-proved anchor pools from phase 21. Choose
   up to 16 disconnected pairs per edge by deterministic, documented ranking,
   giving the four coincident cross-layer edges first consideration. Record the
   full eligible count and the selected count; do not imply pair-exhaustiveness.
2. Search a finite dogleg family: short escape from either anchor, one or two
   bends, then a main leg; at most one through via for cross-layer pairs. Bound
   escape distance, bend count, headings, pair count and wall time. Measure every
   segment and via span under the same conservative proxy, including netless
   copper, loaded zone fill and keepouts. Unknown means unproved. Own-net copper
   at an endpoint is permitted only with proved identity and cluster membership.
3. Independently check any candidates with a fresh engine and denser samples.
   If credible candidates remain, run at most three distinct transactional
   trials, zero model calls, with the existing rollback, native DRC and
   connection gates. Do not count proxy paths or DRC-refused closures as
   accepted. Promote only through complete established gates against the
   canonical original; otherwise leave the accepted pointer unchanged.
4. Keep all per-edge nets, coordinates, geometry and rule values private;
   public output carries aggregate counts and method parameters only. No
   layout/rule/zone/footprint edits, engine/wire change, staging, commit or push.

## Evidence

DeepSeek owns private phase-22 scripts/evidence and public aggregate RESULT.md,
HISTORY/CHANGELOG entries. Report 49/49 edge accounting, eligible/selected pair
coverage, search statuses, independent checks, every trial's closure/DRC/copper
state, pointer and board digest before/after, and a bounded next decision. Astra
reviews.
