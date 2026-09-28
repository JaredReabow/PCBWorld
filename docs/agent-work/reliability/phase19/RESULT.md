# Phase 19 - bounded 2D path test

Status: **ready for review.** A read-only, bounded 2D clearance-aware search over
all 49 phase-18 `connection_not_verified` edges, on the unchanged accepted
generation. **No edge produced a candidate path in any of the five runs or
either window, so no transactional trial was run** - the contract makes trials
conditional on a credible path. Nothing was routed, no board file was written,
no checkpoint was taken, and the accepted pointer and hash are unchanged.

Contract: [PLAN.md](PLAN.md). Predecessor: [phase18/RESULT.md](../phase18/RESULT.md).

This report carries aggregate counts, method parameters and limits only. Board
measurements - rule values, per-edge coordinates and distances, and the geometry
of individual components - stay in the private evidence tree, as the phase-19
plan requires.

## What was searched

Each offered edge gets a lattice over a window that follows its offered segment -
**4 mm to each side and 2 mm beyond each end**, escalated to **6 mm / 4 mm** for
edges the primary window cannot connect - at a **0.25 mm** pitch, with both
offered anchors forced onto the lattice. Moves span two lattice steps so every
piece's midpoint is itself a measured sample. Every copper layer a through via
spans is in the graph: an on-layer move stays on its layer, and a via is a
transition at one point that must be clear on all of them, fail closed. The four
coincident cross-layer edges declare no direction to offset along, so their
window is the axis-aligned square of the same half-size around the shared point.
The layers, the window, the pitch and the allowance are method settings; the
margin itself is derived from the board's own default netclass (clearance plus
planned track half-width on a layer, clearance plus planned via radius on the via
column), and those values are board measurements kept in the private evidence.

The clearance test at each measured sample is: no foreign or **netless**
track / pad / via copper inside the margin (each item at its own shape), no
foreign or netless **pour fill** inside it (the engine's read-only zone query),
and no rule-area **keepout**. Own-net copper is not an obstacle, so an endpoint
standing on its own proved copper is not read as blocked. A sample the engine
cannot resolve is **blocked and counted**; 177 such lattice samples occurred
across the 49 windows. The declared sampling allowance is 0.025 mm - half the
independent checker's dense spacing, applied under the 1-Lipschitz bound, so
samples that clear the margin plus the allowance prove the piece between them
clears the margin.

## Two item models, because phase 18's pad model overstates this board

Phase 18 modelled every pad as the circle of its longer dimension. For the
elongated pads this board is full of, that claims copper far beyond the pad's own
extents: it is a safe obstacle, but a very loose one, and it is why the phase-18
band refused most anchors before any routing question was asked. The search
therefore carries both the harness's own shape-aware extents and the phase-18
apron, and runs both.

| run | item model | threshold |
|---|---|---|
| `per_element` | harness extents | track margin on a layer, via margin on the via column |
| `strict` | harness extents | via margin at every sample (the phase-18 margin value) |
| `apron` | phase-18 circle apron | track margin on a layer, via margin on the via column |
| `apron_strict` | phase-18 circle apron | via margin everywhere (the phase-18 proxy) |
| `endpoint_relief` | harness extents | as `per_element`, but the two offered anchors are held to the un-inflated margin |

`endpoint_relief` exists because an offered anchor is an exact measured point on
its own proved copper, not a sample standing in for the piece around it, which is
what the allowance exists to bound. Without it, most of that run's anchor
failures are decided by the sampling allowance rather than by the board.

## Counts

| | |
|---|---:|
| offered edges | 49 |
| joined to the current board by exact key | **49** |
| searched | **49** |
| edges with a candidate path, any run, primary window | **0** |
| edges with a candidate path, any run, escalated window | **0** |
| unknown lattice samples inside the windows | 177 |

Per run, primary window (the escalated window reproduced every count exactly, so
widening the window connected nothing):

| run | found | no_path | start_blocked | target_blocked |
|---|---:|---:|---:|---:|
| `per_element` | 0 | 30 | 8 | 11 |
| `endpoint_relief` | 0 | 40 | 4 | 5 |
| `strict` | 0 | 8 | 29 | 12 |
| `apron` | 0 | 10 | 27 | 12 |
| `apron_strict` | 0 | 8 | 29 | 12 |

The apron runs block 39 and 41 of the 98 anchors: at that margin the phase-18
apron of a neighbouring elongated pad swallows the anchor, which is the direct
explanation of phase 18's "no clear lane" result. The `strict` run blocks 41
anchors, because a via-sized margin exceeds most pads' own clearance to the
surrounding pour. Under the harness's own extents and the un-inflated anchor
margin, 9 anchors are still blocked outright - a few by **netless** copper
overlapping the anchor itself, the rest by pour just inside the margin - and the
other 40 edges have both anchors usable and still no path: the start and the
target sit in different proxy-clear regions of the window, even with layer
changes allowed wherever a through via is clear on all its layers.

## The machinery does find paths on this board

"No path" is only evidence if the same search, on the same board, with the same
parameters, finds a path where the board has room. A positive control walks a
deterministic 2 mm grid of candidate origins, asks the engine's own clearance at
samples along a 5 mm strip, and stops at the first open one (its position stays
in the private evidence). **All five runs find that strip** at exactly 5.0 mm
with 2 corners - including the strict and phase-18-proxy runs, with every pour on
the strip counted as foreign, which is the strictest form of the proxy. The 49
"no path" answers are therefore a property of the board at those windows, not of
a search that cannot find paths.

## Independent check

A second script runs in a fresh process with its own engine, session, zone cache
and clearance arithmetic (a raw O(items) scan, no shared index or field). It
re-derives the offered edge by exact key, re-identifies both endpoints with the
engine's own point-identity rule, checks continuity and layer transitions, and
dense-walks every retained piece at 0.05 mm at the margin plus the allowance.

With no candidate path on any of the 49 edges there was nothing to check there,
so the checker's dense walk was exercised on the positive control's path: **101
samples, 0 failures**, with clear margin at every sample (the measured slack is
private). The two geometry sources were also cross-checked: the pad and via
inventories agree between the harness and the board file, their track difference
is accounted for by arc copper that the file reader does not count as segments,
and pad extents agree directly or with the axes swapped. All three counts are
private.

The search itself was tested on synthetic geometry, where the answer is known by
construction: 14 assertions covering an open window, a wall with a gap (walked
around, and the detour is longer than the straight line), a wall across the whole
window (`no_path`), a cross-layer edge needing a clear via column (connected
through the column, and `no_path` when no column is clear), determinism, and the
corner simplification. All pass.

## How wide is each blockage

A supporting read-only diagnostic grows the reachable set from each anchor with
the same move rule and reports the closest approach between the two sides, as a
count of edges per separation band:

| closest approach between the two sides | edges |
|---|---:|
| 0 mm - the sides meet at one point on **different layers** | 4 |
| 0 .. 0.5 mm | 0 |
| 0.5 .. 1.0 mm | 1 |
| 1.0 .. 2.0 mm | 12 |
| 2.0 .. 4.0 mm | 3 |
| over 4.0 mm | 29 |

The four zero-gap edges are the coincident cross-layer pairs: their two sides
reach the same point on different layers, and only the absence of a clear through
via column there stops the route - phase 18's "0 of 8 cross-layer edges have a
via spot", reproduced and now located to a point. The separation is the distance
between two *proxy-clear* regions inside the sampled window; it is not a measure
of what a real route would need, and on its own it does not establish that any
particular design change is required.

## What this does and does not say

It does say that under a sampled clearance proxy, inside a declared finite
window, with every copper layer a through via spans and a fail-closed via rule:
no offered edge of the phase-11 `connection_not_verified` class has a continuous
candidate path, in either the harness's own pad model or the conservative
phase-18 one, and widening the window from 4/2 mm to 6/4 mm changed nothing.
Phase 18's open question is narrowed rather than closed: **expanding from the 1D
leg and its lanes to this bounded 2D proxy did not find a path.** That is a
statement about this window, this sampling and this proxy - not a demonstration
that the 1D geometry, or the plan, was the binding constraint.

It does not say that no route exists on the board. The window is finite; the
margin is a proxy, and the applicable clearance where conditional custom rules
apply, plus hole-to-hole clearance, are still not readable through this engine
(phase 15); pour distance is sampled; and the harness's pad-extent convention was
confirmed against the measured part geometry of two multi-pin footprints on the
board - their identities and dimensions are private - rather than from the
engine's compiled source, with the phase-18 apron run carried as the bracket that
holds regardless of that convention. A candidate path found this way would not
have been a legal one: only the native DRC can call copper rule-clear.

## Trials

**None were run, because no edge produced a credible path.** The contract makes
bounded transactional trials conditional on that; with 0 of 49 the trial
machinery has nothing to test, and no route was started, no DRC was run, no
checkpoint was taken and the accepted pointer is unchanged. Running a trial on an
edge with no candidate would be an unbounded re-roll, not a measurement of the
candidate.

## Verification

| command | exit | result |
|---|---:|---|
| private search, read-only, both windows | 0 | 49/49 offered, joined and searched; 0 paths in 5 runs |
| private search re-run, final scripts, same arguments | 0 | aggregate identical: all 24 status counts, 0 found, accepted still unchanged |
| search unit tests (synthetic geometry) | 0 | 14/14 assertions, `RESULT pass` |
| private positive control | 0 | an open strip is found, and all 5 runs connect it at 5.0 mm |
| private independent checker | 0 | 0 candidate paths to check; control path 101 samples, 0 failures; inventory cross-check clean (counts private) |
| private gap diagnostic | 0 | separation band counts as tabled above, 49/49 edges measured |
| accepted generation sha256 | - | `6c4f8ab81b83...f593477cf1b`, unchanged; artifact and scratch hashes identical before/after; `checkpoint_count()` 0 -> 0; no commit, stage or push |

No public harness file was changed, so no public test was added or altered and
the strict harness gate was not re-run for this phase. The search, its tests,
the control, the checker and the gap diagnostic live in the private tree and are
re-runnable as written there, with the per-edge geometry and rule values.

## Bounded next recommendation

Two bounded questions, neither of them a routing campaign and neither of them an
inference from the separation counts alone:

1. **Via-column question (4 edges).** For the coincident cross-layer pairs, whose
   two proxy-clear sides meet at one point on different layers, ask why no
   through via is clear there and what - if anything the harness may change -
   would make one clear. This is a question about the via, not about a corridor.
2. **Corridor question (the remaining 45 edges).** For the edges whose two sides
   are separated inside the window, decide whether the next probe should widen
   the search, change the plan geometry, or change the physical design - with the
   separation band as a *lower* bound on the distance between proxy-clear
   regions rather than as a mandate for a placement change.

Neither question follows from history: no phase-17 record and no pre-field record
was read for this result.
