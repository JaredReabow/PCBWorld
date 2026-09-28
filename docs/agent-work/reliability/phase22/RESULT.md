# Phase 22 - bounded alternate-anchor doglegs

Status: **ready for review.** A read-only search over a declared, finite dogleg
family between component-proved same-net anchor pairs behind the 49 phase-11
`connection_not_verified` edges, on the unchanged accepted generation. **No
pair produced a candidate**, so no transactional trial was run. Nothing was
routed, no board file was written, no checkpoint was taken, no DRC was run, and
the accepted pointer and hash are unchanged.

Contract: [PLAN.md](PLAN.md). Predecessors: [phase18](../phase18/RESULT.md),
[phase19](../phase19/RESULT.md), [phase20](../phase20/RESULT.md),
[phase21](../phase21/RESULT.md).

This report carries aggregate counts and method parameters only. Board
measurements - net names, coordinates, rule values and per-edge geometry - stay
in the private evidence tree, as the plan requires. Nothing here is native DRC
authority: a point this search calls clear is clear **under the measured proxy**.

## What was asked

Phase 21 joined every proved same-net substitute on one side of an edge to every
proved substitute on the other and measured a **straight** candidate path for
each pair: 21 276 pairs evaluated, 0 candidates, and the clear prefix of the
straight leg leaving a pad was, within that family, the binding term. Phase 22
asks the smallest question that leaves: if a route is allowed to leave a pad in
a *different direction* before turning toward the other side - a short escape,
one or two bends and a main leg, with at most one through via - does any pair
produce a candidate?

## The pool, and the pairs this phase chose

The anchors are phase 21's, unchanged: every native component's whole anchor
list, each anchor proved with the engine's own point-identity rule and its own
connectivity cluster, keepout and unresolvable anchors refused rather than
guessed. Re-deriving them here reproduces phase 21's pool exactly.

| | |
|---|---:|
| offered edges, joined to the board by exact key | 49 / 49 |
| raw anchor-pair product (the component API's own counts) | 33 092 |
| refused: the two sides are already connected | 10 219 |
| refused: one component | 1 597 |
| refused: degenerate | 0 |
| **eligible pairs (disconnected, component-proved)** | **21 276** |
| **pairs selected for the search** | **560** |
| edges with fewer than 16 eligible pairs | 21 |

The eligible count is not a new pool: per edge it equals phase 21's own
`evaluated` count, computed by the previous phase under the same predicate and
re-derived here by a private reconciliation script whose residual is 0 across
all 49 edges. Selection is a deterministic, documented ranking - smallest total
anchor movement from the offered point, then the anchor coordinates - and the
chosen pairs are the head of that ranking, at most 16 per edge; the four
coincident cross-layer edges are searched first, as the contract requires. The
selection is a bounded subset, **not** a claim about all 21 276 pairs.

Layer relation is measured on the pair, not on the offered edge: a substitute
pair can be cross-layer where the offered edge is same-layer. Of the 560
selected pairs, 390 are same-layer and 170 are cross-layer.

## The family

| parameter | value |
|---|---|
| escape headings per anchor | 8 compass headings plus the direction toward the peer anchor |
| escape lengths | 0.4 mm, 0.8 mm, 1.2 mm, plus a zero-length escape |
| escape variants per anchor | 28 (zero first, so every pair's first variant is the phase-21 straight segment) |
| combinations per pair | 784 |
| bends | at most 2; the zero-length escapes fold the two-bend polyline to one bend or to the straight segment |
| via | exactly one through via per cross-layer variant, and it must sit **on** the measured polyline |

Measurement is phase 21's instrument, unchanged: discrete copper (foreign *and*
netless) is measured exactly by segment-to-shape distance; pour fill and rule
areas are sampled at a **0.125 mm** pitch under the 1-Lipschitz allowance,
densified to **0.05 mm** inside 0.5 mm of each anchor; an unresolvable point or a
rule area is a refusal, never a clearance. For a cross-layer variant the two
prefix walks along the polyline - one from each anchor on its own layer - bound
the feasible arc-length window for the via exactly, and only inside that window
is the via column tested on every copper layer a through via spans.

## Outcome: 0 candidates

Every one of the 560 selected pairs was measured, none is unexamined, and no
variant anywhere produced a candidate. Verdicts below are the pair's own best
variant - the family is enumerated in a fixed order and a pair stops at its
first candidate.

| verdict | same-layer pairs | cross-layer pairs |
|---|---:|---:|
| **candidate** | **0** | **0** |
| blocked by a copper item | 230 | 0 |
| blocked by a pour inside the requirement | 156 | 1 |
| cross layer: the two sides' clear prefixes never met | - | 164 |
| cross layer: prefixes met, no clear through-via column | - | 5 |
| not proved at the declared pitch | 4 | 0 |

After the escalation below, the 4 unproved pairs join the pour column, so the
final per-pair accounting is 161 pour, 230 copper, 164 span, 5 column and 0
candidates - 560 of 560 resolved.

**438 563 variants were measured.** Each one records the stage that decided it.
Aggregated over the enumerated family - a variant exists for every
(start escape, target escape) combination, so a pad whose escapes are all
blocked contributes its own 28 refusals:

| stage | variants | share |
|---|---:|---:|
| the escape out of the start anchor | 287 936 | 65.7 % |
| the escape out of the target anchor | 91 764 | 20.9 % |
| the main leg, after both escapes were clear | 36 924 | 8.4 % |
| escapes clear, the two clear prefixes never met | 21 818 | 5.0 % |
| escapes clear, prefixes met, no clear via column | 121 | 0.03 % |
| candidate | 0 | 0 |

Read as three findings rather than one:

* **The first turn away from a pad is still where most of this family stops.**
  379 700 of the 438 563 variants (86.6 %, roughly six in seven) were refused by
  an escape - the 0.4-1.2 mm segment leaving one of the two anchors - not by the
  main leg and not by the via. A pad surrounded by foreign copper inside the
  requirement is not helped by choosing a different heading: every heading of
  the ladder fails there, for 28 of the anchor's variants.
* **A turn does buy length, and it is not the whole story.** Taking each pair's
  *best* variant, 429 of the 560 selected pairs reached more than 1 mm of proved
  clear path - zero 53 pairs, up to 0.5 mm 26, 0.5-1 mm 52, 1-2 mm 115, 2-5 mm
  239, over 5 mm 75. Phase 21's straight legs, over the whole 21 276-pair pool,
  stopped mostly within 2 mm of an anchor. Escaping in a different direction
  therefore does move the binding term outward for many pairs.
* **What replaces it is not a via shortage.** With both escapes clear, 36 924
  variants stopped on the main leg and 21 818 never met at all, while only 121
  variants ever reached a feasible window and found no clear column - 5 pairs
  in total, 3 of them the offered pairs on three of the four coincident
  cross-layer edges. That is the opposite of the phase-20 picture at those four
  points, where the column was the whole problem, and it is a statement about
  the polyline this phase measures.

## Independent check

A second script runs in a fresh process, with a fresh engine on its own scratch
copy of the same accepted board. It uses the independent instrument phase 21
already validated - its own flat copper inventory, its own cell index, its own
distance arithmetic and its own dense walk, sharing nothing with this search -
re-derives the 49 edges by exact key, re-proves both endpoints under the
engine's point-identity rule, re-proves the separation with the engine's cluster
query, rebuilds each recorded polyline from its escape points and dense-walks it
at **0.05 mm**.

**560 rows and 560 prefix comparisons:**

| relation | count |
|---|---:|
| agree within one dense sample | 307 |
| search conservative - the checker found *more* clear path | 253 |
| **search over-reports - the checker found less** | **0** |

## The escalation, and why it matters

A sampled proof costs something: every sample must clear the requirement plus
half its local spacing, or the piece between samples is not proved. The main run
therefore records, for every pair, whether the instrument rather than the board
stopped it, and re-measures exactly those pairs finer.

| pass | pitch | pairs re-measured | candidates | still unproved |
|---|---:|---:|---:|---:|
| main | 0.125 mm | - | 0 | 4 |
| 1 | 0.05 mm | 4 | 0 | 4 |
| 2 | 0.025 mm | 4 | 0 | **0** |

After pass 2, **560 of 560 selected pairs resolve to a measured refusal or a
candidate**, with 0 unexamined and 0 unproved. The escalation is what turns "no
candidate at this pitch" into a statement about the board.

## Positive controls

"No candidate among 560 selected pairs" only means something if the same code
finds clearance where the board has it.

| control | result |
|---|---|
| an open 2 mm-escape / 2 mm-run / 90-degree dogleg on a 2 mm origin grid | found at the 763rd scanned origin, 66 samples along the polyline; the other 762 origins refused, 531 on pour and 231 on copper |
| a clear through-via column on all four copper layers, 4 mm grid | **5 of 703** grid points |

## Verification

| command | exit | result |
|---|---:|---|
| private unit tests | 0 | `RESULT pass (3171 assertions)` over the escape ladder, the polyline walk, the same-layer and cross-layer verdicts, the cached/pruned equivalence with an uncached reference over every variant of four cases, and the selection |
| private search, read-only | 0 | 49/49 joined, 21 276 eligible, 560 selected, 438 563 variants, 0 candidates, 0 pairs unexamined, 763.5 s |
| private independent checker | 0 | 560/560 rows re-proved and re-walked at 0.05 mm; 0 over-reports |
| private escalation ladder | 0 | 4 -> 4 -> 0 unproved; 0 candidates; 0 unexamined |
| private positive controls | 0 | a clear dogleg found on the board; 5 clear via columns |
| private pair reconciliation | 0 | residual 0 across all 49 edges; selection is the head of the ranking; the eligible pool equals phase 21's evaluated pool edge by edge |
| accepted generation sha256 | - | `6c4f8ab81b83...f593477cf1b` unchanged; geometry digest unchanged; scratch copies re-hash identically; `checkpoint_count()` 0 -> 0; pointer unchanged; **0 transactional trials** (the contract makes a trial conditional on a credible candidate, and there is none); no commit, stage or push |

No public harness file was changed, so no public test was added or altered and
the strict harness gate was not re-run for this phase. The search, its tests, the
escalation, the checker, the controls and the reconciliation live in the private
tree and are re-runnable as written there, with the per-edge geometry and rule
values.

## What this does and does not say

It does say that, on the unchanged accepted generation, in a declared finite
family - 28 escapes per anchor, at most two bends, at most one through via, and
the via on the measured polyline - **not one of the 560 deterministically
selected anchor pairs has a candidate dogleg**, with discrete copper measured
exactly, the pour sampled to a proven bound at 0.125 mm and re-measured at
0.05 mm and 0.025 mm where the pitch alone refused, and an independent checker
finding zero over-reports. It also says where that family stops: mostly on the
first turn away from a pad, and then on the main leg - not on the via.

It does not say the board is unroutable, that these connections cannot be made,
or that the 20 716 eligible pairs not selected would also fail. It is one
bounded family: escapes of at most 1.2 mm, at most two bends, one through via,
and a via forced onto the measured polyline. Clearance values are a proxy,
native DRC was not run, pour distances come from the fill the loaded board
carries, and hole-to-hole clearance is not measured. A substitution joins two
components of the same net: it is a candidate connection, never a resolution of
the offered ratsnest edge.

## Bounded next recommendation

1. **The first turn, not the via, is now the measured bottleneck.** Two thirds
   of the family's variants never leave a pad, and a further fifth fail on the
   other side's escape. A bounded next step that stays a measurement is an
   escape search that is *not* bound to a fixed heading ladder - for instance a
   short free-direction escape phase that looks for any clear 0.4-1.2 mm
   direction out of a specific anchor before the main leg is chosen. It would
   separate "there is no direction" from "the ladder did not happen to contain
   one".
2. **Alternatively the question moves to plan geometry.** With both escapes
   clear, the main leg - not the via - is what refuses the variant, so the
   remaining room is in waypoint choice along the run rather than in the via
   column. A bounded mid-leg waypoint family is the natural continuation of the
   same instrument.
3. **A physical-design answer remains an option, not a conclusion.** Routing
   room, a different layer assignment or a local pour/placement change near the
   specific anchors this phase measured per edge are all manufacturing
   decisions; the per-edge anchors behind any such decision are in the private
   evidence, and nothing in this result establishes that a design change is
   required.
