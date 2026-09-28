# Phase 9 — aggregate failure taxonomy (V3, accepted generation)

Aggregate only. Net names, coordinates, refusal positions and layout files stay
in the private workspace; this document carries counts, classes and hashes.

Reproduce the aggregate with:

```
python3 tools/reliability/failure_taxonomy.py \
    --board <board.kicad_pcb> --history <run_state.json> [...] \
    --out private/taxonomy.json --public-summary public_summary.json
```

## Binding

| | |
|---|---|
| board sha256 | `6c4f8ab81b83f2cb18044028c2a8252da595a57f85317a31c50e1f593477cf1b` |
| project sha256 | `08dcb693cf8801d5a7e461785c202ab5ff56fd2bcc8152a1aa09d5ad158e0ea7` |
| rules sha256 | `ce92dd7b83822c4fdee4128afb284d77d9aeeb94d69fd869706d97bef5b71e25` |
| geometry digest | `7af2e79c66dd27e8` |
| taxonomy version | 1 |

The board was not modified: it is copied into a private scratch directory, the
taxonomy runs over the copy, and the accepted generation is hash-checked before
and after.

## Two different counts

| | |
|---|---|
| outstanding ratsnest **edges** | **135** |
| distinct disconnected **component pairs** | **639** |
| nets carrying at least one pad group | 191 |
| pad groups in total | 321 |
| nets with two or more proved components | 53 |

The 135 is one drawing per outstanding connection. The 639 is the sum of
`C(components, 2)` over the nets that have more than one proved component, which
is the amount of work an exhaustive router actually faces on this board. Reporting
either number as "the number of connections" would be wrong in a different way.

## Categories — they sum to 135

Two views of the *same* board, differing only in which attempt histories are
joined. "As found" is the state the strategy choice was made from; "after the
campaign" adds this phase's own segments. Both bind to the same board, project and
rules hashes and the same geometry digest.

| Category | As found | After the campaign |
|---|---:|---:|
| `drc_regression` | 35 | 43 |
| `connection_not_verified` | 42 | 49 |
| `copper_absent_at_offered_anchor` | 26 | 26 |
| `unnamed_net_at_offered_anchor` | 6 | 6 |
| `unattempted_cap_observed` | 26 | 11 |
| *all other categories* | 0 | 0 |
| **total** | **135** | **135** |

| | As found | After the campaign |
|---|---:|---:|
| attempted | 79 | 94 |
| not attempted | 56 | 41 |

The campaign's effect shows up in the only place it can: `unattempted_cap_observed`
falls by 15 and the attempted categories rise by exactly 15. No board fact
changed, because nothing on the board changed.

`already_connected_stale_edge`, `unroutable_degenerate_same_point`,
`layer_unresolvable`, `insufficient_component_anchors`, `via_no_continuation`,
`plans_exhausted` and `unattempted_no_record` are all zero on this generation.
They stay in the taxonomy because a zero is a result: the engine draws no stale
edge, no anchor layer is unresolvable, and every pair that was attempted was
attempted with a plan that had something to say.

**56 of 135 connections (41 %) had never been attempted on this board when the
taxonomy was taken.** The largest single cause was not routing capability: 26 of
them draw an anchor that lands on no copper whatsoever, and the other 26 were
pairs the bounded scan never offered.

**`via_no_continuation` is zero, and that is a result, not a gap.** The driver
searches via continuations per pair and records it. On this generation the two
things a `via_no_continuation` claim needs — a via-hop plan on the pair itself,
and a search that probed spots without proving one — never coincided. Four of the
`connection_not_verified` edges carried via-hop plans; their runs' searches are
recorded, and no pair had both.

## What the refusals are

All `drc_regression` edges have `closed_before_refusal = true`: a plan closed the
connection and the native gate refused the copper it closed it with. These are the
connections the router can already join; the copper is what is not legal yet.

The counts are a sum over every refused plan of every such pair, not a count of
problems on the board.

| refusal class | as found | after the campaign |
|---|---:|---:|
| Clearance violation | 4 273 | 11 052 |
| Hole clearance violation | 2 075 | 4 390 |
| Drilled hole too close to other hole | 42 | 83 |

## Facts carried alongside the category

| anchor state | edges |
|---|---:|
| both anchors carry provable same-net copper | 103 |
| an anchor carries copper the point rule cannot name | 6 |
| an anchor carries no copper at all | 26 |

| layer relation | edges |
|---|---:|
| same layer | 102 |
| cross layer | 33 |

| gap class | edges |
|---|---:|
| coincident (cross-layer bridge) | 9 |
| under 1 mm | 29 |
| 1–5 mm | 41 |
| 5–20 mm | 31 |
| over 20 mm | 25 |

| foreign copper within 2 mm of the connection's midpoint | edges |
|---|---:|
| yes | 131 |
| no | 4 |

## History join

| | As found | After |
|---|---:|---:|
| attempt records offered | 11 551 | 22 859 |
| records bound to this board and attributed to an edge | 6 409 | 16 093 |
| records measured on other copper (dropped) | 4 075 | 4 075 |
| records that matched no outstanding edge | 1 067 | 2 691 |
| substitution records | 1 145 | 1 804 |

The dropped 4 075 are the run that *produced* this generation: its records were
measured on the previous board and are not evidence about this one.

## Limits of this taxonomy

* `unattempted_cap_observed` (named `capped_unattempted` before phase 10) uses the
  **run-level** scan counters (`skipped_net_cap`, pairs omitted by the per-net cap
  or by the component window). The run state does not persist the per-net
  breakdown, so this separates "an unattempted edge in a run that reported its
  bounded scan withheld pairs" from "no bound was reported" — it does **not**
  prove that one particular edge was the one withheld. The category was renamed
  in phase 10 so the label carries the weaker, checkable claim; the counts are
  unchanged (26 as found, 11 after the campaign, still summing to 135).
* A substitution replaces the offered edge's geometry before it is attempted, and
  the run state does not record which edge the substitute stood for. Substitution
  records are therefore counted in aggregate, not attributed to the edge they were
  offered for. The runner records that link when it re-anchors *mid-attempt*
  (`reanchored_pairs`), and that path produced no entries on this board.
* `unnamed_net_at_offered_anchor` is a statement about the point-identity rule,
  not about the copper: the cluster exists, the net simply cannot be named from
  the point alone.
