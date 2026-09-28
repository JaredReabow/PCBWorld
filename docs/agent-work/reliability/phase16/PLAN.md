# Phase 16 - the via's real layer span

Status: in progress (2026-09-28). Phase 15 is the accepted predecessor
([RESULT.md](../phase15/RESULT.md)); this phase's outcome is in
[RESULT.md](RESULT.md).

## What phase 15 left

Astra deferred the proposed `probeRules()` interface and, in the same review,
accepted the phase-15 finding that matters more: the opening screen asked only
about the two copper layers a plan changes between, while a via's barrel and hole
exist on every layer it spans. Re-screening with every layer checked left one of
sixteen edges with an opening instead of four. This phase fixes the screen itself.

## Contract

1. **Span, not endpoints.** `zone_coverage` gains `via_layer_span()`, which reads
   the layers a via spans from the **engine's own layer map and order**, and
   `find_openings()` asks every one of them per corridor sample. Interior foreign
   copper disqualifies an opening exactly as an endpoint's does.
2. **Fail closed.** An unreadable or self-inconsistent layer map, or any spanned
   layer whose answer is unknown, refuses the opening (and the screen reports the
   edge as `unknown`) rather than being skipped. The span is the whole copper
   stack, which is a superset of any partial via pair: it can only refuse more.
3. **Bounded and read-only.** At most one query per (corridor position, spanned
   layer): a layer-changing leg's own duplication is deduplicated before the
   query. Nothing is mutated.
4. **Same margin as before.** This phase changes *coverage*, not the clearance
   numbers, so the re-screen is comparable with the earlier result: the same old
   lower-bound margin, reported before and after.
5. **Execute what remains.** The single remaining candidate is tested from the
   exact accepted board with a fresh engine and session, using a wider candidate
   window so it actually runs without weakening the generic refusal-aware policy.
   The authoritative fields are read (`drc_delta`, `connected_before_refusal`).
6. **Promotion only through the immutable store**, and only if something closes.
   Otherwise the pointer stays put and the report says what remains.
7. **No engine or wire changes, no rule guessing, no rule relaxation**, no
   footprint moves, no zone deletion, no commit, stage or push. Public docs carry
   no geometry, net names or rule values.

## Evidence

* Fix: `pcb_world/agent/zone_coverage.py` (`via_layer_span`, `find_openings`,
  `screen_openings`, `EdgeScreen.layers_checked`).
* Tests: `tests/agent/test_zone_point_query.py` (interior-zone disqualification on
  a four-layer board, two-layer compatibility, no-zone board, bounded query
  count, no mutation) and `tests/agent/test_zone_prefilter_unit.py` (span fail
  closed, inconsistent map, board order).
* Tool: `tools/reliability/screen_zone_openings.py`.
* Private evidence: `phase16_routing/` (re-screen, trial, notes).
