# Phase 28 (T28A) - the shorting reporter, repaired at the source

Status: **implementation, regression and self-verification complete, awaiting
T28B independent verification and Astra acceptance.** No board was routed,
refilled or promoted; the accepted generation and the accepted pointer are
unchanged. The `phase27_verifier` tree was not touched by this work: the
rebaseline and the six-step replay are gated on T27A acceptance.

Owner: task T28A. Private evidence lives in `phase28_reporter_repair/` under the
private V3 workspace; this document carries aggregates only.

## 1. What was actually wrong

Both copper-clearance providers canonicalise a `(BOARD_ITEM*, BOARD_ITEM*)` pair
**by pointer address** before touching their per-run `checkedPairs` cache, because
the cache key's equality and hash are order-sensitive. The *filter* did it; the
*visitor* that records "this pair has already produced a finding" did not, in
exactly one place per provider:

```
if( static_cast<void*>( a ) > static_cast<void*>( b ) ) std::swap( a, b );
```

That is the whole defect. When the visited item's address is above its partner's,
the filter stores the entry as `(partner, item)` while the visitor looks up
`(item, partner)`, the lookup misses, `has_error` is never set on the entry the
filter reads back, and the pair is reported again on every copper layer it
shares. The same bytes then report a pair once or once per layer depending on
process address order, which is what made the shorting class non-reproducible at
row level. The other three filter/visitor pairs in each file are already
consistent (the track visitor canonicalises, the copper-graphic visitor never
writes the cache), so the change is one swap in each of two files.

## 2. What changed

| path | change |
|---|---|
| `engine/kicad-patches/rl/drc_test_provider_rl_copper_clearance.cpp` | the pad-clearance visitor's canonical swap (RL module) |
| `engine/kicad-patches/kicad/pcbnew/drc/drc_test_provider_copper_clearance.cpp` | **new overlay copy** of the stock provider with the same swap (the provider `kicad-cli` loads from `_pcbnew.kiface`) |
| `engine/build_rl_router.sh` | one `cp -p` added to the documented patch-copy list, so the overlay reaches the build source tree |
| `patches/engine/0004-reporter-cache-canonical-order.patch` | the sequential engine patch for all three files; sha256 `c8a319719928161f0d4bd1646ba23677099aaf9c9a54f1124e57c3d68ae34a63` |
| `tools/reliability/check_engine_patches.py` | accepts a patch that *adds* a file the pin does not carry (an overlay copy of a stock KiCad source); everything else is unchanged |
| `tests/engine/reporter_fixture.py` | the synthetic multilayer fixture (self-contained, deterministic) |
| `tests/engine/test_reporter_cache_canonical_order.py` | the regression: 6 tests over both providers |
| `tests/engine/test_phase_gate_groups.py` | 3 tests pinning the harness's new group wiring |
| `tests/engine/repro_netless_first_pads.py` | the reproducer for the open ordering question in section 4 (not a test, must not gate) |
| `tools/reliability/check_phase.py` | adds the required `patches` group, wires the regression into the native group, raises the native minimum to 144 |

Mutex scope, layer tracking, `has_error`, cancellation, the genuine-short and
clearance branches, and severity handling are untouched. No pair-set comparator,
no class waiver and no rule change was added.

## 3. Provenance and rebuild

The repair is proven by the two binaries, not by the source edit.

| | pre-repair | repaired |
|---|---|---|
| RL provider source | `0f9a952b855dec5a40af5ed52a5cfc01446a2bf83444a9612389ee1e5459f58b` | `dac3d9cef4b2eb192733d0a89a808afd1058db9c810e273810dc2a15099cfc89` |
| stock provider source | `771316ab56db203774bc7c43f56f1408748bbaef9dc3f2908205ac54bf73b827` (compiler input; no overlay existed) | `2e7657ba74615c7a7875d963d95787eaa441b1aafa0d2ce64b35ccfdf4d44191` |
| `kicad_rl_router.so` | `11e3d40599c56614374bd8accbe5dde433a2d6d6b9811015ddd18bff0e0dcd7d` | `a922eb8134d2f418f62410ff7025a508e6111cced9035cc921b5673c897e6278` |
| `_pcbnew.kiface` | `79ac6f6fcf398fb3a43343e6285d9df82dba522422675d212f27aba006e74db0` | `dfe466906eff9bc87685d87cb34ac997cfb3ca8cf31a666b717b90f2bfc803dc` |
| `ENGINE_CPP_HASH` stamp | `ef2bd46f` (the pristine pre-repair build) | `2f9e6153` |

Both binaries were rebuilt from the pinned engine commit
`7a31e0c982a84fcda75be3ce69966027135bd7c1` with the documented incremental
`ninja kicad_rl_router pcbnew_kiface`, and the module was re-stamped exactly as
`engine/build_rl_router.sh` stamps it. The `kicad-cli` binary itself does not
embed a provider: it hashes identically before and after, and it loads the
rebuilt kiface.

For the pre/post A/B diagnostic in sections 4 and 5, the pre-repair module was
also rebuilt from the pre-repair sources, and it is stamped
`pre-repair-experiment` - deliberately *not* a hash of this tree - so the runtime
provenance guard refuses it. That refusal was reproduced, and those measurements
ran only under the guard's own documented waiver
`PCBWORLD_ENGINE_ALLOW_MISMATCH=1` ("deliberately running an old router"). Every
sample records the compiled provider and binary digests, and **no retention
evidence may be produced under the waiver**: the required public regression
(section 4) asserts the waiver is unset and that the stamp equals the tree hash,
so a pre-repair binary cannot pass it.

## 4. Deterministic fixture, regression, and one open question

`tests/engine/reporter_fixture.py` writes a synthetic four-copper-layer board
from nothing but that file: four same-logical-pad families (a netted pad with 4,
3, 2 and 6 netless partners inside its outline, all through-hole and sharing one
pad number) plus two ordinary different-net collisions between different
footprints and one same-net overlapping pair that must stay silent. Expected:
15 + 2 = 17 shorting pairs.

`tests/engine/test_reporter_cache_canonical_order.py` asserts, on the repaired
generation and with no skips:

* four copper layers, and byte-identical output for two writes of the fixture;
* the engine (RL module), over 3 fresh engine-server processes, reports exactly
  the fixture's **expected item-pair identities** - all 15 same-logical-pad pairs
  and **both** ordinary collisions, computed from the fixture's deterministic
  UUIDs rather than merely counted, so a substituted pair at the same count
  fails - each with multiplicity exactly 1, with the same-net control pair
  explicitly absent;
* the pinned CLI reports that same exact pair set over 2 fresh invocations;
* the provenance guard is enforced (waiver unset; stamp equals the tree hash);
* the patch set reproduces the engine tree;
* with the families' pads declared in the reverse order - a different, fully
  deterministic visitation of the same pair through the production cache - the
  assertions stay order-independent: both genuine collisions are present exactly
  once, the same-net control is absent, every reported pair is one the fixture
  defines, and no pair is reported twice. Whether the same-logical-pad pairs
  appear in this ordering is deliberately *not* asserted, so the regression does
  not freeze the known netless-first suppression (the open question below).

Pre-repair, the same fixture reported rows `{17, 20, 32}` across eight fresh
processes, with per-pair multiplicities `{1, 4}`: three of the eight processes
carried extra rows, and in two of those both multiplicities appear together (12
pairs once and 5 pairs four times in one process). That is the defect. **The
pointer ordering itself is process state and is not set by the board file**: the
claim "both orderings are exercised" is an inference from that measured
multiplicity movement, while what the regression asserts deterministically is the
order-independent invariant (exactly one row per pair, exact pair set) on both
providers.

**Unresolved, and deliberately not fixed here.** A family that declares its
netless partners *before* the netted pad reports nothing at all: the netless visit
takes the provider's `GetNetCode() == 0` early return without reporting, yet still
marks that layer in the pair cache, so the later netted visit is filtered out.
`tests/engine/repro_netless_first_pads.py` reproduces it on the same fixture (17
pairs in the forward ordering, 2 in the reverse - only the ordinary collisions).
Whether that is intended, and whether the cache entry should be written only when
a finding is actually reported, needs its own change and its own evidence; the
regression therefore asserts nothing about the suppressed pairs - only that no
pair is double-reported, that any pair it does report is one the fixture defines,
and that the genuine collisions and the clean control are unaffected.

## 5. Differential on the frozen boards

Three retained phase-26 boards, twelve fresh native engine processes **and** six
fresh pinned-CLI invocations per board, on both the pre-repair and the repaired
binary, every sample kept and no run discarded.

| board | shorting pairs | native rows pre -> post | CLI rows pre -> post |
|---|---|---|---|
| 1 | 30 | 30 -> 30 | 30 -> 30 |
| 2 | 30 | 30 -> 30 | `{30, 33}` -> 30 |
| 3 | 30 | 30 -> 30 | 30 -> 30 |

* The shorting pair set is invariant across all twelve processes of each side and
  **equal between the sides**, on every board: no pair added, no pair lost.
  After the repair every pair is reported exactly once, in every process and
  every CLI invocation.
* Pairs are classified from the engine's own board inventory, not from the
  provider: per board, 6 pairs are same-logical-pad and 24 are ordinary
  collisions, none unresolved. The only pair reported more than once anywhere in
  the differential is board 2's pre-repair CLI pair, and it classifies as
  same-logical-pad: in this sample the defect never reached an ordinary collision.
* No genuine finding is lost, and no DRC class other than the shorting class and
  the connectivity progress classes moves: for every other class the count *and*
  the identity digest are identical on both sides. The shorting class is
  deliberately excluded from that statement - collapsing its per-layer rows is
  the repair. The unconnected-items class, which the gate never lets gate a
  commit, keeps a constant count per board (152 / 151 / 148) while its identity
  pairing churns on both sides, the same shape phase 25/26 recorded.
* The terminal-partition relation is identical on both sides of every board
  (191 nets, 1060 terminals, 334-338 clusters), and the board, project and rule
  hashes are unchanged across the whole run. The raw cluster representatives are
  process-local labels, so the probe digests the canonical partition rather than
  the label.

The differential's pre-repair *native* side landed on the one-row side in all 36
processes; the defect is still reproduced on those exact bytes by the CLI side
(board 2) and by the fixture on both front ends. The pre-repair A/B is diagnostic
only: it was produced under the guard waiver with the compiled digests disclosed,
it backs no retention decision, and under the waiver nothing may be retained.

## 6. Verification

| check | result |
|---|---|
| `bash tools/reliability/check_phase.sh --strict` | exit 0: unit 499 passed; native 144 passed; patches group exit 0 ("the engine patches apply cleanly to the pin and reproduce this tree") |
| required coverage | the regression is in the native group and the patch check is a required group; in strict mode a skip, a load failure, a waiver or a non-reproducible patch set is a failure, not a pass |
| `tools/reliability/check_engine_patches.py` | exit 0: patches `0001`-`0004` apply to the pin and reproduce the engine tree byte for byte (9 files, one added by `0004`) |
| targeted runs | `tests/engine/` 9 passed; `tests/agent/test_gate_integrity.py` 10 passed; the earlier engine/DRC set 99 passed |
| pre/post artifact hashes across every rebuild | the repaired tree still hashes to the recorded post state |
| public-document leak scan | clean: no board hash, net name, footprint reference, coordinate or item identifier in this document |

Harness evidence: `docs/agent-work/reliability/evidence/phase_check.json`.

## 7. Limits

The fixture is a test input, not a design: it makes the provider's cache contract
observable, and its clearances, hole spacings and connectivity are not
meaningful. The pointer comparison inside the visitor is process state and cannot
be set from a board file, so the deterministic regression pins the
order-independent invariant rather than the address order itself; the reverse
ordering exists so the fixture is not tuned to one arrangement of the cache.

Nothing here promotes a board, moves the accepted pointer, or claims the V3
design is finished: the accepted generation still carries its original unrouted
edges and the phase-26 experimental candidate still fails its gates. The six
retained phase-26 steps are **not** rebaselined or replayed in this phase; that
replay, and any routing campaign after it, waits on T27A acceptance.
