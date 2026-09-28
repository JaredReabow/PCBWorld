# Phase 23 — native DRC refusals after real closure

Status: assigned. Phases 18–22 diagnosed static geometry families but promoted
no board. Phase 17's fresh current-harness run is the source for this phase:
24 plan evaluations closed a connection before native DRC refused and rolled
them back.

## Question

What exact DRC deltas rejected those 24 real closures? Is there a shared
harness or rule-application defect we can correct without changing the board's
rules or design, or are the copper results genuinely unacceptable?

## Contract

1. Use only fresh phase-17 records written by the current harness, exact
   accepted generation, and exact plan identity. Reconcile 24/24
   closed-before-refusal records, group by edge, plan and native violation
   fingerprint. Distinguish clearance, zone, hole, connectivity, and rule-load
   failures; unknown stays unknown. Keep all per-plan/net/coordinate/rule data
   private.
2. On up to three representative records, reproduce a candidate in a
   reversible transaction from the immutable accepted board, capture pre/post
   native DRC and connectivity/rollback evidence, and verify the accepted
   pointer/hash afterwards. Zero planner model calls. Do not promote a DRC-
   refused candidate.
3. If a specific shared harness defect is proved, implement one tightly scoped
   correction in public harness code with meaningful tests and strict harness
   gate. Do not change engine/wire or design rules without a separate approved
   architecture decision. If no defect is proved, stop at diagnosis and name
   the physical-design choice with evidence.
4. No rule relaxation, footprint/zone move, source-board mutation, broader
   routing campaign, stage, commit or push. Public result contains aggregates
   and method only; private evidence owns geometry and rule values.

## Evidence

DeepSeek owns private phase-23 scripts/evidence, public RESULT.md and aggregate
HISTORY/CHANGELOG. Report 24/24 record accounting, DRC delta categories,
reproduction status, any code patch/tests, native gate result, accepted artifact
hash/pointer before and after, and the next bounded action. Astra reviews.
