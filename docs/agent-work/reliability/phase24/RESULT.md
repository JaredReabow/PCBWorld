# Phase 24 - the refill measurement

Status: **ready for review.** A zone refill under the pinned rule context is
**supported** on this board, it **did change** the stored fill, the change
**survived save and reopen**, and it **removed the entire clearance class
board-wide**. It also **fragmented 7 of the board's connected clusters** (33
recorded witness pairs, at least 19 rejoins). It replayed none of the 24
historical refusal transactions and proves no cause for them. Nothing was
promoted. **Zero planner calls, accepted pointer and original unchanged.**
Correction cycle: wording only, after Astra's review and T24V's independent
reproduction of the same measurements. No number in this document changed.

Contract: [PLAN.md](PLAN.md). Predecessor: [phase 23](../phase23/RESULT.md).

## The answer

The stored fills, recomputed under the current pinned rules, do not survive as
the same copper. Two whole-board native passes that agreed with phase 23's own
profile to the identity (`7 930` relevant, multiset `2df69ffe72f5e522`) became
`313` relevant and `533` total after one refill: **7 488 fill-clearance
findings resolved**, `147` of `149` hole-clearance findings resolved, and every
other class held except one that the refill itself created.

So the mismatch phase 3 documented is real at board scale: the accepted board's
pours were filled at a finer clearance than its recovered rule file requires,
and refilling under that rule file removes that class - all `7 488` of its
identities on this generation.

That is a statement about the class, not about the 24 plans. **No transaction was
replayed in phase 24.** The causes of the 24 historical refusals are exactly where
phase 23 left them - item-level attribution for 3 of the 24 and a supported
hypothesis for the other 21 - and this phase neither strengthens nor weakens that
attribution. What it adds is that the class those refusals shared is removable
board-wide, and what that removal costs.

The cost is connectivity. Recomputing the pours at the required clearance
**isolates copper fills** - the class goes from a raw `1` to `19`, a net `+18`
made of nineteen new identities and one resolved - and **adds 19 pad groups,
leaves 19 more ratsnest edges, and fragments 7 of the board's connected
clusters** into 33 recorded witness pairs, at least 19 rejoins to restore the
board's own connectivity. Those are separate counts of one refill, not an
item-for-item correspondence. The accepted generation's connectivity depends on
pours filled finer than the rules ask for. Of the refilled board's `313` relevant
identities, `294` persist and `19` are the newly isolated fills; the persistent
set excludes those 19, and this phase does not call any of them genuine
clearance.

## 1. Support and the rule context (A24-1)

There is a supported, pinned refill path and it is not a new one:
`KiCadEngine.fill_zones(rules_path)` over the pinned build's native
`PNS_RL_ROUTER::fillZones`. On a disposable copy of the accepted generation it
**reported success** and changed the board.

| | |
|---|---|
| engine build | `build_rl` (cpp stamp `ef2bd46f`, version 1.4, provenance checked) |
| binding | `KiCadEngine.fill_zones(rules_path)`, one call per phase |
| return value | `true` |
| wall clock | 2.6 s |
| zone-fill epoch | 0 -> 1 |
| source board written? | no - hash identical before and after |

The rule context is the engine's own report, not an assumption. Before the
refill the engine says it read the project from disk, that the routing rules
came from a file, that its DRC rule load error is empty, and it names the rule
file it used; after the save and reopen the reopened board reports the same
context from its own sibling rule file (identical content hash). The refill call
itself passes the same rule file, and the native binding fails closed on a
missing, unreadable or invalid rule file rather than filling under defaults.

## 2. The saved fill changed; nothing else did (A24-2)

Measured on the **bytes on disk**, before and after, with numbers quantised to
nanometres so a re-spelled coordinate is not a geometry change.

| | before | after |
|---|---:|---:|
| zones | 177 | 177 |
| zones whose stored fill polygons changed | - | **95** |
| zones whose stored fill is unchanged | - | 82 |
| stored fill vertices | 88 596 | **140 822** |
| top-level non-zone items | 7 221 | 7 221 |
| non-zone multiset digest | `5d8cc66c43f89ab7` | `5d8cc66c43f89ab7` |
| tracks (segments + arcs) | 6 210 | 6 210 |
| vias | 209 | 209 |
| footprints | 321 | 321 |

Every top-level item that is not a zone - tracks, vias, footprints and their
pads, board graphics, text, nets, the layer stack, the setup block - hashes to
the same multiset before and after. No zone was added, removed, retyped or
moved: zone declarations are identical except that **three** zones gain the
writer's `(fill yes ...)` flag, an imported-board serialisation detail; their
outline, net, layer, clearance and minimum thickness are unchanged.

An independent verifier re-derived both comparisons from the same two files and
found the same thing three ways. Under three separate encodings of the non-zone
multiset - decimal-quantised, quote-aware, and byte-raw tokens - the two boards
are equal, with **0 items added and 0 removed across all 7 221**; and the
declaration comparison finds **exactly three** edits, each one
`(fill ...)` -> `(fill yes ...)` and nothing else, reported as
`all_decl_changes_are_fill_yes_only: true`. The verifier's own artifacts are
`/tmp/p24_verify/indep_cmp.json` and `/tmp/p24_verify/indep_zone_v2.json`.

The save is a real save and the reopen is a real reopen. The board written to
its own candidate path is a different file from the source
(`c8648ff5853f...` against `6c4f8ab81b83...`), a fresh process opening that
candidate reports the same fill digest, the same non-zone digest, the same
vertex count and the same DRC result as the session that produced it, and the
source file is byte-identical after the run. The save's own metadata restore
reported `ok: true` with 58 restored `(net N)` tokens on non-copper items and no
problems - the known writer-drops-the-token behaviour the engine already
restores and verifies, and part of why the non-zone multiset is identical rather
than merely similar.

## 3. What the refill did to the findings (A24-3)

Whole-board native DRC, classified by the repository's own collision-aware
identity policy. Before and after were measured **inside one process and one
engine** around the refill call; a fresh process then re-measured the reopened
candidate. The two comparisons agree on every class count and differ by exactly
one connectivity pairing, so both are reported and labelled rather than merged.

| | in-session (one process) | cross-process (fresh both) |
|---|---:|---:|
| total findings | 8 130 -> **533** | 8 130 -> **533** |
| relevant | 7 930 -> **313** | 7 930 -> **313** |
| connectivity | 200 -> **220** | 200 -> **220** |
| distinct identities resolved | 7 665 | **7 666** |
| added relevant identities | **19** | **19** |
| added connectivity identities | 49 | 50 |

The single identity of difference in each column is the same connectivity
pairing the `unconnected_items` reporter chose differently between processes
(section 6), not copper. Everything below is the cross-process pair; the
in-session pair is identical except that its `Missing connection between items`
row is `29` resolved and `49` added.

| class | before | after | resolved | added |
|---|---:|---:|---:|---:|
| Clearance violation | 7 488 | 0 | 7 488 | 0 |
| Hole clearance violation | 149 | 2 | 147 | 0 |
| Isolated copper fill | 1 | 19 | 1 | 19 |
| Hole size out of range | 230 | 230 | 0 | 0 |
| Drilled hole too close to other hole | 32 | 32 | 0 | 0 |
| Items shorting two nets | 30 | 30 | 0 | 0 |
| Missing connection between items | 135 | 154 | 30 | 50 |
| Track has unconnected end | 65 | 66 | - | - |

**Resolved:** the entire fill-clearance class, and all but two of the
hole-clearance class. **Persistent:** the 294 relevant identities that are
neither resolved nor new - the hole-size, hole-to-hole and shorting classes in
full (230 + 32 + 30) plus the two remaining hole-clearance findings. **New:**
the isolated fills, 19 of them, which are *added* rather than persistent. The
candidate's 313 relevant identities split exactly as 294 persistent + 19 new.

The isolated fill count is the one place where a raw class count and an identity
count disagree: the class goes from `1` to `19`, which is a net `+18` - nineteen
new identities and one that is resolved.

The pour prediction in phase 23 was that a refill would recompute the fills
against the rule file. It did, and the class the refusals shared is gone
board-wide on this generation (again: no transaction of the 24 was replayed).
What phase 23 could not predict from the outside is the second half: the same
recomputation disconnects copper.

## 4. Connectivity: 7 clusters fragmented

Native terminal partition, not counts: the same comparison the promotion ladder
runs.

| | accepted | refilled |
|---|---:|---:|
| nets | 191 | 191 |
| terminals | 1 060 | 1 060 |
| clusters | 321 | **340** |
| vanished terminals / nets | - | 0 / 0 |
| split-relation witness pairs | - | **33** |
| reference clusters fragmented | - | **7** (six into 2, one into 14) |
| minimum rejoins to restore the reference partition | - | **19** |

The 33 relations are the comparator's *witness pairs*: for each reference cluster
it anchors on one surviving terminal and records one pair per terminal that ends
up in a different candidate cluster. They are representative, not an exhaustive
count of lost pairs. The structure underneath them is **7 fragmented reference
clusters - six split in two and one split fourteen ways** - and restoring the
reference partition takes at least `19` rejoins, the sum of `k - 1` over those
clusters. The same 7 fragments and 33 witnesses appear in the in-process
comparison and in the fresh-process comparison. No merged-cluster count is
claimed: the result object's `merged_pairs` field carries a dataclass default and
this comparison never computes it.

The refill is the only variable behind all of them, but they are separate counts:
the pad-group count rises by 19, the ratsnest by 19, the terminal partition needs
at least 19 rejoins, and the isolated-fill class goes from a raw `1` to `19` (a
net `+18`: nineteen new identities, one resolved). This phase does not assert an
item-for-item correspondence between the new isolated fills and the 19 rejoins.
What it measured is the event: pours drawn at the finer clearance were bridging
pads, and pours drawn at the rule clearance do not.

## 5. The complete pinned CLI, against the canonical original

The vetted build-tree reporter (9.0.8, caps removed by the tree's own patch),
two runs per board, both boards staged with the **same** project and rule file
and every input hashed before staging.

| | |
|---|---|
| reference board | canonical original `a6232800646a...` (the reference the accepted generation's own promotion gates were staged against) |
| candidate board | refilled `c8648ff5853f...` |
| verdict | **unverified, not accepted** |
| report complete | true (no class is at a known cap) |
| CLI / provider hashes | `56dd7910af7d...` / `79ac6f6fcf39...` |

| class | rows: canonical original -> refilled | added identities | removed identities |
|---|---:|---:|---:|
| clearance | 7 488 -> 0 | 0 | **7 488** |
| hole_clearance | 149 -> 2 | 0 | 147 |
| solder_mask_bridge | 113 -> 23 | 0 | 90 |
| starved_thermal | 136 -> 73 | **37** | 100 |
| isolated_copper | 1 -> 19 | **4** | 1 |
| track_dangling | 70 -> 66 | **1** | 5 |
| items_not_allowed | 93 -> 90 | 0 | 3 |
| copper_sliver | 9 -> 0 | 0 | 1 |
| shorting_items | 30 -> 30 | 0 | 0 |
| unconnected items | 149 -> 154 | churned | churned |
| every other class | equal | 0 | 0 |

Rows and identities are not the same count and the table keeps both. The nine
`copper_sliver` rows the source reports carry a single identity, so one is
removed rather than nine; and the refilled board's `isolated_copper` section is
nineteen reported rows across four distinct identities, with the gate dropping
thirty byte-identical repeat rows in that class. The identity columns are what
the gate judges.

The verdict is `unverified` for two independent reasons and both are reported
rather than smoothed: the CLI's `unconnected_items` section churned which
endpoint it names between runs of one unchanged board (so that class is judged
over the union of runs and cannot be judged at all without a native terminal
proof bound to this candidate), and several classes really did move. A
supplementary run with the accepted generation as the reference isolates what
the refill itself contributes: the same candidate counts, `7 488` clearance
identities removed, `90` mask bridges removed, `37` starved thermals added,
`4` isolated copper added, `1` dangling track added, `0` shorting items moved.

Two things this run adds to the record:

* **The CLI reporter sees effects the native gate does not.** The native
  gate's added-relevant set is the 19 isolated fills; the complete CLI also
  reports `starved_thermal` and `track_dangling` identities that the project's
  native severity/provider configuration does not surface. A native "zero
  added" is therefore not a CLI pass, which is why the ladder requires both.
* **A save rewrites the project sidecar, and the rules block is not unchanged.**
  The gate refuses a candidate whose `.kicad_pro` is not byte-identical to its
  reference's. The writer turned the imported v1 project into its own v3 shape:
  schema version, colour keys, netclass ordering and list-valued netclass
  assignments all change, the per-netclass `via_annular_width` token - which is
  not a KiCad netclass field - is dropped, and the candidate's `rules` block
  **gains 11 keys** that the imported file did not carry (the writer's own
  defaults: `max_error`, `min_connection`, `min_groove_width`,
  `min_microvia_diameter`, `min_microvia_drill`, `min_resolved_spokes`,
  `min_silk_clearance`, `min_text_height`, `min_text_thickness`,
  `solder_mask_to_copper_clearance`, `use_height_for_length_calcs`). Every value
  the two files share is equal, including all eight netclass clearances and the
  board minimums, but "the whole rules block is identical" would be wrong: it is
  a superset. Both sides of every comparison in this phase were therefore staged
  with the **reference's** project and rule bytes, and a future candidate needs
  its sidecars normalised at staging rather than accepted as written.

## 6. The known cross-process variation, handled

Phase 23 found `Items shorting two nets` varying by one location between
processes on this board. It is not in play here, and that is a measurement
rather than an assumption:

* the shorting class is `30` in all six whole-board passes run this phase - two
  per process for the accepted copy, the refilled candidate and the
  before/after pair inside the refill session;
* it appears in neither the resolved nor the added set of either delta, and the
  CLI's own `shorting_items` class is `30` on both boards with zero moved
  identities;
* the only difference between the within-process delta and the cross-process
  delta is **one connectivity pairing** (7 665/49 inside the process against
  7 666/50 across processes) - the churn phase 23 already characterised as
  `unconnected_items` reporter choice, not copper.

So no part of the refill's result is attributed to that variation, and no part
of it is excused by it either.

## 7. Guards

| | |
|---|---|
| accepted pointer | `57b8db6121ee...` - unchanged |
| accepted generation board | `6c4f8ab81b83...` - unchanged |
| canonical original board | `a6232800646a...` - unchanged |
| source copy during the run | byte-identical before and after |
| candidate | own path, own name, never written over the source |
| planner calls | 0 |
| staging / commit / push | none |

## 8. Verification

| command | exit | result |
|---|---:|---|
| `bash tools/reliability/check_phase.sh --strict` | 0 | 496 unit + 138 native, no skips |
| `python3 tools/check_separation.py` | 0 | 4/4 checks; wire copies identical |
| `python3 tools/reliability/check_engine_patches.py` | 0 | 3 patches, 6 files byte-equal to the pin |
| `git diff --check` | 0 | clean |
| private accepted-copy measurement | 0 | 2 passes, identical relevant multiset `2df69ffe72f5e522`, 8 130 / 7 930 |
| private refill session | 0 | 1 refill, `true`, 2.6 s; fill changed in 95 of 177 zones; non-zone multiset unchanged; saved to its own path |
| private reopened-candidate measurement | 0 | fresh process, 2 passes, identical relevant multiset `ca2220dc9235845f`, 533 / 313 |
| private classification | 0 | cross-process 7 666 resolved / 19 added relevant / 50 added connectivity, in-session 7 665 / 19 / 49; 33 split-relation witnesses over 7 fragmented clusters, minimum 19 rejoins |
| private complete-CLI gate, canonical original -> refilled | 0 | 2 runs per board, report complete, verdict `unverified` |
| private complete-CLI control, accepted -> refilled | 0 | 2 runs per board, report complete, verdict `unverified` |
| private guards | 0 | pointer, accepted board, canonical original unchanged |
| independent verification (T24V) | 0 | non-zone multiset equal under three encodings, 0 added / 0 removed over 7 221 items; exactly 3 declaration edits, all `(fill ...)` -> `(fill yes ...)` |

## 9. Limits and risks

* **One refill, one copy, one board.** This is a measurement of what a refill
  under the current rules does to this generation; it is not a promotion, and
  nothing here says a refilled board is acceptable.
* **The refill is not a fix and it is not free.** It removes the refusing class
  board-wide and fragments 7 of the board's connected clusters doing it. The
  choice is between pours that violate the rule file and pours that do not
  connect the board - a board or process decision, not a gate decision.
* **Persistent findings are candidates, not conclusions.** `294` relevant
  identities survive a proved refill and are neither resolved nor new, including
  two hole-clearance findings and the full hole-size, hole-to-hole and shorting
  classes; the `19` newly isolated fills are new, not persistent. Whether a
  persistent finding is a genuine clearance needs its own measurement.
* **This phase is not a cause proof for the historical refusals.** Nothing in
  the 24 closure-then-refusal plans was replayed. The board-wide removal of the
  class they share is consistent with phase 23's attribution (3 of 24 proved,
  21 supported) and does not extend it.
* **The CLI verdict is `unverified`, not a pass.** The `unconnected` pairing is
  not reproducible between runs without a bound native terminal proof, and
  several classes moved. It was run because the contract asks for it; it does
  not accept this candidate.
* **The fragmentation is measured, not attributed.** The refill is the only
  variable - same board bytes elsewhere, same engine, same rules, one call - but
  this phase does not identify which pours were load-bearing per net, and the 33
  witnesses are representatives of the 7 fragments rather than a pair inventory.
* **The project sidecar is rewritten by the writer.** Values were checked key by
  key: every shared value is equal and the candidate's rules block is a superset
  by 11 keys. Nothing in this phase depends on the rewritten file, because both
  sides of every comparison used the reference's project bytes.
* **Per-item geometry, net names, coordinates and rule values stay private.**
  The aggregate counts above are the public statement; the private
  `phase24_refill/` evidence carries the rest.

## 10. Next bounded action

**Nothing here is promotable as it stands.** The refilled board's connectivity is
worse than the accepted generation's, so no promotion can be based on it before
the original connectivity is restored and every existing gate passes again. The
promotion ladder does not change for this phase: a candidate needs the accepted
pointer's connectivity preserved (the native terminal partition shows no
fragmented reference cluster), the whole-board native DRC gate under the pinned
rules, the terminal-partition gate, and the complete pinned CLI gate against the
canonical original - all of them, on the candidate's own generation, before the
pointer may move.

That leaves one bounded measurement before any design decision, and it is not a
rule change:

* **Restore connectivity and re-gate, or do not refill at all.** If the fills
  are to be recomputed under the current rules, the work is to restore the 7
  fragmented reference clusters (at least 19 rejoins) without reintroducing the
  findings the refill removed, and then to run the three production gates. The
  7 fragments by net and the 19 newly isolated fills by area are the private,
  already-measured input to that work.
* **The alternative is not ours to take in this phase.** Making the router
  pour-aware - so the pours stay as drawn and the router stops issuing copper
  into them - is an engine capability decision with its own phase and its own
  contract.
