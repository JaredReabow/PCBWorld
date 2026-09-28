# Phase 18 - result

Status: **ready for review.** A read-only endpoint and anchor census of all 49
`connection_not_verified` edges on the unchanged accepted generation. Anchor
identity is answered for every offered anchor; anchor availability is answered
for all but one; and the readable defect is geometry at the board's own
clearance margin, not a missing endpoint. **Nothing was routed, no board file
was written, no checkpoint was taken, and the accepted pointer and hash are
unchanged.**

Contract: [PLAN.md](PLAN.md). Predecessor: [phase17/RESULT.md](../phase17/RESULT.md).

## What was measured, and what was deliberately not

The census joins the phase-11 taxonomy to the current accepted board by **exact
offered-edge identity** - the canonical key (net plus both anchors,
order-independent) that `failure_taxonomy` produces - never by net name. For
every joined edge it asks the engine, read-only: what the point-identity rule
says at each offered anchor; whether a route start is provable there and whether
the anchor sits in a rule-area keepout; whether the net's own components prove
substitute anchors, and which of those are already joined to the peer; where
foreign copper sits relative to the declared straight leg and to lanes beside
it, on every layer the route must be legal on; and, for cross-layer edges,
whether any spot to drop the through via is free of foreign copper at the
sampled margin the census actually measures (the proxy below - **not** a
native-DRC-cleared verdict).

Nothing in the classification was taken from a record. The phase-17 seven-edge
trial and the pre-field attempt history were **not** read for it: same-generation
history is not same-harness history, and a stored step is not a cause.

The full private evidence - net names, coordinates, geometry, rule values, and
the per-edge rows - stays in the private tree
(`phase18_census/anchor_census.json`, `clear_area.json`, `validation.json`,
`NOTES.md`). This report carries aggregate counts only.

## The join

| | |
|---|---:|
| offered edges | 49 |
| joined by exact key | 49 |
| missing from the current ratsnest | 0 |
| ambiguous keys | 0 |
| net codes agreeing with the taxonomy | 49 |
| ratsnest edges on the board | 135 |

The accepted generation was hash-checked before and after every run and is
byte-identical (`6c4f8ab81b83...f593477cf1b`); the board geometry digest was
identical before and after; `checkpoint_count()` went 0 -> 0.

## Anchors: identity answered, availability answered but for one

| measure | count |
|---|---:|
| offered anchors (49 edges x 2) | 98 |
| identity proved by the point-identity rule | **98** |
| anchor carries no copper at all | 0 |
| copper present but no pad or via names its net | 0 |
| two nets in one cluster (ambiguous) | 0 |
| route start provable and outside a keepout | 97 |

This is the phase's first separation. These 49 are **not** the
`copper_absent_at_offered_anchor` (26) or `unnamed_net_at_offered_anchor` (6)
class: the engine can name the scheduled net at every offered anchor, on both
ends, as a fresh measurement rather than an inherited category. The single
exception is availability, not identity: one anchor of one coincident
cross-layer edge sits inside a rule-area keepout on its own layer, so that
endpoint cannot be started from even though its net is named correctly.

## Proved substitutes: every edge has somewhere to move to

| | |
|---|---:|
| edges with at least one proved same-net substitute anchor | 49 |
| substitute anchors proved | 402 |
| of those, not already joined to the peer (would close something) | 304 |
| of those, already joined to the peer (would close nothing) | 98 |
| substitutes per edge, min / median / max | 4 / 6 / 22 |

No edge is short of same-net copper. The caveat is identity rather than
quantity, and it is the taxonomy's: a substitute anchor can stand for a
different component pair than the ratsnest edge drew, so a substitution is a
candidate connection, never a resolution of the offered one.

## Geometry: the declared corpus is not margin-clear under the measured proxy

Two words are kept strictly apart in this section. **Margin-clear** means "no
foreign copper within the board's minimum clearance plus the adopted copper
radius, at the sampled points and layers the census queried" - a geometric
lower bound. **Rule-clear** is reserved for a verdict the native DRC actually
returned, and this phase produces none: it runs no DRC, and the applicable
clearance where conditional custom rules apply, plus hole clearance, are still
not readable through this engine (see phase 15).

The margin is the board's own minimum copper clearance plus the adopted copper
radius (the private value and its basis are in the private evidence). Foreign
copper means any other net **and** netless copper, on the layer being tested.

| measure | edges |
|---|---:|
| foreign copper within the margin at the start anchor itself | 33 |
| first contact within the first millimetre of the leg | 16 |
| no contact anywhere on the leg | 0 |
| pour contact somewhere on the leg | 37 |
| rule-area keepout contact on the leg | 1 |
| a point the engine could not resolve (explicit unknown) | 1 |
| obstructed at both anchor ends | 20 |
| **no margin-clear lane at any offset within 3 mm, full leg** | **44** |
| a margin-clear lane found at some offset within 3 mm | 1 |
| coincident anchors (no direction to offset along) | 4 |
| **no margin-clear interior lane** (excluding 1 mm at each end, 34 measurable edges) | **34 of 34** |

The longest obstructed run measured along any one leg is tens of millimetres;
the exact value stays in the private evidence.

**Cross-layer edges: 0 of 8** have any via spot that is margin-clear under the
measured proxy on the direct leg, on every copper layer a through via spans.
Four of the eight are coincident (the two anchors share a point on different
layers), where the via must go essentially at the offered anchor - and for one
of those the anchor is the keepout case above.

A supporting read-only diagnostic then asked the complementary question: is
there room anywhere near the leg at all? Sampling a 1 mm x +/-4 mm grid around
every leg on every required layer and recording the largest **open radius**
(distance to the nearest foreign pour or item, capped at the search window):

| largest open radius near the leg | edges |
|---|---:|
| below the board margin (no room in the window) | 2 |
| margin .. 0.5 mm | 1 |
| 0.5 .. 1.0 mm | 27 |
| 1.0 .. 2.0 mm | 18 |
| over 2.0 mm | 1 |

**47 of 49 edges have at least one point within 4 mm of the leg with
margin-clear room**, and the largest open radius found is just under the 4 mm
window. **That is free space, not connectivity.** A margin-clear point is a
single point: it does not establish a continuous path between the two anchors,
it does not show that copper laid near it would survive the native DRC, and it
is not a statement that the board is unsaturated. What it does establish is
narrower and still useful - the declared geometry (a straight leg, and the lanes
beside it that a direct or walk-around plan can use) has no margin-clear path
under the measured proxy for 44 edges, while margin-clear points exist nearby
for almost all of them. The binding constraint is therefore the plan geometry
this census can see, and whether a real route exists in that free space is
exactly what the next step below would have to measure.

## Verdicts

| verdict | edges | confidence |
|---|---:|---|
| `geometric_obstruction` | 48 | `no_clear_lane_within_3mm` 47, `direct_leg_only` 1 |
| `both` (anchor defect **and** geometry) | 1 | measured |
| `anchor_identity` or `anchor_availability` alone | 0 | - |
| `unresolved` | 0 | - |

The single `both` edge is the coincident cross-layer pair whose target anchor is
inside a keepout: it also has no via spot on the leg. The single
`direct_leg_only` edge is the one with a margin-clear lane inside 3 mm; only its
centre line is obstructed, so its verdict carries the weaker confidence.

## One bounded next intervention

The evidence supports one, and it is about **plan geometry**, not about anchors
and not about another routing campaign:

> **Phase 19 candidate.** Extend the read-only search from the declared 1D leg
> and its narrow band to a *bounded 2D clearance-aware* search over a fixed
> window per edge, and report per edge whether any path connects the two proved
> anchors inside that window without foreign copper inside the measured margin.
> A path found that way is a *candidate*, not a legal one: only the native DRC
> can call copper rule-clear, so the second half is at most one bounded
> transactional trial per edge where such a candidate path exists.

Why this, from the measurements above: anchor identity is proved 98/98 and a
proved substitute exists for 49/49, so the failure is not "no endpoint"; under
the measured proxy 44 of 49 edges have no margin-clear lane in the declared band
and 8 of 8 cross-layer edges have no margin-clear via spot, so the geometry a
straight-line or narrow-band plan declares has no margin-clear path to follow;
yet 47 of 49 edges have margin-clear points within 4 mm, so a 2D search has
somewhere to look. None of that is a legality statement. Every part of the
phase-19 proposal is read-only until a candidate path is measured, and its
success criterion is a count, not a campaign.

Two edges have no margin-clear point anywhere in the sampled window. If the 2D
search finds no candidate path for an edge, that edge needs a physical design
decision (more routing room, a different layer assignment, or a placement
change) rather than another planner change, and the census's own numbers say
which edges those are.

## Verification

| command | exit | result |
|---|---:|---|
| private census, read-only | 0 | 49/49 joined by exact key; accepted artifact and board digest identical before/after |
| private validator, fresh engine and raw O(items) scan | 0 | join 49/49, identity 98/98, first contact 49/49, 0 failures |
| private clear-area diagnostic | 0 | 47/49 edges have at least one margin-clear point within a 4 mm window |
| census re-run, same arguments | 0 | aggregate identical |
| pitch 0.25 / 0.5 / 1.0 mm | 0 | verdicts and the 3 mm lane counts identical; fine-grained first-contact counts move (33 -> 35 at the start anchor at the finer pitch) |
| margin 0 mm (containment only, diagnostic) | 0 | all contact disappears (48 `unresolved`) - the finding is margin-driven, and this is the honest bound on it |
| accepted generation sha256 | - | `6c4f8ab81b83...f593477cf1b`, unchanged; no commit, stage or push |
| review correction (wording only) | 0 | `git diff --check` clean; proxy wording and the free-space caveat corrected in this file, `CHANGELOG.md` and `HISTORY.md`; no analysis artifact, test, engine or wire file changed, so no behaviour re-run was needed |

No public harness file was changed, so no public test was added or altered for
this phase. The read-only census, its validator and its diagnostic live in the
private tree and are re-runnable as written there.

## Limits and risks

* **A sampled band is not a proof of unreachability.** "No margin-clear lane
  within 3 mm" is a measurement of that band, not a statement about the board;
  the router may walk around it, which is exactly what the phase-19 proposal
  would test.
* **The finding is clearance-margin driven.** With the margin removed the
  contact disappears entirely, so the census measures "no margin-clear lane
  under the measured proxy", not "copper physically blocks the line". The
  applicable clearance where conditional custom rules apply is not readable
  through this engine (see phase 15), so a rule-complete statement is not
  available - that phrase is deliberately used here only to say it is *not*
  available - and the margin used is the board minimum plus the adopted copper
  radius.
* **Netless copper is counted.** A pad with no net is still copper that a
  margin-clear lane may not overlap, and including it changed one edge's answer
  during development - it is reported as a separate flag in the private rows.
* **Free points are not free routes.** The 4 mm diagnostic reports isolated
  points with an open radius; it says nothing about connectivity between them,
  about a continuous path, or about a DRC verdict on any copper placed there.
* **Hole-to-hole clearance is not measured**; this engine does not expose pad
  drill diameters, so only via barrels have a readable drill size.
* **Fine-grained first-contact counts are sampling-sensitive.** The headline
  verdicts and the 3 mm lane counts were stable across three sampling pitches;
  the per-edge "contact at the anchor" counts were not, and are quoted as
  measurements of one sampling.
* **No cause was attributed from history.** Nothing here says where a past plan
  stopped; it says what the board offers now.
