# Phase 24 — disposable-copy zone refill measurement

Status: approved by separate Astra review of phase 23. Phase 23's evidence
histogram fix is accepted; the accepted board remains at 135 unrouted.

## Question

Do the stored zone fills, when recomputed under the current pinned project rules,
change the DRC findings that rejected three reproduced closures? This is a
measurement on a disposable copy, not permission to alter the accepted board.

## Contract

1. Identify a supported pinned KiCad/engine refill path and prove the exact
   project and rule context it uses. If refill is unsupported, errors, or does
   not persist changed fill on save/reopen, report that separately; do not
   interpret unchanged DRC as a physical-design conclusion.
2. Copy the immutable accepted generation into private scratch. Capture board,
   rules, project, accepted-pointer hashes and complete non-zone item inventory
   before. Refill once under the pinned context, save, reopen, and prove whether
   zone fill geometry changed while all non-zone geometry and rule values stayed
   unchanged. Keep an independently named candidate artifact; never move the
   accepted pointer in this phase.
3. On both before and after copies, run full native DRC and terminal partition
   checks, classify findings as resolved, persistent and new by stable identity.
   Run complete pinned KiCad CLI verification against the canonical original.
   Account for the known cross-process DRC shorting-class variation; do not
   attribute that variation to refill without controlled evidence.
4. No routing campaign, planner calls, board design edits, rule relaxation,
   footprint movement, engine/wire change, staging, commit or push. Per-edge
   nets, coordinates, item geometry and rule values stay private. Public result
   has aggregates and clear limits only.

## Evidence

DeepSeek owns private phase-24 scripts/artifacts and public aggregate RESULT.md,
HISTORY/CHANGELOG entries. Report supported/unsupported refill outcome,
before/after hashes and inventories, save/reopen proof, native DRC and terminal
partition diffs, complete pinned CLI results, unchanged accepted pointer and a
bounded recommendation. Astra independently reviews.
