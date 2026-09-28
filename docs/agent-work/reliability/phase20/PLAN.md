# Phase 20 — coincident cross-layer via-column diagnosis

Status: assigned. Phase 19 is accepted as a finite-window proxy result; zero
candidate paths does not prove the board unroutable.

## Question

For the four offered edges whose proved endpoints coincide in XY but lie on
different copper layers, exactly what blocks a through-via column at or near
the shared point? Is there a legal, documented harness option other than the
current through via, or is a physical-design decision required?

## Contract

1. Diagnose all four exact edges on the unchanged accepted generation, with a
   narrow bounded search around each coincident point. Reproduce the phase-19
   via failure independently using the native read-only board geometry and
   available rule surface. Attribute every block to copper, zone, keepout,
   drill/hole, unknown custom rule, or unavailable engine information. Keep
   unknown distinct from blocked.
2. Inspect the pinned engine/KiCad source or public API locally to confirm the
   pad-extent convention and supported via types/layer spans; do not infer via
   capability from a wrapper field or a rendered image alone. Report what the
   current harness can actually place and what would require an engine change.
3. If a specific legal via candidate emerges, at most one bounded transactional
   trial on one exact edge, with existing rollback, native DRC and connection
   checks. Zero model requests. A closed-then-refused route is not acceptance.
   Any promotion requires the complete established gates against the canonical
   original. Otherwise leave the pointer untouched.
4. Keep per-edge nets, coordinates, geometries and rule values private. Public
   output is aggregate counts, capabilities, tests and limits. No rule
   relaxation, footprint/zone move, board edit outside a reversible trial,
   engine/wire change, staging, commit or push.

## Evidence

DeepSeek owns private phase-20 scripts/evidence, a public aggregate RESULT.md,
and HISTORY/CHANGELOG additions. Validate exact 4/4 coverage, source-backed via
capability, accepted pointer/hash before/after, and any trial's terminal state.
Astra reviews the result and decides whether harness work or a physical-design
question is next.
