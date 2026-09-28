# RESULT — phase 5: installed-KiCad cross-check and recovery

Status: **historical candidate acceptance is withdrawn pending revalidation.**
The prior sections record the investigation and measurements as they stood then;
they are not current acceptance evidence. The terminal comparison did not
reliably bind reused UUIDs to physical pads, and the CLI gate had integrity gaps.
See the recovery correction in [CHECKPOINT.md](CHECKPOINT.md).
Contract: [PLAN.md](PLAN.md). Handover: [CHECKPOINT.md](CHECKPOINT.md).

## 1. The number that blocked phase 4

Phase 4 recorded that the installed KiCad CLI reported 23 added and 27 resolved
findings between the frozen source and the six-closure candidate, while the pinned
engine reported zero added relevant identities. Both numbers were real
measurements of *different things*, and neither was a statement about the board.

## 2. Save-only is physically transparent (measured)

A zero-routing load/save roundtrip of the frozen baseline through the pinned
engine, with the project and rules copied verbatim, compared as board data:

| Class | Source | Roundtrip | Changed | Added | Removed |
|---|---|---|---|---|---|
| Track segments | 6073 | 6073 | 0 | 0 | 0 |
| Vias | 209 | 209 | 0 | 0 | 0 |
| Footprint blocks (geometry multiset) | 321 | 321 | 0 | 0 | 0 |
| Zones (declared properties) | 177 | 177 | 0 | 0 | 0 |
| Zone fills (polygons / vertices / digest) | 194 / 88596 / equal | 194 / 88596 / equal | — | — | — |

The installed CLI's own report of the two boards is likewise the same finding set.
The engine's save therefore changes no physics; it only re-serialises.

## 3. The installed CLI truncates its report (measured)

KiCad's `DRC_ENGINE` caps findings per error code. Reading the pinned source:
`ERROR_LIMIT = 199`, `EXTENDED_ERROR_LIMIT = 499` for clearance and unconnected
items (the same file notes the caps exist to keep the GUI marker list responsive,
and that a capped run even misfiles shorts once they are exhausted).

The installed 10.0.6 CLI reproduces those numbers exactly, every run:

| Class | Reported | Cap |
|---|---|---|
| `clearance` | 499 | `EXTENDED_ERROR_LIMIT` |
| `drill_out_of_range`, `lib_footprint_issues`, `silk_overlap`, `silk_over_copper`, `starved_thermal` | 199 each | `ERROR_LIMIT` |

Five classes sitting on exactly 199 is not a property of a board. The
run-to-run identity churn phase 4 measured is the truncation boundary moving:
comparing two capped reports compares which findings made the cut. **The phase-4
23-added figure is that artefact, and a capped report cannot support any
identity claim.**

## 4. A complete reporter, and what it says

Because the pinned source already carries the PCBWorld cap-removal modification,
the tree can build its own `kicad-cli` (`ninja kicad-cli`, ~8 s incrementally, plus
`pcbnew_kiface` for the loader, ~93 s). It is a task-isolated artifact:

* version **9.0.8** (the installed application is 10.0.6 — recorded, not conflated);
* it links the **stock** KiCad copper-clearance provider, while the router module
  links the RL fork, so its DRC is an independent implementation;
* the pinned engine build was not rebuilt: `kicad_rl_router.so` sha256 and the
  `2613fb07` stamp are unchanged, verified before and after;
* it is configured explicitly (`cli_path` + `env`), never installed over the
  user's application and never touching global settings or design rules.

Complete reports, three runs per board, are **identical run to run**:

| Board | Findings | Unconnected |
|---|---|---|
| Frozen source | 9758 (×3) | 149 (×3) |
| Zero-routing roundtrip | 9758 (×3) | 149 (×3) |
| Six-closure candidate | 9754 (×3) | 143 (×3) |

Against that reporter, the candidate's delta is exact and tiny:

* **0 added finding identities in every class**;
* `track_dangling` 70 → 66 (4 resolved, 0 added);
* every other class identical, including `clearance` 6904 = 6904 with the same
  identity set (not merely the same total);
* unconnected items 149 → 143;
* the source and its own roundtrip copy compare equal in every class.

The physical delta of the candidate, from the board data:

| Class | Source | Candidate | Changed | Added |
|---|---|---|---|---|
| Track segments | 6073 | 6093 | 0 | 20 |
| Vias | 209 | 209 | 0 | 0 |
| Footprint blocks (geometry multiset) | 321 | 321 | 0 | 0 |
| Zones / fills | 177 / 194 | 177 / 194 | 0 | 0 |

The 20 new segments are the six closures, all one width (`[rule values redacted]`), on the nets the run
records as connected. No placement, zone, fill, via, or existing track changed.

## 5. The gate was rebuilt, not loosened

`pcb_world/agent/cli_gate.py` now:

* **refuses a report that may be truncated** — any class whose count equals a
  known per-class cap (199/499) yields `unverified` with the reason naming the
  class and pointing at the complete-report remedy;
* **requires reproducibility** — fewer than two runs per board, or any class whose
  identity set varies between runs, yields `unverified`; a count-only verdict is
  no longer reachable;
* **compares exact identities** for every class, plus non-increasing counts and
  non-increasing unconnected items;
* records the CLI path, version, options, rule hashes, both board hashes, the
  per-class counts and identity results, and whether the report was complete;
* distinguishes `not_configured` (native-only acceptance, stated as such),
  `unavailable` (no usable CLI), `unverified`, `regressed` and `verified`.

The regression the architecture hold demanded is in the suite: a candidate that
*replaces* one clearance finding with a different one at an equal total is refused
(`test_a_new_finding_replacing_a_resolved_one_at_equal_count_is_refused`), as is a
report at a known cap, a non-reproducible class, and a single-run comparison.

## 6. Verification

```bash
cd /Users/leo/Documents/PCBWorld-reliability
bash tools/reliability/check_phase.sh --strict     # exit 0: 275 unit + 37 native, no skips
PYTHONPATH=$PWD .venv/bin/python -m pytest tests/agent -q -o addopts=   # 312 passed
git diff --check                                   # clean
```

Private evidence (boards, reports, comparison detail) stays in
`/Users/leo/Documents/Helix_Control_V3_pcbworld_improved/phase5_diag/`; this
document carries aggregates only.

## 7. Limits

* The complete reporter is KiCad **9.0.8** from our pinned source; the user's
  installed application is 10.0.6. Both were consulted; only the complete one is
  used for acceptance, and the version is recorded in the verdict.
* The engine's own RL copper-clearance provider reports many more clearance
  findings than the stock provider on this board (7488 vs 6904 on the source).
  That is a provider difference, not a board difference; the engine gate remains a
  *relative* zero-added gate, and the complete CLI is the absolute reference.
* No zone refill and no further routing was attempted in this phase: the operator
  contract authorises them only after this boundary, which is now resolved.

## 8. Zone refill (contract C): measured, and a trade rather than a win

The engine gained a derived-copper refill: `KiCadEngine.fill_zones(rules_path)`
(`PNS_RL_ROUTER::fillZones`), patched in `engine/kicad-patches/rl/`, rebuilt into
the private `kicad_rl_router.so`, engine C++ stamp `2613fb07` → `16e66300`. The
filler needs `ZONE_FILLER`, which lives in the pcbnew kiface this routing module
does not link, so the source is compiled into the module and the one interactive
dialog path (behind a non-null parent, never taken headlessly) is stubbed. It
refills and rebuilds connectivity in 2.2 s on V3, touching fill polygons only.

Measured on a disposable copy of the frozen source, under the current rules:

| Metric | Source | After refill | Δ |
|---|---|---|---|
| Engine fill-clearance findings | 6904 | 0 | −6904 |
| Engine relevant DRC findings | 7930 | 313 | −7617 |
| Complete-CLI findings | 9758 | 2173 | −7585 |
| Complete-CLI `clearance` | 7488 | 0 | −7488 |
| **Unrouted edges** | **149** | **160** | **+11** |
| **Pad groups** | **335** | **346** | **+11** |
| Tracks / vias | 6175 / 209 | 6175 / 209 | 0 |
| Complete-CLI new identities | — | 117 | silk_overlap +75, silk_over_copper +20, starved_thermal +17, isolated_copper +4, track_dangling +1 |

The clearance class is entirely resolved, and the price is visible in the same
measurement: the pour that previously bridged eleven connections retreats, so
**eleven existing connections are lost**, and the changed fill silhouette adds 117
findings in other classes. Both the engine and the complete CLI agree
(149 → 160 unconnected); the installed-CLI refill showed the same effect
(149 → 161) before the file-format boundary stopped it.

**Therefore the refilled board is not adopted as the authoritative routing state.**
The operator contract requires that loss of existing connections be visible and
not hidden by other nets' improvements, and this refill fails that condition on
its own. It is retained as a private generation
(`phase5_diag/e_refilled/refilled.kicad_pcb`, sha256 `e7e75378…`) for the
follow-up decision: adopt it only as a *staging* state whose eleven connections
are restored by routing and re-verified under both gates, or leave the
pre-existing clearance/rules mismatch in place and route the original board (which
the six closures already did).

## 9. Experimental repair branch (root-approved staging)

Root approved the refilled board as an **unaccepted, disposable staging/repair
candidate**. It is not promoted, the accepted pointer is untouched, and no staging
violation is relabelled.

Starting from the **six-closure** board (to preserve useful routes) rather than
the source, refilled under the current rules, and revalidated under the new engine
build (stamp `16e66300`):

| | six-closure | refilled repair | repair after a bounded routing pass |
|---|---|---|---|
| engine clearance identities | 6904 | 0 | 0 |
| complete-CLI findings | 9754 | 2167 | — |
| unrouted edges | 143 | 157 | **156** |
| pad groups | 329 | 343 | **342** |
| tracks | 6195 | 6195 | 6197 |

Per-net terminal relations (`get_pad_groups`, 191 nets):

* source → six-closure: **0 lost, 6 gained** (4 nets improved);
* six-closure → refilled repair: **14 relations lost on 5 nets** — GND 34 → 44
  (ten poured islands), and one each on nets 28, 156, 158 and 160;
* after a 10-attempt deterministic repair pass: **1 restored** (net 5, 0.7446 mm
  of copper, accepted by the native gate with 0 added relevant findings); 13
  relations remain to restore.

Added findings versus the frozen source, diagnosed by rule: 116 identities —
`silk_overlap` 75, `silk_over_copper` 20, `starved_thermal` 16, `isolated_copper`
4, `track_dangling` 1. Each is a consequence of the fill silhouette changing
(silkscreen measured against a retreated pour, thermal reliefs re-evaluated,
islands left by the retreat). No footprint, zone outline, net, layer, priority,
thermal setting or stackup was altered.

**Refilled polygons survive routing.** After the ten attempts, comparing the
repair board with its routed artifact: 2 new segments, 0 changed, vias and
footprint geometry identical, zone properties unchanged and **fills byte-identical
by digest** (180 polygons). A rollback therefore does not corrupt the derived
copper — the property the contract required.

### Open
The 13 unrestored relations, the 116 added identities, and the final promotion
comparison (original accepted reference, native + complete CLI, per-net
connectivity) remain. Root acceptance of this repair is pending, as is root's
review of the CLI-gate code.

## 10. Correction pass (root review findings)

**Historical correction, itself superseded by the recovery correction at the end
of this report.** The terminal relation figures in this section were obtained
before pad identity was bound to physical inventory, so the apparent 33-pair
measurement is not current evidence. The refill impact is now characterized as
64 changed non-copper silk/mask zones accounting for 95 added silk findings.

Root rejected the phase-5 report as written. What changed:

**Terminal connectivity is now a partition, and the earlier claim was wrong.**
`pcb_world/agent/terminals.py` compares *which terminals share a cluster*, not how
many groups a net has, and the engine gained
`getPadClusterMembers` (native `CN_CLUSTER` membership, named `REF.PAD`) so the
partition comes from the same connectivity `get_pad_groups` counts. Re-measured:

| comparison | group-count delta (old claim) | terminal relations split (correct) |
|---|---|---|
| source → six-closure | 0 | **0** |
| six-closure → refilled | +14 groups | **33 pairs, all GND** |
| source → refilled | +11 groups | **33 pairs, all GND** |

The splits centre on one pad, which the pour had joined to ~33 GND terminals and
no longer does. The historical "14/13 lost relations" were **group-count deltas**,
as root said; the measured relation loss is 33 pairs. The comparison refuses on a
vanished terminal or net even when counts agree, and the equal-count-swap,
lost-masked-by-gained and removed-terminal cases are unit-tested. It is wired into
the fresh-process saved-artifact gate (`verify_saved_artifact.py`) and the runner
carries the accepted reference's partition into every promotion request.

> This paragraph describes the historical analysis only. Its terminal counts and
> the pad attribution are withdrawn until captured with the current physical pad
> identity and complete native inventory.

**Fill is fail-closed.** `fillZones` now refuses before mutating: an active routing
session, a missing rules file, or rules that fail to load all raise; the previous
DRC engine is restored on every exit path; connectivity and the incremental-DRC
caches are invalidated after a fill; a filler that reports incomplete or throws is
reported as failure. Engine stamp after this pass: `51442b08`.

**Still open from the review**: `cli_gate` schema/truncation hardening (finding 3),
explicit experimental lifecycle state (finding 4), packaging the refill as a
reproducible patch under `patches/` (finding 5), a real-zone native fill test, the
silk-overlap geometry diagnosis (finding 6), and the long bounded repair/routing
run. These are recorded in the checkpoint rather than reported as done.

## 11. Recovery correction — current status

The phase-5 board acceptance claim is withdrawn. Earlier terminal inventory used
UUIDs without binding them to unique physical pads; the board reuses UUIDs, so the
resulting terminal-pair counts cannot support a comparison. The corrected native
inventory exports a physical identity composed from UUID and pad geometry /
inventory and refuses duplicate identities. The 14-group and 33-pair figures
above are both unverified and must not be reported as current connectivity facts.

The refill changed 64 non-copper silk/mask zones, accounting for 95 added silk
findings. Refill now filters to copper zones so non-copper fill polygons remain
unchanged. The historical 117-finding total and per-class breakdown above were
measured before this correction and do not supersede the 95-silk-finding root
diagnosis.

The CLI gate now validates report envelope, schema, version and types; stable item
identities; unconnected identities; tool and report hashes; effective project and
rules sidecars; subprocess-group cleanup; generation-local report retention; and
remaining run budget. Experimental staging has a separate pointer bound to an
immutable accepted reference, and does not move the accepted pointer or claim
global acceptance. Promotion requires native, complete-CLI and original-reference
terminal evidence.

Validation on 2026-09-26: focused terminal/CLI/artifact-store tests **38 passed**;
the unit group **295 passed**. `check_phase.sh --strict` fails before native
validation because cached router provenance does not match current C++ source
(`RouterProvenanceError`). A build attempt stopped at missing `wx-config`; there
is no current native pass. The installed KiCad 10.0.6 reporter is capped; the
complete KiCad 9.0.8 run predates these C++ changes and is historical only. The
consolidated engine patch applies cleanly to the pinned engine source and produces
byte-identical files. No routing, refill, planner/API calls, commit or push was
performed during this recovery correction.

## 12. Inventory-proven identity and the production final gates (current)

This section supersedes §11's status lines. Engine stamp `e290ee11`
(`ENGINE_VERSION=1.4`, module sha256 `bd8f8b30…`); `check_phase.sh --strict` →
exit 0, **333 unit + 49 native, no skips**.

### 12.1 The identity hole and the rule that closes it

The gate keyed a violation on its UUID pair alone. That is sound only where a
UUID names one item, and this board reuses UUIDs: **121 of 6845 copper UUIDs cover
821 of 7545 tracks/vias/pads (all pads)**, and 250 of 321 footprint UUIDs are
reused. Two different physical violations can therefore be reported under one
UUID pair, and a candidate that replaced pair A with pair B at an equal count
compared as "no change".

`pcb_world/agent/drc_gate.py` builds a `BoardInventory` from the engine's own item
accessors and asks one question of each violation's item UUIDs:

* **exactly one item carries the UUID** → identity is the UUID itself, so every
  existing pair key is unchanged and keeps its movement tolerance;
* **anything else** (a UUID carried by several items, an item the accessors do not
  expose, the nil UUID, an unreadable inventory) is *not proven*, so the key is
  refined by the violation's condition and any change of position, layer or net
  set counts as an addition.

An earlier revision resolved a reused UUID to whichever candidate sat nearest to
the reported point (within 2.0 mm and 0.1 mm clear of the runner-up, thresholds
measured on the frozen source). Root review rejected that: nearest-candidate
geometry is a heuristic, and labelling it "proven" reintroduces the very hole the
rule was meant to close. The thresholds and the distance code are removed;
duplicate identifiers are repaired on a disposable copy of the board instead
(canonical metadata repair), and this gate stays fail-closed for anything it
cannot prove.

The cost is visible: **7972 of 7986 relevant keys have no inventory proof**, so
for 99.8% of them movement is no longer tolerated. `tests/agent/
test_drc_inventory_identity.py` pins the rule, including the reported hole
(a physical pair A→B swap under one UUID pair at equal counts is an addition) and
the two cases that still keep movement tolerance.

### 12.2 Same policy in the saved-artifact gate

`tools/reliability/verify_saved_artifact.py` no longer has a key-set fallback. The
baseline travels as the complete violation multiset — every key *and* condition,
the inventory ambiguity flags, the rules path, and the engine/board provenance —
and the child replays it through `violations_from_evidence` and `diff_sets`, the
same code path the local transactions use. A request without that evidence is
refused with an explicit recapture message. Terminal relations come from a second
mode that opens the frozen reference *and* the candidate in one fresh process.

**The baseline proves which bytes and build it came from.** Replaying the
violations alone would let a substituted, more permissive baseline pass under a
verdict that names the reference's hashes, so the staged value is a full envelope
(`pcb_world/agent/reference_baseline.py`): the multiset, the identity policy, the
reference board/project/rules hashes, and the engine (`cpp_hash`, module sha256,
version, provenance-checked). `verify_generation` re-measures all of it in the
child before replaying and refuses with named problems — a different board,
project, rules, policy, or engine build. `envelope_problems` is the single rule,
used by the runner's in-process capture, the child's `capture_baseline` mode and
the verifier. The adapters additionally re-hash every input after each child
returns (drift is refused rather than recorded) and cap each child at
`min(per-child ceiling, remaining run budget)`. Tests: `tests/agent/
test_reference_baseline.py` plus the native wrong-reference / stale-engine /
real-capture cases in `tests/agent/test_native_final_gates.py`.

### 12.3 The three production gates, measured on both private candidates

`pcb_world/agent/final_gates.py` builds the adapters and
`tools/reliability/run_final_gates.py` runs them for one staging generation.
Everything below was measured against the frozen original reference
(`133edc28…`) with all six candidate/reference hashes bound by each gate.

| Gate | six-closure `255a5cb9…` | copper refill `9017be2e…` |
|---|---|---|
| native (fresh child, baseline replay) | refuse — 179 added relevant identity groups / 206 occurrences at an unchanged 7930 relevant findings | refuse — 19 added groups / 77 occurrences, including `Isolated copper fill` islands |
| terminal (fresh two-board partition) | **pass** — 335→329 clusters, 1060 terminals both sides, 0 splits, 0 vanished nets or terminals | refuse — 33 split relations, 343 clusters for 1060 terminals, 0 vanished |
| complete CLI 9.0.8, 2 runs/board | refuse — equal-count identity churn in relevant classes: clearance 9, silk_overlap 60, silk_over_copper 10, courtyards_overlap 6, starved_thermal 2, lib_footprint_issues 2 | unverified — the unconnected pairing churned and the terminal proof is not ok, so the exemption does not apply; class deltas: clearance 7214→0, isolated_copper 1→4, unconnected 113→126 (raw 149→157) |

### 12.4 Why each native refusal happens (measured)

`phase5_identity_cost.py` replays both comparisons offline and splits the added
identities by whether their pair key already existed in the baseline (a moved
condition) or not (a genuinely new violation pair). Evidence:
`phase5_final_gates/{sixclosure,copper_refill}_identity_cost.json`.

| | added groups | under an existing pair key | new pair keys | previous key-only policy |
|---|---|---|---|---|
| six-closure | 179 (all `Clearance violation`) | **179** | 0 | 0 added groups — **acceptable** |
| copper refill | 19 (all `Isolated copper fill`) | 0 | **19** | 19 added groups — refused |

So the six-closure refusal is entirely the new identity rule: the same pair of
boards was acceptable under the previous policy and now reports 179 moved
clearance conditions. The refill's refusal is unchanged by the tightening — its 19
added groups are new `Isolated copper fill` pair keys under either policy (the
earlier "four isolated-copper additions" measured the refill against the
six-closure state it was built from, not against the frozen reference the gate is
bound to).

The CLI churn is the reporter's
representative-pairing choice — the candidate file lists its footprints in a
different order than the frozen reference, and with 250 reused footprint UUIDs
that changes which representative item 60 `silk_overlap` findings name, at
identical counts and totals.

### 12.5 Engine patch set is now complete and provable

`patches/engine/0002-phase5-integrity-and-copper-fill.patch` was regenerated
against the `0001`-applied tree so it carries **all six** changed engine files:
the four `kicad-patches/rl/` sources, the SWIG 4.5.1 compatibility change in
`kicad-patches/kicad/pcbnew/CMakeLists.txt`, and `engine_server/wire.py`'s
`PadInfo.uuid`/`physical_id`. `tools/reliability/check_engine_patches.py` reads
each file from the pinned commit `7a31e0c`, applies `0001` then `0002`, and
reports every result byte-equal to this checkout's engine tree plus identical
wire copies. `patches/engine/README.md` documents the order, the base and what
each file is for.

### 12.6 Open decisions (root)

1. Extend the native inventory to zones and footprint graphics (native binding
   change + fixture) so the ~50% of findings that reference them can be proven,
   or accept that only mutations leaving every unproven condition bit-identical
   can be committed.
2. Require generations to preserve item order, or compare the CLI against a
   byte-order-normalized reference, given that relevant-class identities churn
   with footprint order.
3. The copper refill stays an experimental staging state: 33 split relations and
   new isolated copper, and nothing here makes it promotable.
