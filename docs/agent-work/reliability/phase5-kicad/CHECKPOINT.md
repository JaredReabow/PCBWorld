# CHECKPOINT — phase 5 recovery

## Current state — 2026-09-27, via-state corrections + continuation trial (accepted board unchanged)

Status: **the accepted board did not move this cycle.** `6c4f8ab81b83…` (135
unrouted / 321 pad groups / 6 210 tracks) is still the continuation baseline; its
three production gates were re-run for this report and pass (native 0 added
relevant, terminal 335 -> 321 clusters with 0 splits, complete CLI 9.0.8 verified,
raw unconnected 149 -> 135). What changed is the harness.

**Corrections (each with the test that proves it).** `_via_size_for_net`
re-resolves at every transaction entry and compares against the size *in force*, so
A -> B -> A ends with A's size (exact setter sequence pinned). `resolve_via_size`
keeps an explicit `0.0` floor and makes absent/non-numeric/non-finite/negative
floors unusable with the fields named. Adopted sizes keep native precision
(a `0.30000000004` floor is pinned). Any setter exception quarantines, because no
readback exists for the router's active sizes (a mutate-then-raise setter is
tested). A plan containing a via is refused with `via_size_unusable` when no lawful
size could be established, instead of routing on a stale setting.

**Harness capability added.** The via search is continuation-aware (a spot whose
far-layer copper is proved to be the target's component closes the hop by itself),
seeded by the pair's own refusal geometry, budget-shared per anchor, and it records
its sample honestly. Component nearest-neighbours are taken over the whole net
rather than the rotating window, with an optional gap ceiling that is **off by
default** because one net here genuinely spans 86 mm.

**Trial (2400 s).** 266 attempts, 1 434 distinct plans, 39 via searches with 28 526
probes and 60 continuation-proved positions, 270 refusals with hint geometry,
1 391 verified rollbacks and **one unverifiable one**: a legitimate 86 mm
connection (net 74) routed 101 mm of copper, the acceptance DRC then failed
(`drc_unavailable`), the rollback could not be verified, and the session
quarantined itself and stopped the run. Fail-closed is correct and nothing was
promoted - the run's accepted artifact is still `6c4f8ab81b83…`.

**Next actionable diagnostic (not a rule change, not a funding question).** Why can
a large transaction's rollback not be verified after the DRC becomes unavailable?
The record is `phase8_routing/run_via2/run_state.json`, pair
`[74, 120.659, 90.912, 2, 205.669, 130.663, 1]`, candidate
`pour_0_component_anchor`, 2 steps, 101.18 mm. Reproduce it in a scratch session
with the engine's error channel captured, and decide whether the restore path needs
a bounded re-probe or whether the failure is engine-side.

`bash tools/reliability/check_phase.sh --strict` -> exit 0 (398 unit + 116 native,
no skips); `check_engine_patches.py` byte-equal; `tools/check_separation.py` 4/4.

## Current state — 2026-09-27, per-net via sizes, rotating windows, 135 unrouted

Status: **a new accepted generation exists**: nine lawful closures in total, all
three production gates passing against the canonical original. Supersedes the
section below it.

**Accepted generation** `1790471639479510000-6c4f8ab81b83-4969cfa5`, board
`6c4f8ab81b83…` (`phase8_routing/run_via1/artifacts/`, private). Final-gate staging
`phase6_final_gates/run_via1/…/1790472116906669000-6c4f8ab81b83-1d75038c`:

| Gate | Evidence |
|---|---|
| native (fresh child, baseline replay) | pass — relevant 7930 -> 7930 (0 added), connectivity 219 -> **200**, total 8149 -> 8130 |
| terminal (fresh two-board) | pass — source 335 clusters / candidate **321**, 1060 terminals both sides, 0 splits, 0 merged |
| complete CLI 9.0.8 | **verified** — raw unconnected `[149,149]` -> `[135,135]` (-14), track_dangling 70 -> 65, every other class exact |

**Two review corrections landed.** (1) The via size is no longer one class applied
to a fixed probe list: `resolve_via_size` resolves the *route net's* own class and
the session applies it at transaction entry, keeping declared vs adopted values,
rejecting non-finite values, and quarantining on a partial diameter/drill setter
pair. (2) The component windows rotate and report coverage
(`ComponentPairScan.coverage`), component identity is a digest of the native
terminal membership rather than a row index, anchors are expanded over the layers
their terminals occupy, and a run whose window or cap omitted part of the graph
stops with `pairs_exhausted_capped` plus a note instead of claiming global
exhaustion.

**Free-position via search.** `_via_free_positions` probes the engine's via
prefilter over a bounded grid (2 mm disc, 0.15 mm pitch, ~558 points, 600-probe
budget) around the anchors and midpoint, one candidate per distance band, skipping
spots the gate already refused that pair at, cancellable and deadline-bounded, with
the budget shared per anchor. The prefilter is a *filter*: it knows about
through-hole pads and nothing about tracks, zones, holes or the via's span, so the
transaction DRC stays authoritative. Refusals also offer a shove-mode escape.

**Validation run (2400 s).** 153 attempts, 4 045 distinct plans, offer sources
ratsnest 1 728 / component 1 399 / substitution 948; new strategies `via_free` 55,
`drc_escape` 325, `via_jog` 273, `drc_avoid` 273; 47 via searches and 11 654
probes; 573 refusals with hint geometry; 3 876 rollbacks `restored` and zero
`retained_unknown`. One closure (net 5, component offer): 136 -> **135** unrouted,
322 -> **321** pad groups, 6 210 tracks. The run ended at its 2400 s boundary - the
engine call that crossed the deadline was reaped, with the best board already
promoted after its fresh-child gate.

`bash tools/reliability/check_phase.sh --strict` -> exit 0 (396 unit + 103 native,
no skips); `check_engine_patches.py` byte-equal; `tools/check_separation.py` 4/4.
Visual QA: `phase8_visual/via1_*`; a copy of the bytes opened in the user's
running KiCad.

**Next actionable step.** `via_free` did not close anything in this run: all 55
evaluations failed to *connect* (not to pass DRC), and the hand-checked pair
(its geometry is in the private evidence `[coordinates redacted]`) has no legal
via within 1.4 mm on either side of the hop. The search therefore needs to be *refusal-driven* rather than
prefilter-driven: seed the grid from the recorded violation positions of the
pair's own refusals (a hole-clearance finding names where the hole field is), and
search the far-layer continuation as well as the via site - a via that lands
legally but cannot continue to the target closes nothing. That is the next
algorithm; the remaining 135 edges are a checkpoint, not completion.

## Current state — 2026-09-27, component-graph scheduling and a router via-size defect

Status: **a new accepted generation exists**: eight lawful closures in total (the
seven-closure board plus five from the coverage pass plus one from component-graph
scheduling, the last two on generations of their own). All three production gates
pass against the canonical original. Supersedes the section below it.

**Accepted generation** `1790457591505422000-ed5b783945c6-c35663e1`, board
`ed5b783945c6…` (`phase8_routing/run_graph1/artifacts/`, private). Final-gate
staging `phase6_final_gates/run_graph1/…/1790467784620017000-ed5b783945c6-205eb4fb`:

| Gate | Evidence |
|---|---|
| native (fresh child, baseline replay) | pass — relevant 7930 -> 7930 (0 added), connectivity 219 -> **201**, total 8149 -> 8131 |
| terminal (fresh two-board) | pass — source 335 clusters / candidate **322**, 1060 terminals both sides, 0 splits, 0 merged |
| complete CLI 9.0.8 | **verified** — raw unconnected `[149,149]` -> `[136,136]` (-13), track_dangling 70 -> 65, every other class exact, zero regressions |

**Scheduling is now component-based.** `native_components` reads the engine's own
cluster membership (323 components over 191 nets here); `component_pairs` offers
the nearest distinct disconnected component pairs; `reanchor_pair_variants`
substitutes a proved endpoint only with its provenance attached
(`component_id`, `moved_mm`, `provenance="candidate"`), never presenting it as the
edge the ratsnest drew, and skips a component already joined to the other endpoint.
An edge with no provable substitute stays in the queue and is counted in
`unresolved_offers`.

**The refusals found a real defect.** 514 recorded `drc_regression` refusals,
grouped by class and position (median 0.113 mm from the plan's line), led to
`Hole size out of range (min hole ...; actual ...)` (`[rule values redacted]`): the PNS size cache
was not populated from the project, so every via the router added was illegal.
`AgentSession._sync_routing_sizes` now adopts the project's own netclass via size,
floored by the board's minimum - the board's declared value, not a relaxation.
Refusals also steer the next plan (`drc_avoid`, `drc_escape`, `via_jog`), with
geometry-canonical names and one hint per class from its earliest record after the
first version livelocked a run.

**Scoped DRC differential extended to the new shapes.** Via insertion and span,
layer transition, a discarded via attempt and an intentional clearance regression
all return identical relevant multisets and identical added-relevant counts against
a whole-board pass on identical copper (`phase8_routing/drc_diff_via5/`). Cases
that produce no copper now fail the differential.

**Campaign.** 8 025 s of a 12 000 s cap over three deterministic segments,
3 356 distinct (pair, plan) evaluations over 315 pairs on the final generation
(ratsnest 1 713 / component 789 / substitution 882; `drc_escape` 280,
`drc_avoid` 250, `via_jog` 245). One closure (net 4, `pour_0_component_anchor`,
from a component offer). Progress 137 -> **136** unrouted, 323 -> **322** pad
groups, 6 208 tracks, 209 vias; 3 195 rollbacks `restored`, zero
`retained_unknown`; stop reason `pairs_exhausted`.

`bash tools/reliability/check_phase.sh --strict` -> exit 0 (385 unit + 88 native,
no skips); `check_engine_patches.py` byte-equal; `tools/check_separation.py` 4/4.
Visual QA: `phase8_visual/graph1_*` (3D top/bottom plus per-layer copper) and the
board opened as a copy in the user's running KiCad.

**Where the remaining 136 edges stand (next actionable step, not a funding
question).** The queue is exhausted for the strategy set as written: 438 of the
final generation's refusals are plans that *did* close the connection and were
refused, and the dominant classes are hole and clearance findings on the copper
itself. The next algorithm is a **direct free-position search for the via** rather
than a fixed jog ring: probe `pad_block_reason(..., for_via=True)` over a bounded
grid (0.15 mm pitch, ~2 mm disc) around the anchor - a geometry query with no DRC
per probe - and route start -> best free spot -> target. The one pair checked by
hand (its geometry is in the private evidence `[coordinates redacted]`) has no
legal via within 1 mm;
larger-radius placement plus shove-mode layer escapes at the recorded violation
positions is the documented next step.

## Current state — 2026-09-27, attempt cost profiled, scoped DRC differential, five more closures

Status: **a new accepted generation exists**: the seven-closure board plus five
more lawful closures from a bounded coverage pass, all three production gates
passing against the canonical original. Supersedes the section below it.

**Accepted generation** `1790451898090613000-f353d35c029d-c6b5df62`, board
`f353d35c029d…` (`phase8_routing/run_cov2/artifacts/`, private). Final-gate
staging `phase6_final_gates/run_cov2/…/1790453581125990000-f353d35c029d-71cd6596`:

| Gate | Evidence |
|---|---|
| native (fresh child, baseline replay) | pass — relevant 7930 -> 7930 (0 added), connectivity 219 -> 202, total violations 8149 -> 8132 |
| terminal (fresh two-board) | pass — source 335 clusters / candidate **323**, 1060 terminals both sides, 0 splits, 0 merged, 0 vanished |
| complete CLI 9.0.8 | **verified** — raw unconnected `[149,149]` -> `[137,137]` (-12), track_dangling 70 -> 65, every other class exact, zero regressions |

**Why the pass was faster.** Attempt cost was profiled with a timing proxy
(`tools/reliability/profile_attempt_cost.py`): full native DRC 38.7-40.1 s, and
eight of them were 312 s of a 352 s window - a closure paid three (probe accept,
apply baseline, apply accept). The deterministic sweep now applies candidates
directly (`_apply_candidates`, `AttemptRecord.sweep_member`) and the scoped
native DRC re-check is an opt-in verification pass, enabled only with the
differential in `tools/reliability/drc_incremental_differential.py` (relevant
multisets identical for direct connect, shove-mode connect, shove of an existing
track and rollback; a rule file changed behind the same path is refused).

**Coverage.** `component_anchor_candidates` + `reanchor_pair` now offer the
scheduled net's nearest *proved* anchors when the ratsnest anchor carries no
copper of that net, labelled `provenance="candidate"`; the offered pair is
retired for that copper generation when its endpoint is replaced. In the
campaign: 16 reanchored pairs, 20 unanchorable anchors, 5 records naming a
component-derived replacement.

**Campaign.** `phase8_route.py` carried the prior run's 246 attempt records
forward only where the pair's local copper is provably unchanged (35/35
examined), then ran 5 693 s of a 12 000 s budget: 932 attempts, 4 228 plan
evaluations, stop reason `pairs_exhausted`, **five closures** (net 6). Progress
142 -> **137** unrouted, 328 -> **323** pad groups, 6 197 -> 6 206 tracks, 209
vias. Rollback discipline: 3 786 records `copper_state=restored` (3 591 after
applied steps), zero `retained_unknown`, zero accepted-but-uncommitted.

`bash tools/reliability/check_phase.sh --strict` -> exit 0 (385 unit + 67 native,
no skips); `check_engine_patches.py` byte-equal; `tools/check_separation.py` 4/4.
Engine C++ unchanged, so patch `0002` is unchanged. Visual QA of the new board
(3D top/bottom plus per-layer copper SVGs) is in `phase8_visual/` (private) and
the board was opened as a *copy* in the user's running KiCad.

## Current state — 2026-09-27, verified re-anchoring + pour-aware routing progress

Status: **a new accepted generation exists**: the canonical six-closure board plus
one lawful pour-aware closure, all three production gates passing against the
canonical original. Supersedes the section below it.

**Accepted generation** `1790443642808325000-bf32c2c099fb-60f29049`, board
`bf32c2c099fb…` (`phase6_final_gates/run_routed_improved/artifacts/`):

| Gate | Evidence |
|---|---|
| native (fresh child, baseline replay) | pass — relevant 7930 -> 7930 (0 added), connectivity findings 219 -> 207, keys without inventory proof 301 -> 296 |
| terminal (fresh two-board) | pass — source 335 clusters / candidate **328** (was 329), 1060 terminals both sides, 0 splits, 0 merged, 0 vanished |
| complete CLI 9.0.8 | **verified** — raw unconnected `[149,149]` -> `[142,142]` (-7), track_dangling 70 -> 65, every other class exact, zero regressions |

**How the closure happened.** The bounded deterministic pass (2400 s; 113 pairs
offered, 18 attempted, 139 plan evaluations over direct/push-and-shove/detour/
obstacle/dodge/layer/pour strategies) closed one connection - that pair's
geometry is in the private evidence `[coordinates redacted]` - with the new
**pour-aware** plan `pour_1_component_anchor` — a waypoint on copper the engine
proved was already connected to the far terminal. Board afterwards: 142 unrouted,
328 pad groups, 6197 tracks, 209 vias.

**Identity gaps are now measured, not assumed.** 34 of 143 ratsnest edges had an
unanchorable endpoint: 6 are track-only clusters (re-anchored from the cluster's
own copper proof), 2 were a track shadowed by an unanchorable zone in the engine
hit test (fixed: `getConnectedPoints` now walks every overlapping item), and 26
sit on no scheduled-net copper at all — named with coordinates, covering zones
and nearest same-net/foreign copper, and never guessed. Engine stamp `ac47369d`,
patch `0002` regenerated and byte-equal.

**Refill branch (re-derived after the metadata migration).** Copper-only refill of
the accepted board: 33 split terminal relations, +14 unrouted, +14 pad groups, 0
added relevant findings — physical, so it stays experimental and unpromoted.

**Visual QA** (separate artifacts, the user's running KiCad window untouched):
3D top/bottom renders and per-layer copper plots of the accepted candidate and of
the improved candidate under `phase7_visual/`; the improved board shows no
rendering or copper defect.

`bash tools/reliability/check_phase.sh --strict` → exit 0 (382 unit + 60 native,
no skips); separation 4/4; `check_engine_patches.py` byte-equal.

## Current state — 2026-09-27, canonical pair accepted, bounded routing pass

Status: **the canonical six-closure generation is promoted for continued work**;
the routing phase ran and produced no further closure, with the reason recorded.
This section supersedes every status line below it.

* Three integration fixes landed after the first review: the CLI gate judges the
  reporter's `unconnected_items` section on its **raw** row count (the collapsed
  total churns 131/131 vs 125/126 at an unchanged raw 143 because the reporter's
  byte-identical repeats vary), the UUID normalizer re-verifies its own output
  (no duplicate, no structural collision, no reference, no UUIDv5 collision)
  before it reports success, and `pcb_world/agent/serialized_metadata.py` +
  `KiCadEngine.save` restore the `(net N)` tokens KiCad drops from non-copper
  graphics (58 of them here, proven inert against the KiCad source and measured
  identical on the complete inventory, terminal partition and relevant DRC).
* With that gate the canonical pair passes **all three** production gates:
  native pass, terminal pass (source 149 unrouted / 335 clusters vs candidate
  143 / 329, 1060 terminals, 0 splits), CLI **verified** (19 of 20 classes exact,
  zero regressions). Accepted generation
  `1790435662721972000-929e56319763-f7fc31ff`, board `929e5631…`.
* Bounded adaptive routing from that board (200 attempts / 1800 s, two passes of
  one run): 116 plans over 18 pairs, 0 closures. 83 plans never connected, 22
  connected but were refused by the native no-new-violation gate, 6 pairs have an
  endpoint the engine resolves to net 0 and cannot plan. Board unchanged at
  143 / 329; the run artifact (`73518eb2…`, metadata restored) also passes all
  three production gates against the canonical original.
* `bash tools/reliability/check_phase.sh --strict` → exit 0 (380 unit + 56
  native, no skips). Evidence: `phase6_canonical/report.json`,
  `phase6_metadata_restore.json`, `phase6_routing/run_adaptive/routing_evidence.json`,
  `phase6_final_gates/run_*/…/final_gate_evidence.json` (all private).

## Current state — 2026-09-27, complete inventory + canonical migration

Status: **board-level acceptance stays withdrawn; the accepted pointer has not
moved.** This section supersedes every status line below it.

**The identity proof is no longer copper-only.** The engine now exposes
`get_board_items()` (patch `0002`, `engine_server/wire.py` +
`pcb_world/engine/wire.py` carrier `BoardItemInfo`), and `BoardInventory` is
built from that one accessor (policy `inventory-identity-v2`). Every container
the board owns is covered - tracks, vias, pads, board zones, drawings and
groups, and each footprint with its graphical items, fields, zones and groups -
so a violation naming a zone, a courtyard graphic or a footprint can be
**proven** to name one physical item. A missing or failing accessor marks the
inventory incomplete; there is no fallback to the partial rows.

**Duplicate identifiers are repaired on a copy.** `pcb_world/agent/
kicad_metadata.py` rewrites only the identity tokens of reused UUIDs, in place,
by UUIDv5 of each occurrence's own parsed structure; everything else keeps its
exact bytes and order, structurally identical occurrences and references are
refused, and the result is idempotent with its mapping and hashes carried as
evidence (`tests/agent/test_kicad_metadata.py`).

**Measured on the frozen V3 pair** (private evidence, `phase6_*`):

| Measurement | Before | After |
|---|---|---|
| Relevant findings without an inventory proof (frozen reference) | 7924 / 7930 (copper-only) | 1177 / 7930 (complete inventory); 301 on the canonical board, all nil-UUID sides |
| Reused identifiers | 922 over 8164 occurrences | 0 (canonical source and canonical candidate) |
| Native gate on the six-closure candidate | refuse — 179 added identity groups | **pass** |
| Terminal partition (canonical pair) | pass | pass — 335 clusters, 1060 terminals, 0 splits |
| CLI gate (canonical pair) | refuse — identity churn in 6 relevant classes | **unverified** — 19 of 20 classes exact, zero regressions; only the reporter's `unconnected_items` byte-identical duplicate rows vary between runs of one board (17 vs 18 at an unchanged raw 143) |

Zero-route save/reopen of the canonical board is stable twice over (identical
inventory, terminal partition and relevant DRC multiset; the two saved files are
byte-identical), and the original→canonical terminal partition is identical
under the UUID-free physical mapping. The engine stamp is now
`ENGINE_CPP_HASH=dc39720d`; rerun `python tools/reliability/
check_engine_patches.py` for the byte-equality proof. `bash tools/reliability/
check_phase.sh --strict` → exit 0, 360 unit + 56 native, no skips.

Updated: 2026-09-26. Status: **board-level acceptance is withdrawn pending
revalidation.** The previous phase-5 acceptance depended on terminal and CLI
evidence that did not bind UUIDs to physical pad identity and did not validate
report integrity sufficiently. Treat the detailed measurements below as
historical until reproduced under the current gates.

## Current state — 2026-09-26, inventory-proven identity + production final gates

Status: **board-level acceptance stays withdrawn and the accepted pointer has not
moved.** This section supersedes the status lines and numbers below it; those are
kept as the history of the review that produced them.

**Build and suite.** Engine stamp `ENGINE_CPP_HASH=e290ee11`, `ENGINE_VERSION=1.4`,
router module sha256 `bd8f8b30…`. `python tools/reliability/check_engine_patches.py`
reads all six patched files from the pinned engine commit `7a31e0c`, applies
`0001` then `0002`, and reports each result byte-equal to this checkout's engine
tree with identical `wire.py` copies. `bash tools/reliability/check_phase.sh
--strict` → **exit 0, 333 unit + 49 native, no skips**
(`docs/agent-work/reliability/evidence/phase_check.json`).

**Identity is inventory-proven, not UUID-pair-proven, and not guessed.** A
violation key keeps its movement tolerance only when the board's own inventory
proves the pair names one physical violation: a UUID carried by exactly one item
*is* that item (identity = the UUID, so existing keys are unchanged). Everything
else — a reused UUID, an item the accessors do not expose, the nil UUID, an
unreadable inventory — is *unverified*, and its key falls back to a
condition-refined identity where any change of position, layer or net set is an
addition. **There is no proximity threshold**: root review rejected the earlier
2.0 mm / 0.1 mm nearest-candidate rule because a heuristic must not be labelled
proven identity. Measured on the frozen reference: **121 of 6845 copper UUIDs are
reused (821 of 7545 items, all pads)**, and **7972 of 7986 relevant keys have no
inventory proof** — 99.8% of them, because most findings involve an item the
inventory cannot name. The ambiguity is repaired on a disposable copy of the
board (canonical metadata repair), never by relaxing this gate.

**Production final gates.** `pcb_world/agent/final_gates.py` is the production
adapter set for `ExperimentalArtifactStore.run_final_validation`:
`tools/reliability/verify_saved_artifact.py` in a fresh owned child (reopen +
baseline replay), the same verifier in `terminal_partition` mode (both boards in
one fresh process), and the complete 9.0.8 CLI gate whose only exemption —
churned `unconnected_items` endpoint pairing — is bound to that terminal proof.
`tools/reliability/run_final_gates.py` runs all three for one staging generation
and writes the evidence even when it refuses.

**The baseline is bound to the bytes and the build it came from.** A captured
baseline travels as an *envelope* (`pcb_world/agent/reference_baseline.py`): the
violation multiset plus the reference board/project/rules hashes, the identity
policy, and the engine that measured it (`cpp_hash`, module sha256, version,
provenance-checked). `verify_generation` re-measures all of that inside the child
*before* it replays a single identity and refuses with named problems - a baseline
captured from another board, from other sidecars, under another policy, or by a
different engine cannot ride on the reference's hashes. Both the runner's
in-process capture and the child's `capture_baseline` mode build the envelope
through the same module, so the producer and the consumer cannot drift. The
adapters also re-hash every input after each child returns (refusing on drift with
"an input changed while the gate was running") and cap each child at
`min(per-child ceiling, remaining run budget)` when a budget callable is
configured.

| Gate | six-closure `255a5cb9…` | copper refill `9017be2e…` |
|---|---|---|
| native (fresh child, baseline replay) | **refuse** — 179 added relevant identity groups at an unchanged 7930 relevant findings; **all 179 sit on pair keys the baseline already had, 0 new pairs** | **refuse** — 19 added groups, **all 19 new pair keys**, every one an `Isolated copper fill` |
| terminal (fresh two-board partition) | **pass** — 335→329 clusters, 1060 terminals both sides, 0 splits, 0 vanished nets/terminals | **refuse** — 33 split relations, 343 clusters for 1060 terminals, 0 vanished |
| complete CLI 9.0.8 (2 runs/board) | **refuse** — identity churn at equal counts: clearance 9, silk_overlap 60, silk_over_copper 10, courtyards_overlap 6, starved_thermal 2, lib_footprint_issues 2 | **unverified** — its unconnected pairing churned and the terminal proof is not ok, so no exemption applies; class deltas recorded (clearance 7214→0, isolated_copper 1→4, unconnected 113→126) |

Both candidates were validated against the frozen original reference
(`133edc28…`) with their exact hashes bound by every gate; evidence is private in
`phase5_final_gates/{sixclosure,copper_refill}_evidence.json`. Neither was
promoted.

**Why the refusals happen, in order of size.**

* The six-closure native refusal is the measured cost of the stricter identity
  rule, not a new physical finding: a dedicated comparison
  (`phase5_identity_cost.py`, evidence in `phase5_final_gates/
  sixclosure_identity_cost.json`) reports **179 added groups / 179 under an
  existing pair key / 0 new pair keys, all `Clearance violation`**, while the
  previous key-only policy reports **0 added groups for the same pair of boards**.
  With 99.8% of keys unproven, a candidate that only adds copper still moves the
  reported condition of pre-existing findings, and every such move is now an
  addition.
* The refill's 19 added native groups are **all new pair keys** (`Isolated copper
  fill`), and the previous policy reports the same 19 — the tightening did not
  create them. (The earlier phase-5 note of "four isolated-copper additions"
  describes the refill against the six-closure state it was built from; against
  the frozen reference the gate binds to, the number is 19.)
* The CLI refusal is the reporter's *representative-pairing* choice, not a
  changed physical condition: 250 of the 321 footprint UUIDs are reused, and the
  candidate file lists its footprints in a different order than the frozen
  reference, so 60 `silk_overlap` findings name the same zone/segment condition
  through a different representative segment (same counts, same totals).
* The refill's terminal refusal is the connectivity cost already documented in
  §"Repair evidence so far": 33 previously connected terminal pairs are no longer
  connected, and no added identity or count waiver can excuse that.

**Decisions this leaves with root** (not implementation choices):

1. Whether the native copper inventory should be extended to the items the
   current accessors do not name (zones, footprint graphics) so that the ~50% of
   findings that reference them can be proven too. That is a native binding change
   with its own fixture, and it would restore movement tolerance for those keys.
2. Whether a candidate may be compared from a *byte-order-normalized* reference,
   or whether item order in a generation must be preserved, given that the CLI's
   relevant-class identities churn when footprint order changes.
3. Whether the copper refill stays an experimental staging state at all; nothing
   in this work makes it promotable.

## Recovery run 2026-09-26 (later) — build restored, review fixes landed

**Pinned router rebuilt, strict suite green.**

* The engine submodule's `kicad-python` checkout is absent in this working tree,
  so `engine/build_rl_router.sh` cannot rsync a pristine source. The already
  synced `build_rl/kicad_src` was kept and the script's own patch-copy list was
  replayed (`mkdir -p` + the 44 `cp -p` lines extracted from the script); all 44
  files are byte-identical to `engine/kicad-patches/` afterwards.
* `wx-config` was never missing — it is the project-local symlink
  `.local-bin/wx-config -> /opt/homebrew/opt/wxwidgets@3.2/bin/wx-config-3.2`
  (wxWidgets 3.2.11). Builds must run with `PATH="$PWD/.local-bin:$PATH"`.
* `build_rl/kicad_src/pcbnew/CMakeLists.txt` carried a build-tree-only SWIG 4.5.1
  compatibility fix (Homebrew swig 4.5.1 no longer emits the `PyInt_FromLong` /
  `PyString_Check` shims for 9.0.8's `common/swig/wx.i`). It is now part of the
  patch tree, so the tree and the patch set agree.
* `ninja -C build_rl kicad_rl_router` → 5 steps (2 compiles + link). New stamp
  `ENGINE_CPP_HASH=e290ee11`, `ENGINE_VERSION=1.4`;
  `pcb_world.engine.ensure_router_provenance()` passes with no
  `PCBWORLD_ENGINE_ALLOW_MISMATCH`.
* `bash tools/reliability/check_phase.sh --strict` → **309 unit + 45 native
  passed, exit 0** (before: 295 unit, 44 native setup errors).
* The native failures were one real defect plus one wrong expectation:
  `pns_rl_bindings.cpp` now exposes `PadInfo.uuid`/`PadInfo.physical_id`, so the
  wire registry needed the same fields in *both* `wire.py` copies (the engine
  server refuses to start on schema drift); and Luna's new fill test expected
  "design rules failed to load" for a *missing* rules file, where the engine
  correctly says "design rules file not found".

**Root review findings (integrity) fixed, each with a direct repro test.**

1. `run_gate` now accepts `source_project`/`candidate_project` — the runner had
   always passed them, so a real configured gate raised `TypeError`; the explicit
   project is what gets staged and verified.
2. `ExperimentalArtifactStore.promote_after_final_gates` takes
   `run_final_validation(...)` evidence: the store recomputes the *current*
   candidate's board/project/rules hashes plus the frozen reference's full
   sidecar hashes, and every gate must name all six. Stale evidence for candidate
   A can no longer promote candidate B. Staging now fsyncs bytes, manifest and
   directory, and the pointer's checkpoint is built with the new generation's
   path/hash (a crash-resume no longer reads the previous best board).
3. CLI identity is collision-safe (UUID + position + semantic + description, as a
   multiset) and byte-identical repeat rows are dropped once; the comparison
   runs over the **union** of runs, so a churned item pairing cannot hide an
   addition and a vanished identity at an unchanged total is refused. The native
   gate's pair key is likewise a multiset now (`ViolationSet.counts/members`):
   measured on the frozen source, 226 keys carry more than one violation and the
   old dict dropped 835 violations (998 relevant).
4. Completeness is a reporter *policy*: vetted version list (9.0.8 only),
   required severities, vetted options, provider from its documented app-bundle
   location only (`Contents/PlugIns/_pcbnew.kiface`, hashed), optional exact
   CLI/provider hashes. A report from an unknown reporter is refused even when no
   class sits on a known cap.
5. `run_gate` hashes every input *before* staging, requires each staged copy to
   be byte-identical to its source, re-checks the originals, the staged copies,
   the binary and the provider after the runs, and records
   `hash_capture=before_staging` plus `staged_inputs_match`.

**Copper-only refill, measured on the real board.** From the immutable
six-closure reference `phase5_diag/c_candidate/candidate.kicad_pcb`
(sha256 `255a5cb9…`), generation
`phase5_repair_run/h_copper_refill/` (private):

| | six-closure | copper refill |
|---|---|---|
| non-copper zones (72, silk/mask) | — | **byte-identical, 0 differing** |
| copper zones with rewritten fill | — | 95 |
| native relevant / distinct identities | 7930 / 7310 | 313 / 373 |
| unrouted edges | 143 | 157 |
| added relevant identities (multiset) | — | **4, all `isolated_copper`** (3 on one supply net, 1 on another) |
| terminal partition | — | **33 split relations** (GND 20 incl. 18 on one pad, three supply nets at 4 each, one further net at 1), 0 vanished |
| complete CLI (9.0.8, 2 runs) | 9754 findings | 1973; `clearance` 7214→0, `copper_sliver` 9→0, `isolated_copper` 1→4, unconnected 109→126 |

Verdicts: the refill is **staging-only, not acceptable** (native gate: 4 added
identities; CLI gate: `regressed`). The connectivity cost is unchanged from the
earlier all-zones refill, but the 95 silk/mask findings are gone — the copper-only
filter does what it was written for.

Open: `courtyards_overlap` reports 11 churned identities at an equal count
(52→52) between the two boards; the items involved are footprints whose UUID,
position *and* description coincide (duplicated UUIDs), so the reporter's item
identity is ambiguous there. It needs a decision (treat as reporter ambiguity, or
bind those items to the board's physical inventory) before any promotion claim.

Not done: the bounded repair/routing run (up to 200 attempts / 30 min) against
those 33 split relations. The generation above is the checkpoint for it.

Current verified status:

* Focused terminal, CLI-gate, and artifact-store tests pass: **38 passed**.
* The current Python unit group passes: **295 passed**.
* `bash tools/reliability/check_phase.sh --strict` **fails closed**: the native
  group cannot load the cached router because `RouterProvenanceError` detects a
  source/build mismatch. The C++ changes below have not been rebuilt; the earlier
  build attempt stopped because `wx-config` is unavailable. The cached module is
  not validation evidence.
* The 10.0.6 installed CLI report is capped and diagnostic only. Complete 9.0.8
  CLI evidence predates the current C++ changes and must be regenerated before
  any candidate acceptance.
* Prior terminal-pair and per-net relation counts are withdrawn as unverified.
  UUIDs are reused on this board; native inventory now binds UUIDs to pad
  geometry and inventory and refuses collisions.
* Refill was measured to alter 64 non-copper silk/mask zones, accounting for 95
  added silk findings. Refill must therefore touch copper zones only. All refill
  and route repairs remain experimental; the frozen source and six-closure
  reference stay immutable.

The approved experimental workflow now keeps an isolated staging pointer bound
to an immutable original reference. It cannot change the accepted pointer or
claim global acceptance. Promotion requires native evidence, a complete CLI
report, and terminal connectivity compared against the original reference.

## Historical evidence before the recovery correction

The status table and detailed repair measurements below preserve the prior
checkpoint for traceability only. Their pass/acceptance claims and terminal
relation counts are superseded by the correction above; do not use them as current
verification or promotion evidence.

| Item | State |
|---|---|
| Workspace | `/Users/leo/Documents/PCBWorld-reliability`, branch `feat/agent-reliability-actions`, base `b3d62f5` |
| Gate | `bash tools/reliability/check_phase.sh --strict` → exit 0 (275 unit + 37 native, zero skips) |
| Agent suite | `pytest tests/agent -q -o addopts=` → 312 passed |
| Pinned engine | unchanged: `kicad_rl_router.so` sha256 identical before/after the CLI build; stamp `2613fb07` |
| Complete reporter | task-isolated build-tree `kicad-cli` 9.0.8 (`build_rl/kicad/KiCad.app/Contents/MacOS/kicad-cli`), needs `KICAD_RUN_FROM_BUILD_DIR=1` |
| Installed application | 10.0.6, untouched; its report is capped and its verdict is `unverified` |
| Candidate | 0 added identities in both complete gates; 20 new segments; no placement/zone/fill change |
| Git state | nothing committed, pushed or staged; `git diff --check` clean |

## What changed in the repository

* `pcb_world/agent/cli_gate.py` — the installed-CLI gate: truncation detection,
  mandatory reproducibility, exact identities, no count-only path, full verdict
  provenance, and `env` support for a task-isolated binary.
* `pcb_world/agent/runner.py` — `cli_gate` / `cli_verifier` configuration, the
  verdict recorded in every generation manifest, promotion refused on
  `regressed` or on a required-but-not-`verified` verdict, and a fail-closed
  `blocked` report (instead of an exception) when the baseline itself cannot pass.
* `tests/agent/test_cli_gate.py` — the sound-policy tests, including the
  equal-count-swap regression, cap detection, non-reproducibility refusal, the
  missing-CLI status, and runner promotion refusal.
* `tools/reliability/check_phase.py` — the new suite is part of the strict gate.

## Reproduce

```bash
cd /Users/leo/Documents/PCBWorld-reliability
bash tools/reliability/check_phase.sh --strict

# rebuild the complete reporter if the private build tree is cold
ninja -C build_rl kicad-cli pcbnew_kiface

# complete vs capped gate on the private candidate
export KICAD_RUN_FROM_BUILD_DIR=1
export DYLD_LIBRARY_PATH="$PWD/build_rl/kicad/KiCad.app/Contents/Frameworks"
.venv/bin/python /Users/leo/Documents/Helix_Control_V3_pcbworld_improved/phase5_dual_gate.py \
  <source.kicad_pcb> <source.kicad_dru> <candidate.kicad_pcb> <candidate.kicad_dru> \
  /Users/leo/Documents/Helix_Control_V3_pcbworld_improved/phase5_diag/dual_gate.json --runs 2
```

## Next phase (resume here)

**Contract C status: the refill capability exists and is measured; adopting it is
a decision, not a step.** `KiCadEngine.fill_zones` (engine stamp `16e66300`)
clears all 6904 fill-clearance findings on a disposable copy (complete CLI:
`clearance` 7488 → 0, findings 9758 → 2173) at the price of **11 lost
connections** (unrouted 149 → 160, pad groups 335 → 346) and 117 new findings in
other classes. It is retained as the private generation
`phase5_diag/e_refilled/refilled.kicad_pcb` (`e7e75378…`) and is **not** the
authoritative routing state.

Remaining work, in order:

1. **Decide the refill's role** (operator/root): adopt it as a staging state whose
   eleven lost connections are restored by routing and re-verified under both
   gates — or route the original board and leave the pre-existing fill/rules
   mismatch in place. The trade is measured in RESULT §8; no heuristic is needed.
2. **Bounded routing** under full native + complete-CLI acceptance, whichever
   state is chosen, with per-net terminal connectivity compared explicitly.
3. Open items carried from phase 4 remain: a hung native call still cannot be
   pre-empted in-process, and richer deterministic candidate geometry
   (obstacle-derived multi-waypoint paths) is the next routing work.

## Version boundaries (must travel with any acceptance)

| Tool | Version | Role |
|---|---|---|
| Pinned engine, private build | C++ stamp `16e66300`, `.so` recorded in `provenance_phase5.json` | acceptance gate |
| Build-tree CLI from the pinned source (stock clearance provider, caps removed) | **9.0.8** | acceptance gate |
| Installed application | 10.0.6 — report capped | diagnostic only; never relabelled verified |

Any board acceptance must name the exact KiCad version whose report supported it.

## Approved experimental repair architecture (root decision, 2026-09-26)

Root approved using the refilled board as an explicitly **unaccepted, disposable
staging/repair candidate** — reversible engineering inside the authorised task,
not a physical-design permission question. The accepted pointer is not moved, no
staging violation is relabelled, and the final baseline is not reset to erase the
lost connections or the added findings.

Rules this phase now operates under:

1. The frozen source and the six-closure reference stay immutable. The repair
   branch starts from the **six-closure** board (to preserve useful routes) after
   explicit revalidation under the new engine build; a new build gets new-run
   source/candidate checks, never `allow_mismatch`.
2. Two distinct states exist: the last **accepted** generation and the current
   **experimental repair** generation. Repair checkpoints are durable but
   non-promoted and live outside the run's `artifacts/` pointer, so normal resume
   still trusts only `accepted_artifact.json`. Local route transactions may
   compare against their immediate experimental state; **final promotion always
   compares against the original accepted reference** under native + complete-CLI
   detailed DRC and per-net terminal connectivity.
3. The lost connections are enumerated as per-net relations, not totals, and all
   previously valid ones must be restored before promotion; other closures may
   not mask a lost relation.
4. The added findings are diagnosed by rule and item geometry; no design object,
   thermal rule or stackup is changed to make DRC green, and staging violations
   are not waived as final ones.
5. Planner budget: at most 10 additional real HTTP requests and 200k additional
   tokens **across all phase-5 copies** (not per copy). Deterministic and native
   work needs none.

### Repair evidence so far

Refilling the six-closure board under the current rules (disposable generation
`phase5_diag/f_repair/refilled.kicad_pcb`, sha256 `ee0c6094…`):

| | six-closure | refilled repair |
|---|---|---|
| engine relevant findings | 7930 | 313 |
| engine clearance identities | 6904 | 0 |
| complete-CLI findings | 9754 | 2167 |
| complete-CLI clearance identities | 6904 | 0 |
| **unrouted edges** | **143** | **157** |
| **pad groups** | **329** | **343** |

Per-net terminal relations (engine `get_pad_groups`, 191 nets compared):

* source → six-closure: **0 lost, 6 gained** (4 nets improved);
* six-closure → refilled repair: **14 relations lost on 5 nets** — GND 34 → 44
  (ten poured islands), and one each on nets 28, 156, 158 and 160, where the last
  three are supply nets.

Added findings versus the frozen source, diagnosed by rule: 116 identities —
`silk_overlap` 75, `silk_over_copper` 20, `starved_thermal` 16, `isolated_copper`
4, `track_dangling` 1. Every one is a consequence of the fill silhouette
changing (silkscreen measured against a retreated pour, thermal reliefs
re-evaluated, isolated islands left by the retreat); no footprint, zone outline,
net, layer, priority, thermal setting or stackup moved.

Count reconciliation (machine-readable, private): both providers report the same
**7488 clearance findings** on the frozen source; **6904** is the number of unique
identities in that set. The earlier phase-3 text that used 7488 for the engine and
6904 for the CLI was comparing findings to identities, not two different boards.

### Not yet done (updated after the root correction pass)

* **Terminal relations, measured properly**: the refill splits **33 GND terminal
  pairs** (centred on one pad), not the 14 group-count deltas previously reported.
  Restoring them by lawful routing is the repair's core work.
* `cli_gate` hardening: reject a malformed/empty report (`[{}, {}]` must not equal
  a valid empty one), validate schema/types/version, check unconnected-item
  truncation and reproducibility, hash the CLI binary and the loaded kiface,
  verify the sidecars the CLI actually reads, fail closed on missing item
  identities, keep immutable per-generation report copies, and bound gate work to
  the run deadline.
* Explicit experimental lifecycle state: a repair branch must be flagged, bound to
  an immutable original acceptance reference, and refused by normal
  resume/promotion unless it passes the original-reference native + complete-CLI +
  terminal-partition checks.
* Package the refill as a reproducible patch under `patches/` with build evidence
  and a documented GUI stub.
* A real-zone native fill test (valid vs missing vs malformed rules, save/reopen,
  failure leaves the source intact).
* Geometry evidence for the added silk findings, and the long bounded
  repair/routing run (up to 200 attempts / 30 min with stall retirement).
* Root acceptance of the repair, and root's review of the CLI-gate code, are both
  still pending.
