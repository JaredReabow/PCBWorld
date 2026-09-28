# Phase 20 - coincident cross-layer via-column diagnosis

Status: **ready for review.** All four phase-19 coincident cross-layer edges were
diagnosed on the unchanged accepted generation. **No candidate exists under the
measured clearance proxy at or near any of them** - in the declared finite
+/- 3.0 mm window, at the two column models tested (through, and endpoint-span)
and at both margins - so no transactional trial was run and no board was
touched: no route, no native DRC, no checkpoint, no board file written. The
accepted pointer and board hash are unchanged.

Read the whole report with these three limits attached: **native DRC was not
run**, the pour distances come from the **fill the loaded board carries, which is
not verified against the current rules**, and the margins are a clearance proxy
rather than rule authority. Nothing here is a legality finding.

Contract: [PLAN.md](PLAN.md). Predecessor: [phase19/RESULT.md](../phase19/RESULT.md).

This report carries aggregate counts, capabilities, method parameters and limits
only. Board measurements - rule values, per-edge nets, coordinates, pour
distances and item geometry - stay in the private evidence tree, as the phase-20
plan requires. No query result here is native DRC authority; a point this report
calls clear is clear **under the measured proxy**.

## What was asked

Phase 19 located four offered `connection_not_verified` edges whose two proved
endpoints coincide in XY on different copper layers, whose two proxy-clear sides
meet at exactly that point, and where no clear through-via column exists. Phase
20 asks, for exactly those four edges: what blocks the column, is the blocker
copper / pour / keepout / drill / unknown, is there a *legal, documented* harness
option other than the through via the harness already places (the plan's own
wording), and does this need an engine change or a design decision?

## What blocks the four columns

Every column is blocked by a measured object, and the class is reported
separately from "the engine could not say":

| column blocker class | edges |
|---|---:|
| foreign pour fill (zone) | 3 |
| rule area that disallows vias (keepout) | 1 |
| foreign/netless copper item | 0 |
| drill / hole-to-hole | 0 |
| unknown engine information | 0 |
| clear | 0 |

The pour class is decisive for the same reason at each of those three edges: the
through via's barrel exists on every copper layer, and on at least one layer
between the two endpoints the shared point lies **inside** a foreign pour fill,
where a distance of zero means the via would be placed on that pour's copper. At
two of those edges the pour on the endpoint layers themselves is also closer than
the via's own radius plus the board's clearance, so the column fails there even
with the sampling allowance removed - which is why phase 19 saw those two as
`no_path` (both anchors usable) rather than `blocked`. At the fourth edge a rule
area on the target layer disallows vias outright, and the start layer separately
carries a foreign pad just inside the inflated via margin while remaining
comfortably outside the track margin.

Drill is measured and clear at all four points: the nearest via barrel clears the
board's own hole-to-hole minimum at every one of them, and no through-hole pad
lies within 2 mm of any of the four points (through-hole pads expose no drill
through this engine, so they are reported as unmeasured, never as clear).

Nothing was attributed to history: no phase-17 record and no pre-field record was
read for this result.

## The bounded candidate probe

Each edge got an axis-aligned square around its shared point at a **0.05 mm**
pitch - **1.5 mm** half-size, escalated to **3.0 mm** - with every point measured
on all four copper layers through the engine's read-only zone query and the
harness's own shape-aware item model. Two candidate classes were measured: a
**through** column, clear on every layer a through via spans (the phase-19 rule,
fail closed), and a **span** column, clear only on the layers from the lower to
the upper endpoint layer - the bracket for a blind/buried via the harness cannot
currently place. Each class was evaluated at the allowance-inflated margin and at
the un-inflated one, so "blocked by the board" and "blocked only by the sampling
allowance" are separate answers. A column-clear point then had both straight legs
back to the offered point sampled at the same pitch and tested at the track
margin on their own layers.

| measurement | result |
|---|---:|
| edges diagnosed | 4 / 4 |
| point-layer measurements | 293,792 |
| points clear for a through column, either window, either margin | 0 |
| points clear for a span-restricted column, either window, either margin | 0 |
| points the engine could not resolve | 0 |
| candidate points passing the two-leg test | 0 |

Widening the window from 1.5 mm to 3.0 mm changed nothing, and removing the
sampling allowance changed nothing. The refusal is the board at those points, not
the search's parameters - and the same read-only machinery finds clear ground
elsewhere on this board (phase 19's positive control), so "no proxy-clear
candidate here" is a measurement of this window, not an inability to measure.

Because no candidate emerged under this proxy, **the contract's trial branch
never opened**: a closed-then-refused route on a point with no proxy-clear column
would be an unbounded re-roll, not a measurement of a candidate. Nothing was
routed, no native DRC was run, no checkpoint was taken and the accepted pointer
is unchanged.

## What the harness can place (source-backed)

The via capability was read from the pinned engine's own C++ and from the
harness's own action schema, not inferred from a wrapper field or a rendered
image. `phase20_capability.py` re-derives each statement and machine-checks it;
22 of 22 checks pass. The extension is paired with its sources by content: the
engine's own patch-tree content hash matches the stamp written next to the
extension, the build tree named by the CMake cache is the one read, and every
router file the patch overlay provides is byte-identical to the copy in that
build tree.

| question | source-backed answer |
|---|---|
| via type the placer builds | through vias only. The router's size settings initialise the via type to `THROUGH` and are never re-typed by the headless path |
| layer span | the whole stack, top copper to bottom copper, for a through via. The layer-pair map stays empty, and the placer's span is derived from it only for a non-through type |
| who could set a non-through via | only the interactive GUI tool sets a layer pair; the headless RL interface has no via-type, layer-pair or span control at all |
| harness action surface | the structured action schema's via action carries position and mode and nothing else; the Python engine wrapper's whole via surface is diameter, drill, toggle and layer switch |
| microvia fields | the netclass and design-rule structs do expose microvia minima - they are board metadata, and no placement path consumes them |
| pad extent convention | a pad reports its own size, with the cardinal 90/270 degree rotation baked in by swapping the two dimensions and non-cardinal angles left to the caller; a multi-layer pad reports a spans-copper sentinel that expands to every copper layer; the accessor carries no drill |

So the answer to the plan's question - is there a documented harness option other
than the through via - is **not in this engine as built**: placing a blind/buried
or micro via would require new control in the router interface and the harness,
and that is a capability statement read from source, not a legality statement
about any particular via.

That capability question is also not what decides these four edges. The
span-restricted bracket above is proxy-clear nowhere in either window either, so
a blind/buried via of the endpoint span would meet the same measured pour and the
same rule area at these points. What the measurements show is a **local design
constraint** at four specific points on this board; what should change, if
anything, is a decision for the plan and the physical design. This window does
not establish that a design change is required for every possible route plan.

## Independent check

A second script runs in a fresh process with its own engine, session, layer
conversion, raw zone query - no wrapper - and a flat O(items) copper scan with its
own shape distance, sharing no index, bucket or field with the diagnosis. It
re-derives the four edges from the taxonomy and the live board by exact key,
re-measures the column verdict on every layer, and re-measures each anchor's
clear state at the un-inflated track margin.

| comparison | compared | agree | disagree |
|---|---:|---:|---:|
| per-layer column verdict at the shared point | 16 | 16 | 0 |
| anchor clear-state at the un-inflated track margin | 8 | 8 | 0 |

The two implementations also name the same nearest item where an item actually
blocks, and agree on the pour distance to four decimals on every layer of every
edge. Phase 19's own per-edge answers (`gap.json`, `paths.json`) are carried
beside these measurements and reproduce: the two edges phase 19 recorded as
`no_path` have both anchors clear at the un-inflated track margin, and the two it
recorded as `target_blocked` have a target layer that is not clear even there.

## Tests

The classifier that turns a measurement into one blocker word is covered where
the answer is true by construction: 41 assertions, `RESULT pass`. They include
clear, a margin exactly met, keepout winning over clear copper, over blocking
copper and over an unresolved point, copper versus pour decided by which slack
binds, the stable copper-first tie, drill as its own class and losing to a more
binding copper term, unknown when nothing measured blocks, unknown **not** hiding
a measured copper, pour or drill blocker, determinism, and case-by-case agreement
between the two independent implementations' classifiers.

## Verification

| command | exit | result |
|---|---:|---|
| private capability check | 0 | 22/22 checks: content-hash pairing, overlay byte identity, via type/span absence, pad extents, harness action surface |
| private classifier tests | 0 | 41/41 assertions, `RESULT pass` |
| private diagnosis, read-only, both windows | 0 | 4/4 edges; 293,792 point-layer measurements; 0 through and 0 span candidates; 0 unknown |
| private independent checker | 0 | 16/16 layer verdicts and 8/8 anchor verdicts agree; 0 disagreements |
| accepted generation sha256 | - | `6c4f8ab81b83...f593477cf1b`, unchanged; artifact and scratch copies re-hash identically before/after; accepted pointer file unchanged; `checkpoint_count()` 0 -> 0; no commit, stage or push |

No public harness file was changed, so no public test was added or altered and
the strict harness gate was not re-run for this phase. The diagnosis, the probe,
the capability check, the checker and the tests live in the private tree and are
re-runnable as written there, with the per-edge geometry and rule values.

## What this does and does not say

It does say that all four coincident cross-layer edges fail on a measured object
at the shared point - pour fill (three edges) or a rule area that disallows vias
(one) - and not on missing engine information; that the nearest hole is clear;
that no point within a 3.0 mm square at 0.05 mm pitch is proxy-clear for a column
of either tested model (through, or endpoint-span), at either the inflated or the
un-inflated margin; and that the harness can only place through vias, so a
non-through via would need an engine change.

It does not say the board is unroutable, or that a different plan, a different
layer assignment or a physical change could not connect these four nets: the
window is finite and declared, the margin is a clearance proxy rather than native
DRC authority, and native DRC was not run for this phase. The applicable
clearance where a conditional custom rule applies is still not readable through
this engine (phase 15). For these four edges one source of margin divergence can
be ruled out: every netclass on the board carries the same clearance and via
sizes, so no netclass override changes the margin these four nets see. That is
not a complete applicable-rule proof - an object-type condition in the custom
rule file, or a conditional rule the engine cannot resolve, could still change
what native DRC would say, which is exactly why the proxy is not called legal
here. Pour distances come from the fill the loaded board carries rather than from
a fresh refill, and a pad's drill is not exposed.

## Bounded next recommendation

The via-column question asked by the plan is answered within its own bounds: no
column of either tested model is proxy-clear anywhere in the declared finite
+/- 3.0 mm square around these four shared points, at either margin, with the
loaded fill unverified and no native DRC run. On that evidence the next step is a
decision rather than another search - but it is a decision about these four
points on this board, not a finding that a design change is necessary for every
route plan.

1. **Decide the intent for these four connections.** Either the plan geometry
   and layer assignment for them changes (a different endpoint on a reachable
   layer), or the physical design changes (pour relief or a rule-area change near
   the shared points), or these four stay unrouted and are documented as such.
   All three are design decisions with manufacturing consequences; this result
   does not establish that any one of them is required.
2. **Add a non-through via control only if a candidate needs one.** The
   span-restricted bracket found no proxy-clear candidate at these four points,
   so an engine change now would add capability without a use *here*. Revisit it
   if a later phase finds an edge where a partial-span via is proxy-clear and a
   through via is not.

The remaining 45 phase-19 edges - the ones whose two sides are separated inside
the window - are unchanged by this result and still hold phase 19's corridor
question.
