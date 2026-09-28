# Phase 21 — component-equivalent anchor substitutions

Status: assigned. Phases 18–20 are accepted as bounded diagnoses of the exact
offered anchors; no board promotion occurred.

## Question

Can any of the 49 `connection_not_verified` edges be closed by starting or
ending on a different, native-proved same-net copper anchor from the same
connected component, rather than insisting on the offered point? A substitute
counts only if the component relation is proved and the two sides are not
already connected.

## Contract

1. Reconstruct the exact 49 offered edges on the unchanged accepted generation.
   Enumerate substitute anchors through the harness's native component API,
   deduplicate and prove each one's component membership and peer separation.
   Reject ambiguous, keepout and already-connected substitutes. Do not equate
   shared net name alone with a valid substitute.
2. Run a deterministic bounded search over alternate anchor pairs, prioritizing
   the four coincident cross-layer edges and then the other 45. Reuse or improve
   phase-19 measured proxy with continuous segment and through-via checks. Cap
   pair evaluations and wall time; report coverage and unexamined pairs rather
   than implying exhaustive search. The public result has aggregate counts only.
3. Independently check any candidates. If one or more credible candidate paths
   appear, at most three distinct bounded transactional trials (zero model
   requests) with native DRC, connection verification, rollback and terminal
   evidence. A path or closure is not acceptance. Promote only through complete
   existing native DRC, terminal partition and pinned KiCad CLI gates against
   the canonical original; otherwise preserve the accepted pointer.
4. No layout/rule/zone/footprint edits, no engine/wire change, no broader routing
   campaign, no stage/commit/push. Keep per-edge nets, coordinates, measured rule
   values and individual geometry in the private tree.

## Evidence

DeepSeek owns private phase-21 scripts/evidence and public aggregate RESULT.md,
HISTORY/CHANGELOG entries. It must record exact 49-edge coverage, pair-search
budget/coverage, independent checker results, trial outcomes, accepted board
digest and pointer before/after, and a concrete next decision. Astra reviews.
