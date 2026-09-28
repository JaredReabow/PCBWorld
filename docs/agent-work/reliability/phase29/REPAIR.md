# Phase 29 (T29A) - the netless-first same-logical-pad suppression, repaired

Status: **implementation, regression and frozen differential complete, awaiting
independent verification and Astra acceptance.** Nothing was routed, refilled or
promoted; the accepted generation and the accepted pointer are unchanged. This
page carries aggregates only - private evidence (board references, nets,
coordinates, item identifiers and the exact pair list) lives in
`phase29_reporter_repair/` in the board workspace.

Owner: task T29A. The behaviour this repairs was diagnosed read-only in T29N and
published in `NETLESS_ORDER.md`; this page is its implementation.

## 1. What was wrong

The copper-clearance provider decides a same-logical-pad pair (two pads of one
footprint sharing a pad number) from its ``SameLogicalPadAs`` branch, and the
branch's first two exemptions are:

```cpp
if( pad->GetNetCode() == 0 || pad->GetNetCode() == otherPad->GetNetCode() )
    return true;                  // no finding, and no signal to the caller
```

The pad loop is `for pad { for layer }` with an R-tree filter that records the
layer it is about to test in the run's ``checkedPairs`` cache and only invokes
the visitor when the layer was not already claimed. A visit that returns `true`
has still claimed the layer. So when the **netless** member of a mixed pair is
the one reached first, `GetNetCode() == 0` returns early without reporting,
leaves the layer claimed, and the netted member's later visit on that layer - on
every layer - is filtered out. The pair is reported by neither member.

The consequence is order dependence on identical copper: the same pairs and the
same geometry report a different shorting class depending only on where a
footprint's pad blocks sit in the file. T29N measured it on the frozen V3
generations: 30 rows as saved, 34 after a text-level reversal of four families'
pad blocks, and a KiCad save normalises the order back.

## 2. What changed

The release is in the **visitor**, not the branch, and it is deliberately narrow.

| path | change |
|---|---|
| `engine/kicad-patches/rl/drc_test_provider_rl_copper_clearance.cpp` | when a pad-pad visit produces **no finding**, the visited pad is netless, its partner is netted and the two are the same logical pad, the visitor releases the layer bit the filter claimed. |
| `engine/kicad-patches/kicad/pcbnew/drc/drc_test_provider_copper_clearance.cpp` | the same release in the stock overlay the pinned CLI loads, so the two front ends stay in step. |
| `patches/engine/0005-netless-first-pair-release.patch` | the sequential engine patch for both files, cut against the `0004`-applied tree. |
| `patches/engine/README.md` | the patch order row, the apply list and a section for `0004`/`0005`. |
| `tests/engine/reporter_fixture.py` | the fixture extended to the case list in section 4. |
| `tests/engine/test_reporter_cache_canonical_order.py` | the regression, extended to the ordering contract on both front ends and the save/reload round trip. |
| `tests/engine/repro_netless_first_pads.py` | the standalone diagnostic; it now makes a verdict instead of recording an open question. |

Releasing the claim lets the **netted** member decide the pair: it files the
single row, and its `has_error` then suppresses the remaining layers exactly as
before T29. Nothing else moved:

* the condition is `visited pad netless **and** partner netted **and**
  `SameLogicalPadAs` **and** no finding`. An exempt pair - equal net codes
  (which covers the both-netless case) or two `unconnected-(...)` short names -
  never matches, so it keeps its single claim and stays silent;
* a pair that *is* reported keeps its `has_error` de-duplication, so the T28
  repair's "once, not once per layer" property is preserved;
* the T28 canonical pointer ordering in both the filter and the visitor, the
  mutex scope, `has_error`, cancellation, the R-tree reach bound, the severity
  handling, the KiCad exemptions and the RL incremental-clearance scope are
  untouched. No rule, waiver, severity, clearance or proximity value changed.

## 3. Provenance and rebuild

The repair is proven by the two binaries, not by the source edit.

| | pre-repair | repaired |
|---|---|---|
| RL provider source | `dac3d9cef4b2eb192733d0a89a808afd1058db9c810e273810dc2a15099cfc89` | `19686ca1ffa9fae5769dfa6a2f94dd35160853884443cf69a9ab2afb84ac19c8` |
| stock provider source | `2e7657ba74615c7a7875d963d95787eaa441b1aafa0d2ce64b35ccfdf4d44191` | `319ff6793aec4d1d826b1bdbee882e014ee14e23beb782911dac8eea3d8d37cb` |
| `kicad_rl_router.so` | `a922eb8134d2f418f62410ff7025a508e6111cced9035cc921b5673c897e6278` | `819a26f1ebe5affe78a3974d5b42f9aab05ff03d090696ddbcfd524f90f22179` |
| `_pcbnew.kiface` | `dfe466906eff9bc87685d87cb34ac997cfb3ca8cf31a666b717b90f2bfc803dc` | `c23f8cb9023e9e8d8d0cf6e0b52e267ebf96e6a3f0156bc8076d50e48fb2221e` |
| `ENGINE_CPP_HASH` stamp | `2f9e6153` | `b47d4f0c` |

Both providers were rebuilt from the pinned engine commit
`7a31e0c982a84fcda75be3ce69966027135bd7c1` with the documented incremental
`ninja kicad_rl_router pcbnew_kiface`, and the module was re-stamped exactly as
`engine/build_rl_router.sh` stamps it (`ENGINE_CPP_HASH` = the tree content hash,
`ENGINE_VERSION` = `1.4`). The `kicad-cli` binary itself does not embed a
provider: it hashes `56dd7910af7d190c...` before and after and loads the rebuilt
kiface. `tools/reliability/check_engine_patches.py` reports that the five-patch
series applies cleanly to the pin and reproduces this checkout's engine tree
byte for byte (9 files, wire copies identical).

## 4. Synthetic coverage

`tests/engine/reporter_fixture.py` writes one deterministic four-copper-layer
board from nothing but that file (uuid5 of fixed tokens, so two writes are
byte-identical), and `tests/engine/test_reporter_cache_canonical_order.py` pins
the **exact item-pair identities** - not row counts - on both the RL module and
the CLI-loaded provider, over fresh processes. The fixture's cases:

| case | shape | expectation |
|---|---|---|
| `FM4`, `FR4` | netted through-hole pad with 4 and 3 netless partners inside its outline; the board-wide flip reorders their pads | 4 + 3 pairs, each once, in **both** orders |
| `GS1` | netless surface-mount partner inside a netted surface-mount pad, `F.Cu` only, netless first | 1 pair - the single-copper-layer case |
| `MX1` | two netted pads on different nets and two netless pads in one footprint, interleaved netless-first | 5 pairs; the netless/netless pair stays silent |
| `EQ1` | two pads on the **same** net | silent |
| `AL1` | a footprint whose pads are all netless | silent |
| `DN1` | two netted pads, different real nets, no `unconnected-` name | 1 pair |
| `UC1` | two pads, **both** with an `unconnected-(...)` short name | silent - the two-sided exemption |
| `GEN*` | different footprints, different nets, overlapping | 2 ordinary collisions |
| `CL1`/`CL2` | different footprints, one net, overlapping | silent |
| `NEAR*` | different footprints, different nets, 0.10 mm apart (in reach) | never a shorting row |
| `FAR*` | different footprints, different nets, far out of reach | never a shorting row |

Expected: **14 same-logical-pad pairs + 2 ordinary collisions = 16**, each with
multiplicity exactly 1, and none of the 18 exempt/control pairs present. Both
declaration orders and both front ends report exactly that set (engine, 3 fresh
server processes; CLI, 2 fresh invocations, per order). A separate case registers
the fixture's own coverage, so a family deleted from the tables fails the
regression rather than quietly shrinking it.

`test_save_and_reopen_normalises_pad_order_and_keeps_the_report` exercises the
round trip: a hand-written netless-first board is saved through the harness's own
writer, the writer is observed to re-order the flip families' pads (asserted from
the deterministic pad UUIDs' file offsets, not from the writer's whitespace), and
the pre-save and post-save boards report the identical exact pair set.

## 5. Frozen differential

The accepted V3 generation was staged into a disposable copy and measured with
the repaired providers, then compared against T29N's pre-repair capture of the
same bytes (recorded under engine source hash `2f9e6153` and kiface
`dfe46690...`). The board's own bytes were untouched, and the staged copy was
re-hashed after every front-end pass.

| | pre-repair | repaired |
|---|---:|---:|
| native shorting rows / pairs | 30 | 34 |
| pinned-CLI shorting rows / pairs | 30 | 34 |
| pairs added | - | 4 |
| pairs lost | - | 0 |

The four added pairs are exactly the four netted/netless same-logical-pad
overlaps the diagnosis identified as declared netless-first, attributed from the
board **text** rather than from the report. Native and CLI report the same 34
pairs. Every other class keeps its row count on both front ends (native clearance
7488, hole clearance 149, hole size 230, unconnected 135, dangling tracks 65,
holes too close 32, isolated copper 1; all 18 non-shorting CLI classes
identical), and every other class's identity digest is identical too - with one
measured exception: the ratsnest class (`1:Missing connection between items`)
produces a **different digest on identical bytes and identical binaries** in
every fresh process (three repeats, three digests), so it is compared by count
only. That instability is pre-existing and independent of this change; it is
recorded rather than smoothed over.

This movement is **reported debt that was already there**, not new copper: the
added pairs are pre-existing overlaps the reporter dropped on these bytes, and
the accepted board's shorting class now reads the same 34 rows the identical
geometry reads out of order.

## 6. Combined harness

`bash tools/reliability/check_phase.sh --strict` - the acceptance mode, which
requires the native group to execute with no skips and refuses a load or
provenance failure - reports:

```
[unit]   exit 0: 499 passed
[native] exit 0: 146 passed
[patches] exit 0: OK: the engine patches apply cleanly to the pin and reproduce this tree
```

The reporter regression asserts the provenance waiver is unset and that the
module stamp equals the tree's content hash, so a pre-repair router cannot pass
it.

## 7. Limits

* The frozen differential's "before" side is T29N's pre-repair capture, not a
  pre-repair binary rebuilt inside this task. It is the same board bytes, and the
  recorded engine/kiface digests differ from the repaired ones as expected; the
  comparison is a same-bytes A/B across a known binary change, not a single-binary
  re-measurement.
* The differential is one accepted generation plus the frozen lineage T29N
  already scanned. It does not re-scan the experimental candidate or the
  baseline, and it does not rerun the gate end to end.
* The ratsnest class digest is measurably process-unstable on these bytes with
  fixed binaries; only its count is compared, and no claim is made that a stable
  digest for it exists.
* Nothing here decides what a netless pad physically inside a netted pad **means**
  electrically. The repair makes the overlap visible; whether the intended copper
  relationship is what the schematic intends is a board decision, not a reporter
  decision, and the repaired rows are not evidence that the netless pad is a
  short.
* The save/reload normalisation is pinned on the synthetic fixture. The diagnosis
  inferred KiCad's sort key from real families rather than reading the writer
  source, so a writer change would be caught by the fixture but the exact key is
  still not claimed.
