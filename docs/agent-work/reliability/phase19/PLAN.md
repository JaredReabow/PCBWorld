# Phase 19 — bounded 2D path test

Status: assigned. Phase 18 is accepted as a read-only diagnosis; its sampled
minimum-clearance proxy is not native DRC authority.

## Question

For the exact 49 phase-18 edges on the unchanged accepted generation, can a
continuous candidate path be found through a fixed local 2D window, rather
than a single open point or a lateral straight lane? If so, can a small sample
actually connect without a native DRC regression?

## Contract

1. Search all 49 exact offered edges within a declared, finite window around
   the offered segment (4 mm lateral margin initially; 2 mm beyond each end),
   using deterministic bounded sampling. Include same-layer and cross-layer
   edges; the latter must treat a through via as occupying every spanned copper
   layer. Report search exhaustion or unknowns explicitly.
2. Sampled grid nodes and **segments** must satisfy the measured clearance
   proxy against foreign copper, netless copper, zones, and keepouts. Handle
   endpoints on their proved same-net copper without mistaking it for an
   obstacle. Do not call a proxy path legal or DRC-clear.
3. Independently check the candidate path geometry, exact endpoints and
   layer transitions. Keep all per-edge geometry and rule values private. Public
   output contains aggregate counts and the method/limits only.
4. If a path is found, run at most three bounded transactional trials on distinct
   edges, selected deterministically from the strongest candidates. Zero model
   planner requests. Use existing rollback, native DRC and verification gates.
   A trial that closes then fails DRC is a refusal, not acceptance. Do not
   promote a board unless it passes the complete established promotion gates
   against the canonical original; otherwise leave the pointer unchanged.
5. Enforce a 30-minute wall-clock cap for all trials combined. Search itself
   must be bounded and terminate. Preserve original EasyEDA project. No rule
   relaxation, footprint or zone edit, engine/wire change, or public geometry.
   No stage, commit or push.

## Evidence

DeepSeek owns private phase-19 search/trial scripts and evidence, public
`phase19/RESULT.md`, and aggregate HISTORY/CHANGELOG additions. Add focused
tests for any public code; run the strict harness gate if public code changes.
Record accepted pointer/generation/hash before and after, search coverage of
49/49, path and independent-check counts, every trial's closure/DRC/copper
state, and any exact blocker. Astra reviews and chooses the next intervention.
