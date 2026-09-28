# Engine patches (reviewable, not applied to the shared checkout)

The routing engine is a separate GPLv3 program, pinned here as the `engine/`
submodule. Its working tree is not tracked by this repository, so every change
this workspace needs from it is kept as a patch in this directory and applied to
this workspace's engine checkout and build copy only.

| | |
|---|---|
| Engine base | `7a31e0c982a84fcda75be3ce69966027135bd7c1` — *PCBWorld Engine v1.0.1* |
| Patch order | `0001-routing-rule-context.patch`, `0002-phase5-integrity-and-copper-fill.patch`, `0003-zone-point-query.patch`, `0004-reporter-cache-canonical-order.patch`, then `0005-netless-first-pair-release.patch` |
| Build copy | `build_rl/kicad_src`, refreshed from `kicad-patches/` by `engine/build_rl_router.sh` (45 `cp -p` lines) |
| Verification | `python tools/reliability/check_engine_patches.py` |

Apply in order, then rebuild:

```bash
git -C engine apply ../patches/engine/0001-routing-rule-context.patch
git -C engine apply ../patches/engine/0002-phase5-integrity-and-copper-fill.patch
git -C engine apply ../patches/engine/0003-zone-point-query.patch
git -C engine apply ../patches/engine/0004-reporter-cache-canonical-order.patch
git -C engine apply ../patches/engine/0005-netless-first-pair-release.patch
PATH="$PWD/.local-bin:$PATH" bash engine/build_rl_router.sh   # or: ninja -C build_rl kicad_rl_router
```

`ninja` alone is enough when `build_rl/kicad_src` is already synced; the build
script is what re-copies `kicad-patches/` into that tree. Either way the
`ENGINE_CPP_HASH` stamp next to `kicad_rl_router.so` must be refreshed, because
the runtime provenance guard refuses a router built from other C++.

`tools/reliability/check_engine_patches.py` is the completeness proof: it reads
every patched file from the **pinned commit** (`git show <pin>:<path>`), applies
the whole series to that pristine copy in order, compares each result byte for
byte with this checkout's engine working tree — the tree the router was built
from — and finally compares the engine-side `engine_server/wire.py` with the
environment's `pcb_world/engine/wire.py`.

## `0001-routing-rule-context.patch`

Three files, all inside `kicad-patches/rl/`:

| File | Change |
|---|---|
| `pns_rl_router.cpp` | The routing-time `DRC_ENGINE` is created with the project's own `<board>.kicad_dru` (resolved exactly as pcbnew's `PCB_BASE_EDIT_FRAME::GetDesignRulesPath()` does) instead of `wxFileName()` (default netclass rules). A requested-but-absent rule file is recorded instead of silently degrading to default rules. |
| `pns_rl_router.h` | Member fields + accessors: `getRoutingRulesPath()`, `wasRoutingRulesLoadedFromFile()`, `getLastDrcRulesLoadError()`. |
| `pns_rl_bindings.cpp` | Python bindings for those three accessors. |

## `0002-phase5-integrity-and-copper-fill.patch`

Six files, the phase-5 integrity and copper-fill work. It is cut against the
`0001`-applied tree, so the two patches are one ordered pair — apply `0001`
first, and never re-cut `0001` without regenerating `0002`.

| File | Change |
|---|---|
| `kicad-patches/rl/CMakeLists.txt` | `pcbnew/zone_filler.cpp` is compiled into the module. `fill_zones` always passes a null parent, so KiCad's interactive stale-fill prompt is unreachable; the module still resolves wx GUI symbols through its existing linkage and no GUI stub is claimed. |
| `kicad-patches/rl/pns_rl_router.h` | Declarations for `fillZones`, the DRC item-signature bookkeeping, and the pad identity fields. |
| `kicad-patches/rl/pns_rl_router.cpp` | `fillZones` (copper zones only — silk and mask fill data is preserved exactly), the `RulesValid` refusal, the cache/world resync after a fill, the quarantine path for unreadable state, the pin-ordered cleanup hardening, `RLPadInfo::uuid`/`physical_id` (the pad's KIID plus its copper geometry, which is what makes a **reused** pad UUID resolvable to one physical pad), and `getBoardItems()` — the complete identity-bearing item inventory (tracks, vias, pads, zones, drawings, groups, every footprint with its graphical items, fields, zones and groups), each row carrying UUID, kind, source container, parent, layer, net and a save-stable physical identity string. |
| `kicad-patches/rl/pns_rl_bindings.cpp` | Binds `fill_zones`, the routing-rule accessors, `PadInfo.uuid`/`PadInfo.physical_id`, and `BoardItemInfo` / `get_board_items()`. |
| `kicad-patches/kicad/pcbnew/CMakeLists.txt` | SWIG 4.5.1 build compatibility for the generated wrapper, scoped to that one source file: the Py2-era shims SWIG used to emit (`PyInt_FromLong`, `PyString_Check`) are mapped to their Python 3 counterparts. SWIG ≤ 4.3 emitted those shims itself; Homebrew's 4.5.1 does not, so KiCad 9.0.8's `common/swig/wx.i` otherwise fails to compile. |
| `engine_server/wire.py` | `PadInfo` gains `uuid` and `physical_id`, and the new `BoardItemInfo` mirror is registered in `_WIRE_TYPES`, in declaration order. This is the **protocol schema**: it must stay byte-identical to the environment's `pcb_world/engine/wire.py` (`tools/check_separation.py` check 4, and the final line of `check_engine_patches.py`). |

### Parts of the engine tree

* `kicad-patches/` is the engine's own patch tree — the files the build copies
  into `build_rl/kicad_src` (or `build_rl/kicad`, for the app variants).
* `engine_server/wire.py` is imported directly by the engine server process; it
  is not copied anywhere.
* `kicad-python/` is the pinned upstream KiCad checkout. It is **not** vendored
  here (this working tree has no `engine/kicad-python`), so
  `engine/build_rl_router.sh` cannot rsync a pristine source from it. The already
  synced `build_rl/kicad_src` is kept and the script's patch-copy list is
  replayed over it; that list must name every `kicad-patches/` file a patch
  touches, or the build tree and the patch set disagree.

## `0003-zone-point-query.patch`

Four files, the phase-11 read-only zone query and its protocol mirror. Cut
against the `0002`-applied tree, so the three patches are one ordered sequence:
apply `0001`, then `0002`, then `0003`, and never re-cut an earlier one without
regenerating the later ones.

| File | Change |
|---|---|
| `kicad-patches/rl/pns_rl_router.h` | `RLZonePointHit` / `RLZonePointResult` and the `getZonePointHits()` declaration, with the semantics written down at the declaration: every covering zone is returned (never one net hiding another), `distance_mm` is the distance to that layer's *filled* copper, and `fill_provenance` is `loaded_unverified` / `no_fill` / `unknown` rather than a currency claim. |
| `kicad-patches/rl/pns_rl_router.cpp` | The accessor. Read-only: no mutation, no checkpoint, no world resync, callable while a session is active. Per query and copper layer it walks the board's zones (plus footprint-scoped ones), tests the point against the zone outline and against `ZONE::GetFilledPolysList(layer)`, reports islands via `ZONE::IsIsland`, and reports rule-area keepouts through the same `HasKeepoutParametersSet()` gate `syncZone` uses. The predicate is `IsCopperLayer`, not a `F_Cu..B_Cu` range — this enum orders the stack `F_Cu=0, B_Cu=2, In1_Cu=4 (+2)`, so a range check silently refuses every inner layer. Distances come from `SHAPE_POLY_SET::SquaredDistance`, which returns 0 inside the fill (holes excluded) and otherwise the distance to the nearest fill edge or void wall; an empty fill poly set is reported as `no_fill` rather than as KiCad's "no segments" sentinel. |
| `kicad-patches/rl/pns_rl_bindings.cpp` | Binds `ZonePointHit` / `ZonePointResult` and `get_zone_point_hits(queries)`, where `queries` is a list of `(x_mm, y_mm, layer, window_mm)` rows. Primitives only, so the engine server needs no conversion; the window is what keeps a response bounded to the geometry the caller is actually testing. |
| `engine_server/wire.py` | The protocol mirror: `ZonePointHit` / `ZonePointResult` in declaration order, registered in `_WIRE_TYPES`/`KRL_FIELDS`. Must stay byte-identical to the environment's `pcb_world/engine/wire.py` (`tools/check_separation.py` check 4 and the final line of `check_engine_patches.py`). |

Advisory only. The query answers what the *loaded* zone geometry holds at a
point; it runs no DRC and replaces no gate. `pcb_world/agent/zone_coverage.py`
turns it into a candidate *ranking* signal (unknown passes through untouched, and
the ranker itself drops nothing from the list it is given), and the transactional
native DRC remains the only authority on whether copper may be kept. Ranking is
not immunity: the sweep truncates to its own candidate limit *after* the rank,
so a plan moved to the back can fall outside that window.

## `0004-reporter-cache-canonical-order.patch`

Three files, the T28 reporter repair. It is cut against the `0003`-applied tree,
so `0001`..`0004` are one ordered sequence.

| File | Change |
|---|---|
| `kicad-patches/rl/drc_test_provider_rl_copper_clearance.cpp` | the pad-clearance visitor canonicalises the `(BOARD_ITEM*, BOARD_ITEM*)` pair with the same pointer swap its R-tree filter applies before reading or writing the run's `checkedPairs` cache. Without it the visitor wrote `has_error` to an entry the filter never read back, so a pair was reported once per copper layer it shared. |
| `kicad-patches/kicad/pcbnew/drc/drc_test_provider_copper_clearance.cpp` | **new overlay copy** of the stock KiCad 9.0.8 provider carrying the same visitor fix. This is the provider `kicad-cli` loads from `_pcbnew.kiface`; without the overlay the two front ends disagree on the multiplicity of the same finding. |
| `build_rl_router.sh` | one `cp -p` added to the documented patch-copy list, so the overlay reaches `build_rl/kicad_src`. |

## `0005-netless-first-pair-release.patch`

Two files, the T29 reporter repair. Cut against the `0004`-applied tree, so the
five patches are one ordered sequence.

| File | Change |
|---|---|
| `kicad-patches/rl/drc_test_provider_rl_copper_clearance.cpp` | the pad-clearance visitor releases the `checkedPairs` layer claim when a same-logical-pad pair is visited from its **netless** member and the partner is netted. |
| `kicad-patches/kicad/pcbnew/drc/drc_test_provider_copper_clearance.cpp` | the same release in the stock overlay, so the engine and the pinned CLI stay in step. |

The `SameLogicalPadAs` branch returns before reporting when the pad it visits
carries `GetNetCode() == 0`, but the R-tree filter has already claimed that layer.
The netted member's later visit was therefore filtered out and a mixed
netless/netted pair was reported by neither member on any layer, so the same
copper reported a different pair set depending only on the pad declaration order
in the file. Releasing the claim lets the netted member decide the pair: it files
the single row, and its `has_error` then suppresses the remaining layers exactly
as before. The release is deliberately narrow - visited pad netless, partner
netted, same logical pad - so an exempt pair (equal nets, both netless, or both
`unconnected-(...)` names) keeps its single claim and a filed pair keeps its
de-duplication. No rule, waiver, severity, clearance, proximity or exemption
changed; `tests/engine/test_reporter_cache_canonical_order.py` pins the exact
pair identities in both declaration orders on both front ends.

## Measured status

* **Rule loading — verified.** With the patch, a board with a sibling `.kicad_dru`
  reports that file from `get_routing_rules_path()` and
  `was_routing_rules_loaded_from_file() == True`; without the patch the accessors
  do not exist at all.
* **Rule validation — verified.** The same rule file changes the backend's own
  DRC verdict on identical copper (a 1.0 mm clearance rule reports a violation
  where the default rules report none).
* **Routing geometry — NOT verified.** The router still places copper at
  ~0.25 mm clearance through a corridor the loaded 1.0 mm rule forbids. Because
  that is a stronger claim than "the file was loaded", `pcb_world.agent.rules`
  never claims enforcement; the session instead refuses to keep any mutation that
  adds a violation under the loaded context (`pcb_world/agent/drc_gate.py`), and a
  rule file that cannot be loaded refuses mutation outright. The C++ comments in
  the patch say the same thing at the source. See
  `tests/agent/test_native_rules.py` and
  `docs/agent-work/reliability/phase5-kicad/RESULT.md` §5.
* **Copper-only fill — verified experimentally, still not promotable.** The
  copper-only filter preserves the 72 non-copper (silk/mask) zones byte for byte
  and removes the 95 previous silk/mask artwork findings, but it still leaves split
  terminal relations, so a refilled board stays an experimental staging candidate
  (`docs/agent-work/reliability/phase5-kicad/RESULT.md`).
* **Zone point query — verified natively.** `tests/agent/test_zone_point_query.py`
  pins overlapping pours (both nets reported, `fill_multi_net` when they
  conflict), a void inside a pour (outline-only with a measured distance), a zone
  spanning three copper layers, rule-area keepout flags, the island flag, a
  board with no zones, an unfilled pour (unknown, never "safe"), a non-copper
  layer (unknown, never "no zone"), and the prefilter's cache and refill-epoch
  lifecycle. The engine query's own geometry is
  `SHAPE_POLY_SET::SquaredDistance`, the same primitive KiCad's DRC uses to
  measure clearances.
