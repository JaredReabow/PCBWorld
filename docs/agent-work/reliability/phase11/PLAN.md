# Phase 11 - a zone question for the refusal-driven search

Status: implemented and verified (2026-09-28). Phase 10 is the accepted
predecessor ([RESULT.md](../phase10/RESULT.md)); this phase's outcome is in
[RESULT.md](RESULT.md).

The first pass over this phase established by research that the pinned engine
had no trustworthy read-only way to ask what zone copper covers a point. That
finding went to the orchestrator with a proposed interface instead of into the
code, and the review returned an approval **with five amendments**, all of which
are part of the contract below.

## What phase 10 left on the table

Phase 10 attributed its refusals and aimed two new candidate families at them.
Both families ran - 327 refusal-derived evaluations over 16 of 43 refusal edges -
and closed nothing. Its own diagnosis says why: the refusals are dominated by
**the plan's own via landing against a foreign copper pour**. The two families
model tracks, vias and pads; neither can see a zone's boundary or which net a
pour belongs to, so a plan can close a connection by dropping a via into someone
else's copper and only the native gate ever notices.

The question this phase has to answer, before any search work:

> Given a point and a copper layer on the *live* board, is that point on filled
> zone copper, and is that zone's copper the candidate's own net or another net?

It has to answer for islands, for voids inside a fill, for a zone that spans
several copper layers, for rule-area keepouts, and it has to be honest when the
stored fill cannot be proved current.

## Contract

1. **One read-only native accessor, and one wire type pair.** The engine gains
   `get_zone_point_hits()`; the mirrored `wire.py` copies gain `ZonePointHit` /
   `ZonePointResult`. Nothing else in the engine changes, no existing accessor
   changes shape, and the protocol modules stay byte-identical.
2. **Every covering zone is returned (amendment 1).** A point and layer can be
   covered by several zones at once. The answer carries *all* of them with their
   nets, plus an explicit copper classification that reports a multi-net overlap
   as `fill_multi_net` and names no single net for it. A conflict is never
   resolved silently.
3. **No currency claim (amendment 2).** `fill_provenance` is
   `loaded_unverified`, `no_fill` or `unknown`. KiCad clears its own refill flag
   when a board is parsed and keeps no fill hash across processes, so the
   engine cannot prove the stored fill matches the loaded rules. There is no
   boolean named `fill_current`, and no consumer may read "loaded" as "safe".
4. **Advisory, and ranking-first (amendment 3).** The prefilter reorders a
   sweep and removes nothing from the candidate list it is given; unknown passes
   through in its original position. It is not a shield from the sweep's own
   budget - the runner truncates to `candidate_limit` *after* ranking, so a plan
   moved late can miss the attempt, and the report says so rather than claiming
   every ranked-late plan still gets a transaction. The transactional native DRC
   remains the only authority on whether copper may be kept, and no promotion
   gate reads the prefilter.
5. **Real geometry, not just a point (amendment 4).** Each hit carries the
   engine's own `distance_mm` to that zone's filled copper, so a caller tests a
   copper object of radius R needing clearance C as `distance_mm < R + C` — a
   circumference test, not a centre test, using the same
   `SHAPE_POLY_SET::SquaredDistance` primitive the DRC measures clearances with.
   Point results are still not a complete collision test against the whole
   board, and the code says so.
6. **Opt-in, off by default.** No control run has yet shown the ranking to be an
   improvement, so `RunnerConfig.zone_prefilter` defaults to False and the
   campaign driver requires an explicit flag. A future phase can then run a
   paired control (on/off) over the same pairs and report the difference.
7. **Bounded cost, cached by fill generation.** A query carries its own
   `window_mm`; the prefilter asks for a bounded set of points per candidate
   (its waypoints plus a capped sample along its own start→target corridor) and
   caches every answer for as long as the zone fill cannot have changed — a
   router's own copper edits never re-pour, so the cache lives for the run and
   is dropped by an explicit invalidation or a refill-epoch bump.
8. **Test the real cases**, not a happy path: overlapping zones, a void inside a
   fill, a zone spanning several copper layers, rule-area keepouts, islands,
   unfilled/stale pour, no-zone board, non-copper layer, an engine without the
   accessor, and the cache/epoch lifecycle.
9. **One bounded deterministic campaign** from the exact accepted generation,
   zero planner requests, reporting evaluated refusal edges, candidate counts,
   accepted closures and rollback, and the accepted pointer/hash before and
   after. Promotion only through the immutable store: fresh child, full native
   DRC, terminal partition, complete pinned CLI gates against the canonical
   original. If nothing closes, the pointer stays where it is.

## Evidence

* Engine patch: `patches/engine/0003-zone-point-query.patch` (described in
  `patches/engine/README.md`).
* Engine surface: `engine/kicad-patches/rl/pns_rl_router.{h,cpp}`,
  `engine/kicad-patches/rl/pns_rl_bindings.cpp`, and the mirrored
  `engine/engine_server/wire.py` / `pcb_world/engine/wire.py`.
* Environment: `pcb_world/engine/kicad_engine.py` (`get_zone_point_hits`),
  `pcb_world/agent/zone_coverage.py` (the prefilter), `pcb_world/agent/runner.py`
  (`_zone_ranked`, the config knobs, the run evidence).
* Tests: `tests/agent/test_zone_point_query.py` (native),
  `tests/agent/test_zone_prefilter_unit.py` (policy without an engine).
* Phase gate: `bash tools/reliability/check_phase.sh --strict`; patch proof:
  `python tools/reliability/check_engine_patches.py`.
* Existing zone consumer: `pcb_world/agent/observations.py` (`zone_anchor`).
* Private campaign evidence: the `phase11_routing` tree beside the V3 board
  (work copy, run logs, the run's own `metrics["zone_prefilter"]`).
