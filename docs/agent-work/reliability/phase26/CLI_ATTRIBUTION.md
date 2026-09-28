# Phase 26 - what the pinned CLI findings are, and where they came from

T26B. Private evidence: `phase26_cli_attribution/` in the board workspace
(`NOTES.md` there carries nets, coordinates and rule values). This page carries
aggregates only.

Three frozen boards were re-measured with the complete pinned reporter, four
fresh processes each, every board staged with the canonical original's own
project and rule bytes so the three reports are comparable:

| board | sha256 | role |
|---|---|---|
| accepted generation | `6c4f8ab81b83f2cb` | the accepted board, unchanged |
| phase-24 refilled baseline | `c8648ff5853fc890` | the refill experiment |
| phase-25 post-refill survivor | `6c8e6e3943685b20` | the reconnection, re-filled |

Reporter: the build-tree `kicad-cli` 9.0.8 (`56dd7910af7d190c`, provider
`79ac6f6fcf398fb3`), the vetted option set, no class at a known report cap
(199/499) in any of the 20 reports, and no report rejected by the gate's own
validator. The canonical original (`a6232800646a7dcd`) and a fifth board (the
pre-refill phase-25 candidate) were measured beside them as context. The
installed 10.0.6 application was not used.

## The attribution

Comparing finding identities - class, severity, and the collision-safe
per-item key the gate uses - across the three boards:

| class | accepted | refilled | survivor | where it came from |
|---|---:|---:|---:|---|
| clearance | 7,488 | 0 | 0 | predates the refill; the refill resolves it |
| hole clearance | 149 | 2 | 2 | predates; 147 resolved by the refill |
| solder-mask bridge | 113 | 23 | 23 | predates; 90 resolved by the refill |
| starved thermal | 136 | 73 | 72 | 136 predate; the refill adds 37 new identities and drops 100; the phase-25 step drops one more |
| isolated copper | 1 | 4 | 4 | 1 predates and the refill resolves it; 4 identities are refill-introduced |
| track dangling | 65 | 66 | 66 | 65 predate; the refill introduces 1 |
| disallowed items | 93 | 90 | 90 | predates; 3 resolved by the refill |
| unconnected (raw rows) | 135 | 154 | 153 | the refill adds 19 rows, the phase-25 step takes one back |

Two things this isolates that the promotion gate cannot say on its own, because
it only ever compares a candidate with the canonical original:

1. **The visible DRC debt predates the refill.** 18 of the 20 classes are
   identity-for-identity identical between the accepted generation and the
   canonical original, including the 7,488 clearance findings, the 149 hole
   clearances, the 136 starved thermals, the 1 isolated copper, the 113
   solder-mask bridges and the 93 disallowed items. The accepted generation
   differs from the canonical original in exactly two classes: the churning
   unconnected pairing, and dangling tracks (65 against 70).
2. **Everything the CLI refuses the refilled boards on was introduced by the
   refill.** The four isolated-copper identities, the 37 new starved thermals
   and the one new dangling track are all absent from every accepted-generation
   run and present on the refilled baseline. The phase-25 reconnection step
   introduced no new identity in any class that is judged by identity; it
   resolved one starved thermal. Its only other movement is in the
   `unconnected_items` pairing, which is judged on raw rows instead.

## The unstable class

`unconnected_items` is the one class whose identities are a visualisation
choice, and that is measured here rather than assumed: the board's raw row count
is stable per board (135 / 154 / 153 / 149), while the endpoint pairing churns -
22 of 128 distinct identities are unstable on the accepted generation, 21 of
156 on the refilled baseline, 16 of 153 on the survivor. Cross-checking this
phase's four fresh runs against the raw reports phase 24 and phase 25 left on
disk, **every class reproduces identity-for-identity except `unconnected_items`**
- so the churn is the reporter's pairing, not a difference between two
measurement campaigns. This is why that class is judged on raw counts plus a
fresh native terminal-partition proof, and every other class on identities.

## The three classes the gate refuses on

The CLI reports the refilled boards with `isolated_copper` 4,
`starved_thermal` 37 refill-introduced identities, and `track_dangling` 1
refill-introduced identity. Joining each identity to its board geometry
(private) shows:

* **Isolated copper (4).** Four zones hold the new identities: three that had no
  fill polygon at all on the accepted board and gain 1-2, and one that goes from
  1 island to 15. Measured policy: no zone in any of the boards declares an
  island-removal mode, and in the pinned source that absence means **ALWAYS** -
  the writer emits the token only when the mode is not ALWAYS, and the parser
  defaults to ALWAYS. The refill path uses the stock zone filler, which
  deliberately removes none of a zone's islands when every polygon on every
  layer is an island. All four flagged zones measure as possibly all-islands, so
  re-filling them under the same policy is expected to change nothing.
* **Starved thermals (37).** All sit on six zones at the same thermal gap and
  bridge width, 35 on the board's ground pours and 2 on a supply pour, and none
  of the pads carries a zone-connection override. The reporter's "min spoke
  count 2" comes from the project's `rules.min_resolved_spokes`, not the board
  or the rule file: the pinned DRC engine seeds the spoke constraint from that
  design setting, whose default is 2, and none of these projects declares a
  different value.
* **Dangling track (1).** One 50.5 um stub on an inner layer. On the accepted
  board its landing point sits inside a same-net pour island; after the refill
  it sits inside none. The stub itself is unchanged - the copper under it moved.

None of these is an established repair, and no setting change alone reverses
them. What the evidence supports:

* **Compliant reconnection.** Connect each isolated polygon into its net's pour
  with legal copper (a short track or via), re-route the net so the pour stays
  one polygon, and route the stub's net to its copper. These add copper the
  design needs.
* **Verified removal.** Deleting the sub-0.1 mm stub is acceptable only once a
  native terminal-partition proof shows it is not load-bearing.
* **Experimental design changes**, to be measured rather than assumed: deleting
  the isolated polygons after the fill; changing the flagged zones' clearance or
  priority; a per-pad solid zone connection or pour thermal geometry for the
  starved pads. These change the delivered copper or its soldering behaviour even
  though the rule bytes are untouched, so they are experiments with a
  manufacturing tradeoff - not repairs.

Whatever is tried, the gate is the same: pinned rule bytes unchanged; a fresh
fill, save and reopen in a new process; a complete pinned CLI comparison by
collision-safe **identity**, two runs per board, not by count; raw unconnected
rows not increased with a fresh native terminal-partition proof; the accepted
generation's terminal partition preserved; zero added identity in every relevant
class; and every gate fail-closed. A no-op cannot be called a fix because a
count happened to match.

## Limits

This is an identity attribution, not an acceptance. The CLI verdict on the
refilled boards remains **unverified**; the accepted board and the accepted
pointer were not modified; nothing was staged, committed or pushed; no paid API
was called. The accepted generation is still 135 unrouted edges from complete,
and the refill's own 19 extra unrouted edges are unchanged debt. The
island-support screen is endpoint-only and is reported as an approximation, so
it bounds the geometry rather than proving an island unreachable, and the
"possibly all-islands" reading of the four zones is that same approximation.
The four isolated-copper identities are distinct identities after byte-identical
repeat rows are collapsed, not the 19 raw report rows behind them.
