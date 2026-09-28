# Phase 21 - component-proved alternative anchor pairs

Status: **ready for review.** A read-only search over every pair of proved
same-net anchors behind the 49 phase-11 `connection_not_verified` edges, on the
unchanged accepted generation. **No pair produced a candidate path**, and after
the bounded escalation the coverage statement is exact: every one of the pairs
the search evaluated resolved to a measured refusal. Nothing was routed, no board
file was written, no checkpoint was taken, no DRC was run, and the accepted
pointer and hash are unchanged.

Contract: [PLAN.md](PLAN.md). Predecessors: [phase18](../phase18/RESULT.md),
[phase19](../phase19/RESULT.md), [phase20](../phase20/RESULT.md).

This report carries aggregate counts and method parameters only. Board
measurements - net names, coordinates, rule values and per-edge geometry - stay
in the private evidence tree, as the plan requires. Nothing here is native DRC
authority: a point this search calls clear is clear **under the measured proxy**.

## What was asked

Phase 18 proved that every one of the 49 edges has proved substitutes on its own
net, but never joined two of them. Phase 19 asked whether a continuous path
exists inside a *window* around the offered segment and found none. Phase 21 asks
the question those leave: with **both** endpoints free to move to any
native-proved same-net anchor, is there a straight candidate path between any
pair, where the two sides are still not connected?

## The anchors

Every net's whole anchor list was taken from the harness's native component API -
the API's own rotation window was opened all the way, so nothing was hidden by
it - and every anchor was then proved independently with the engine's own
point-identity rule and its own connectivity cluster. An anchor inside a rule
area, or one the zone query could not answer, was refused rather than guessed;
duplicate points were folded; a point claimed by two different components was
refused.

| | |
|---|---:|
| offered edges, joined to the board by exact key | 49 / 49 |
| native components walked | 201 |
| anchors the component API lists (both sides, summed) | 1 414 |
| anchors refused during proof (distinct points) | 11 |
| offered points the API had not listed, added | 29 |
| **anchors proved and enumerated (both sides)** | **1 432** |
| raw anchor-pair product (the API's own counts) | 33 071 |
| **enumerated anchor pairs (the search's pools)** | **33 092** |

Those last two figures are the aggregate arithmetic, and the difference is
exactly the pool rule rather than a second anchor set. Per side,
`pool = raw - refused - duplicate points + offered point added`, re-derived edge
by edge with explicit counters by a private reconciliation script whose residual
is 0 across all 49 edges and whose product reproduces the published 33 092
exactly. The component API's own rotation window was opened all the way, so no
anchor it lists was hidden; the 29 additions are the offered point itself, which
phase 18 proved is copper of the scheduled net at 98 of 98 anchors. (The private
search artifact counts refusal *events*, and its 12 is one more than the 11
distinct points because one offered point is refused twice - once as the
component anchor the API lists, once when the search re-offers it. The
reconciliation script counts points, which is what makes the identity exact.
Coincidentally 21 edges have a pool that differs from the raw count; that is not
the +21 pairs, which is the product of the per-edge changes.)

## The pairs

A pair is measured only when the two anchors' own clusters are disjoint - the
native proof that the two sides are not already connected - and the two anchors
do not belong to one component.

| | |
|---|---:|
| pairs enumerated | 33 092 |
| refused: the two sides are already connected | 10 219 |
| refused: one component | 1 597 |
| **pairs evaluated** | **21 276** |
| pairs left unexamined by the budget | **0** |
| headline runtime | 119.7 s |

How a pair is measured is in the private method note; in outline, discrete copper
is measured exactly by segment-to-shape distance, pour fill and rule areas are
sampled at the declared **0.125 mm** pitch under the 1-Lipschitz allowance
(densified to **0.05 mm** inside 0.5 mm of each anchor, where a pad's own
clearance void changes fastest), and a cross-layer pair is bounded by two prefix
walks whose union *is* the segment before any via column is tested.

## Outcome: 0 candidates

| outcome | pairs |
|---|---:|
| **candidate** | **0** |
| blocked by a copper item | 3 134 |
| blocked by a pour inside the requirement | 6 447 |
| cross layer: the two legs' clear prefixes do not meet | 11 682 |
| cross layer: legs meet, but no clear through-via column | 5 |
| not proved at the declared pitch (same layer) | 8 |

Read as three findings rather than one:

* **Within this candidate family, the straight leg leaving a pad is what binds,
  not the via.** The clear prefix of the 23 374 measured legs is dominated by
  **0.5-1.0 mm** (13 589 legs), then 0.1-0.5 mm (4 335), 1-2 mm (2 445),
  2-5 mm (988), over 5 mm (549), under 0.1 mm (493) and exactly zero (975). Only
  **5** pairs got as far as having a feasible via interval and then found no
  clear column at the via requirement - the opposite of the phase-20 picture at
  the four offered coincident points, where the column was the whole problem.
  This is a statement about the *straight line and one through via* this phase
  measures: it says the first millimetre of a straight run is what stops these
  pairs, not that a path leaving a pad and turning is impossible, and not that
  the board needs changing.
* **Same-layer pairs stop on measured contact, and it is usually not a track.**
  Of the 9 581 same-layer refusals, 6 447 are pour inside the requirement, 1 820
  are foreign vias, 1 007 are foreign tracks and 307 are pads.
* **Only 8 pairs were left unproved by the instrument**, all same-layer.

## The escalation, and why it matters

A sampled proof costs something: every sample must clear the requirement plus
half its local spacing, or the piece between samples is not proved. The main run
therefore records, for every pair, whether the instrument rather than the board
stopped it, and re-measures exactly those pairs finer:

| pass | pitch | pairs re-measured | candidates | still unproved |
|---|---:|---:|---:|---:|
| main | 0.125 mm | - | 0 | 8 |
| 1 | 0.05 mm | 8 | 0 | 8 |
| 2 | 0.025 mm | 8 | 0 | **0** |

After pass 2, **21 276 of 21 276 evaluated pairs resolve to a measured refusal or
a candidate**, with 0 unexamined and 0 unproved. The escalation is what turns
"no candidate at this pitch" into a statement about the board.

Two defects were found by cross-checking during the phase and fixed, and both
are described generically here with their measurements left in the private
evidence: a 1-Lipschitz prefix bound applied in the direction that *overstates* a
clear prefix, caught by the independent checker on a corridor running almost
parallel to foreign copper where the search claimed more clear leg than the board
has; and a walk that stopped at the first allowance-band sample instead of
looking for a real contact a fraction of a millimetre further on, which had
mis-labelled a majority of the evaluated pairs as instrument artefacts. Neither
was visible from the search's own output.

## Independent check

A second script runs in a fresh process with a fresh engine, a fresh scratch copy,
its own flat inventory, its own cell index, its own shape arithmetic and its own
segment-to-segment routine. It re-derives the 49 edges by exact key, re-proves
both anchors with the engine's point-identity rule, re-proves the separation with
the engine's cluster query, and dense-walks every row at **0.05 mm** against the
requirement itself.

**4 000 rows and 4 206 prefix comparisons:**

| relation | count |
|---|---:|
| agree within one dense sample | 2 769 |
| search conservative - the checker found *more* clear path | 1 437 |
| **search over-reports - the checker found less** | **0** |

## Positive controls

"No candidate among 21 276 pairs" only means something if the same code finds
clearance where the board has it.

| control | result |
|---|---|
| the open strip phase 19 used, walked by this phase's code | clear, full length, 57 samples |
| the same two points through the whole pair check | `candidate` |
| a 1 mm grid scan for a clear through-via column on all four copper layers | **94 of 11 771 grid points** |

## Verification

| command | exit | result |
|---|---:|---|
| private unit tests | 0 | `RESULT pass (54 assertions)` over the geometry, the walk and the pair verdicts |
| private search, read-only | 0 | 49/49 joined, 33 092 pairs enumerated, 21 276 evaluated, 0 candidates, 0 budget stops, 119.7 s |
| private escalation ladder | 0 | 8 -> 8 -> 0 unproved; 0 candidates; 0 unexamined |
| private independent checker | 0 | 4 206 comparisons, 0 over-reports, 0 disagreements |
| private positive controls | 0 | strip clear and reported as a candidate; 94 clear via columns |
| private pair-math reconciliation | 0 | residual 0 across all 49 edges; pool product equals the published 33 092 |
| review correction (public wording only) | 0 | a per-leg defect magnitude was removed from RESULT/HISTORY/CHANGELOG (it is board geometry, not an aggregate), the two pair totals are reconciled, and the binding-constraint claim is now scoped to this candidate family; `git diff --check` clean and a targeted leak scan returns nothing. No analysis artifact, search, checker, test or engine file changed, so nothing was re-run |
| accepted generation sha256 | - | `6c4f8ab81b83...f593477cf1b` unchanged; artifact and scratch re-hash identically; `checkpoint_count()` 0 -> 0; pointer unchanged; no commit, stage or push |

No public harness file was changed, so no public test was added or altered and
the strict harness gate was not re-run for this phase. The search, its tests, the
escalation, the checker and the controls live in the private tree and are
re-runnable as written there, with the per-edge geometry and rule values.

## What this does and does not say

It does say that, on the unchanged accepted generation, **not one of the 21 276
unconnected anchor pairs has a straight candidate path** - with the discrete
copper measured exactly and the pour sampled to a proven bound - in either the
harness's own shape-aware item model or with the escalation's finer pitch, and
that the refusals are attributed: the first millimetre away from a pad is where
most of them stop, and only five pairs ever reached a feasible via interval.

It does not say the board is unroutable, and it does not say these connections
cannot be made. The candidate geometry here is a **straight segment** (plus one
through via). A route that leaves a pad, clears its neighbours and then runs is
exactly what a router does and exactly what this search does not model; phase 19
searched a window around the *offered* segment and also found nothing, but a
window around *substitute* pairs has not been searched. The clearance values are
a proxy, native DRC was not run, the pour distances come from the fill the loaded
board carries, and hole-to-hole clearance is not measured. A substitution joins
two components of the same net: it is a candidate connection, never a resolution
of the offered ratsnest edge.

## Bounded next recommendation

The evidence points at one question, and it is about plan geometry rather than
about anchors, vias or the engine. Both parts below are options to weigh, not
conclusions: this phase measures straight runs and one through via, and nothing
in it establishes that a turning route would succeed, or that the board must
change.

1. **Whether a candidate is allowed to leave a pad and then turn.** Within the
   family measured here, every cross-layer pair fails on a leg that stops within
   about 2 mm of its anchor in the straight direction, and 6 447 same-layer pairs
   stop on pour contact along a line. A bounded *dogleg* test - one turn inside
   the anchor's own clearance void, then a straight run - is the smallest
   extension of this measurement that would answer whether the connection is
   reachable at all without changing the board. If it finds nothing either, that
   is still a statement about one candidate family rather than a proof about the
   board.
2. **Whether the question moves to the physical design.** Routing room, a
   different layer assignment or a local pour/placement change near the specific
   anchors this phase measured are the remaining places the answer could come
   from, and they are all questions with manufacturing consequences rather than
   actions this result requires. The per-edge anchors behind any such decision
   are in the private evidence.

Neither step is a routing campaign, and neither is established as required by
this result.
