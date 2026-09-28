# Phase 11 - result

Status: **ready for review.** The approved read-only zone query is implemented,
tested and integrated into the sweep as a ranking signal; the bounded campaign
ran and is reported below. The accepted pointer is unchanged.

Contract: [PLAN.md](PLAN.md). The interface this implements is the one the
research pass returned for approval, with the orchestrator's five amendments.

## What changed

| file | change |
|---|---|
| `patches/engine/0003-zone-point-query.patch` | new patch: the accessor and its protocol mirror |
| `patches/engine/README.md` | patch order + the new patch's description |
| `engine/kicad-patches/rl/pns_rl_router.{h,cpp}` | the read-only point-in-zone accessor |
| `engine/kicad-patches/rl/pns_rl_bindings.cpp` | `ZonePointHit` / `ZonePointResult` + `get_zone_point_hits` |
| `engine/engine_server/wire.py`, `pcb_world/engine/wire.py` | the protocol mirror (byte-identical) |
| `pcb_world/engine/kicad_engine.py` | `get_zone_point_hits()` wrapper; `zone_fill_epoch` |
| `pcb_world/engine/containers.py` | re-export the two new mirrors |
| `pcb_world/agent/zone_coverage.py` | the advisory prefilter: margin policy, classification, cache, corridor sampling |
| `pcb_world/agent/runner.py` | `zone_prefilter*` config (**off by default**); `_zone_ranked`, `_note_zone_evidence`, the call site |
| `tests/agent/test_zone_point_query.py` | 13 native tests over real zone geometry |
| `tests/agent/test_zone_prefilter_unit.py` | 11 engine-free tests for the policy |
| `tests/agent/synthetic_boards.py` | multi-layer zones, rule-area keepouts, island flag |
| `tools/reliability/check_phase.py` | both new files in the strict gate; native floor 116 -> 129 |
| `docs/agent-work/reliability/phase11/{PLAN,RESULT}.md` | contract + this report |
| `HISTORY.md`, `CHANGELOG.md` | append-only phase 11 entries |

No footprint moved, no zone deleted, no clearance relaxed, no gate substituted,
and no existing accessor changed shape. `check_engine_patches.py` proves the pin +
`0001` + `0002` + `0003` reproduce this engine tree byte for byte.

## Correction cycle (Astra review)

One consolidated review cycle found four issues; all four are fixed here.

1. **The verdict cache was net-blind (real defect, fixed).** `ZoneCoverage`
   cached the *classified* verdict under `(point, margin)`, but a verdict carries
   `state` / `nets`, which depend on the net being routed. The second pair to ask
   about the same copper was handed the first pair's own/foreign answer and
   mis-ranked. The cache now holds the engine's **net-independent row** (and
   `_Unresolved` markers for points the engine cannot answer); the verdict is
   derived on every call. Regression test
   `test_one_point_is_own_for_one_net_and_foreign_for_another` asks the same
   point at the same margin for two nets and asserts opposite verdicts across a
   cache hit, then asserts the row count did not move — it fails against the old
   code. Every cache call site and both invalidation paths (`invalidate()`, the
   refill-epoch bump) are covered by
   `test_cache_lifecycle_and_refill_invalidation`.
2. **The prefilter is now opt-in (default changed).** `RunnerConfig.zone_prefilter`
   defaults to **False**: it changes which plans a sweep spends its budget on and
   no control run has yet shown that change to be an improvement, so it must be
   asked for. The campaign driver gained an explicit `--zone-prefilter`, kept
   `--no-zone-prefilter` for a paired control, made them mutually exclusive, and
   routes both through `zone_prefilter_enabled()`. Tests:
   `test_the_prefilter_is_opt_in_and_a_default_run_never_ranks` (public config),
   plus a driver check reported under Verification.
3. **"Never drops a candidate" was overstated (docs and tests corrected).** The
   ranker removes nothing from the list it is handed, but the sweep truncates to
   `candidate_limit` *after* ranking, so a plan moved late can miss the attempt.
   Every place that claimed otherwise now states the two claims separately —
   `zone_coverage.py` (module docstring, `CandidateZoneRisk`, `candidate_risk`),
   `runner.py` (config field and `_zone_ranked`), `patches/engine/README.md`,
   `PLAN.md`, this report, `HISTORY.md` and `CHANGELOG.md`. Test:
   `test_a_ranked_late_candidate_can_still_miss_the_truncated_sweep` pins both
   halves — the full list is retained *and* the truncated sweep excludes the
   late plan.
4. **Public/private scan re-run.** The phase-11 entries in `HISTORY.md`,
   `CHANGELOG.md`, `PLAN.md` and this report were re-scanned for board-specific
   coordinates, net names, layer references and rule values: none. The margin
   basis (the board's own clearance and copper radius), the campaign digest and
   the taxonomy counts stay in the private `phase11_routing` tree; the public
   text reports counts, ratios and the accepted hash only.

## Why the query had to exist

The first pass over this phase established that the pinned engine had no
trustworthy read-only way to ask what zone copper covers a point. Three
independent facts, each sufficient on its own:

1. **Point does not reach the router's own world.** The PNS world is the engine's
   obstacle model, and the KiCad sync gate the engine links admits *only rule-area
   keepouts* into it - `PNS_KICAD_IFACE_BASE::syncZone` returns immediately unless
   the zone is a rule area, and every admitted triangle is added with a null net
   (`build_rl/kicad_src/pcbnew/router/pns_kicad_iface.cpp:1394`). Pour copper is
   therefore not a router obstacle at all - and the observation helper phase 10
   relied on (`pcb_world/agent/observations.py:738`) enumerates exactly the
   tracks, vias and pads lists, so a pour can never appear in a plan's obstacle
   view.
2. **The zone row carries no geometry and no layer span.** `getBoardItems()`
   emits one row per zone built by `boardItemSemantic`
   (`engine/kicad-patches/rl/pns_rl_router.cpp:151`), and for a zone that is its
   outline bounding box plus a position - no outline, no fill, no voids, no
   islands. Its `layer` field is `ZONE::GetLayer()`, which returns
   `UNDEFINED_LAYER` for any zone on more than one copper layer
   (`build_rl/kicad_src/pcbnew/zone.cpp:452`), and the only read-only zone
   consumer in the tree silently skips a row whose layer will not map
   (`pcb_world/agent/observations.py:1099`).
3. **Staleness is not reportable at load, by KiCad's own design.**
   `pcb_io_kicad_sexpr_parser.cpp:7418` sets `SetNeedRefill(false)` unconditionally
   when a board loads, and `zone.h:916` states the semantics plainly: "m_needRefill
   = false does not imply filled areas are up to date". `ZONE::GetHashValue()`
   (`zone.cpp:638`) only returns a hash the current process computed, so there is
   no stored-versus-recomputed comparison to expose.

The complete bound surface was enumerated rather than sampled - 110 bound entries
in `engine/kicad-patches/rl/pns_rl_bindings.cpp` (105 on the router class, 5
module-level). The only zone-related read-only exports are `get_board_items()`
and `get_keepouts()`, and neither is a fill query.

### What each existing interface can actually answer

| interface | exposes | why it cannot serve |
|---|---|---|
| `get_board_items()` | per zone: uuid, source, parent, layer, net, outline **bbox** | bbox is not copper; no fill, no voids, no islands, no layer span |
| `get_keepouts()` | rule-area zones, first contour only, per copper layer | keepouts only; no net; holes not exported; pour copper never appears |
| `get_connected_points()` / `world.HitTest` | router-world items at a point | the world holds no pour copper (see verdict 1) |
| `get_tracks()`/`get_vias()`/`get_pads()` | explicit copper items | zones are not items in these lists |
| `run_drc` / `run_drc_incremental` | authoritative whole-board verdicts | the post-hoc gate itself, not a bounded per-point prefilter |

## What was implemented

### The engine accessor

`get_zone_point_hits(queries)` takes `(x_mm, y_mm, layer, window_mm)` rows and
returns one `ZonePointResult` per query, in query order. It is read-only: no
mutation, no checkpoint, no world resync, no quarantine path, callable while a
routing session is active. Per query it walks the board's zones plus
footprint-scoped ones, skips any whose layer set does not cover the queried
layer (so a zone spanning several layers is answered on each of them), and for
each covering zone reports:

* the zone's identity (`zone_uuid`, `name`, `source`) and its own `net_code`;
* `in_outline` and `in_fill`;
* `distance_mm` - the distance to that layer's *filled* copper: 0.0 on copper,
  otherwise the distance to the nearest fill edge or void wall, and -1.0 when
  the zone carries no fill polygons for that layer;
* `fill_is_island` for the covering polygon;
* rule-area coverage and `keepout_flags` through the same
  `HasKeepoutParametersSet()` gate `syncZone` uses;
* `fill_provenance`.

The result additionally carries the copper classification
(`no_zone` / `outline_only` / `fill_single_net` / `fill_multi_net` /
`unknown`), `in_keepout`, the OR of the covering rule areas' flags,
`zones_tested`, and **every** hit.

Two implementation facts are worth naming because they were found the hard way
and are now pinned by tests:

* the layer check is `IsCopperLayer(...)`, not a `F_Cu..B_Cu` range. This enum
  orders the stack `F_Cu=0, B_Cu=2, In1_Cu=4 (+2 per inner layer)`, so a range
  check silently refused every inner layer - the first native run answered
  "no zone" for a pour on `In1.Cu` that was there;
* an empty fill poly set is reported as `no_fill`, never as a distance.
  `HasFilledPolysForLayer()` is true for a zone whose layer entry exists but
  holds no polygons, and KiCad's `SquaredDistance` then returns its "no
  segments" sentinel - which read as a 2147 mm distance until it was guarded.

### The prefilter

`pcb_world/agent/zone_coverage.py` turns the query into a ranking signal:

* **Margin.** `board_margin_mm()` reads the board's own `min_clearance_mm` and
  the wider of the default netclass's track width / via diameter, and returns
  `clearance + copper radius` with a `basis` string. Unreadable or unset fields
  (KiCad writes a negative sentinel) contribute 0.0 rather than a guess, which
  can only weaken the answer.
* **Classification.** A hit within the margin is *contact*; contact with any net
  other than the candidate's - including a netless pour - is `foreign`, contact
  with own and foreign nets at once is `mixed`, own-net contact only is `own`,
  and no contact is `none`. Unknown is a first-class state and passes through.
* **Samples.** A candidate's own waypoints, plus a bounded, deterministic sample
  along the plan's own start -> target corridor (`zone_prefilter_pitch_mm`,
  `zone_prefilter_samples`), sampled on both copper faces when the leg changes
  layer - because that is where the router will place its via. Without the
  corridor sample a direct plan, which names no waypoints, could never be
  classified at all.
* **Cache.** Answers are cached per (point, margin) for as long as the zone fill
  cannot have changed. Routing edits copper; it does not re-pour. The cache is
  dropped by `invalidate()` or when `KiCadEngine.zone_fill_epoch` moves (a
  refill). What is cached is the engine's **net-independent** row for that point,
  not the classified verdict: the net decides own vs foreign, so caching a
  verdict would hand one pair's answer to the next pair that asked about the
  same copper. The verdict is derived on every call; only the query is skipped.
  In the campaign the cache served 18 060 hits against 1 328 engine queries.
* **Ranking, never filtering — with a limit that matters.** `_zone_ranked` is a
  stable partition: candidates the prefilter calls foreign (or mixed) move
  behind the rest, everything else keeps its order, and the ranker itself drops
  nothing from the list it is handed. That is *not* the same as "every plan
  still gets tried": the sweep truncates to `candidate_limit` after ranking, so a
  plan moved late can fall outside the window — measured below, where 58 plans
  did exactly that. `suppressed` is False by construction and the field exists to
  make the ranker's own behaviour checkable.

### The rejected alternatives

The research pass considered three ways to reach the goal without an interface
change and rejected all three: the bounding-box proxy from `get_board_items()`
(measured at a 74.8% flag rate on every copper layer against 50.8%-67.0% true
fill coverage - no discrimination, no voids, multi-layer pours invisible); a new
Python `.kicad_pcb` zone-fill parser (needs its own arc-aware point-in-polygon,
duplicates KiCad's logic, and describes the file while candidates are evaluated
in memory); and importing the build tree's `pcbnew` module into the environment
process (used read-only for the earlier survey, but as a shipped path it loads a
second copy of the GPL library and a second board across the boundary
`check_separation.py` enforces).

## The bounded campaign

One trial, `phase11_routing/run_zone1`, from the exact accepted generation
(`phase8_routing/run_via1/artifacts/accepted_artifact.json`), copied to a private
scratch directory and hash-checked before and after. The trial predates the
opt-in default and ran with the prefilter **explicitly enabled**; the driver now
requires `--zone-prefilter` for that, which is verified below. Zero planner
requests (`model_usage.requests = 0`); the phase-10 balanced budgets
(`--candidate-limit 12 --drc-probes 8 --incremental-drc`); prior history
transplanted from `phase10_routing/run_refusal2` (2 936 records carried, 0 dropped
as other-digest; the digest itself stays in the private tree).

| metric | value |
|---|---:|
| wall clock | 704.9 s (of a 1 400 s budget) |
| stop reason | `no_progress` (stagnation 25 of the configured patience 25) |
| pairs ranked by the prefilter | 25 |
| candidates checked / ranked late | 342 / **289** |
| point queries / cache hits / batches | 1 328 / 18 060 / 273 |
| own records produced | 284, over 25 distinct pairs |
| copper state of those records | all `restored` |
| records that closed and were refused | 54 |
| accepted | **0** |
| `best_board_sha256` | `6c4f8ab81b83…f593477cf1b` - unchanged |
| engine leases | 51 granted, 0 expired, max overrun 0.0 s |

**What the ranking actually changed.** Candidates are truncated to
`candidate_limit` *after* ranking, and this board's pairs offer more plans than
that (13.68 candidates per pair, 25 pairs). Of the 289 foreign-pour candidates,
231 stayed inside the window and were attempted anyway; **58 were ranked out of
the window and spent no transaction** - none of them was a duplicate of an
already-recorded plan, so all 58 are plans this run would have tried had they
kept their original position. The prefilter therefore changed *which* plans the
sweep spent its budget on, and it did so without removing a candidate from the
list or touching an acceptance decision.

**What it did not do.** Nothing closed, so nothing was promoted and no promotion
gate ran. The run's own 25 pairs were all substitution offers rather than the 43
ratsnest edges the taxonomy indexes: under the configured budgets those edges
were already answered by the inherited history, and a ranking-only prefilter
cannot manufacture a new plan for an exhausted pair. The single
`retained_unknown` record in the state is carried history (it sits at index 1 433,
inside the inherited block); every one of this run's own 284 records restored.

**Board state.** The accepted generation is byte-identical: `6c4f8ab81b83…`
re-hashed after the campaign, identical to the accepted pointer's manifest. The
run's own artifact is a fresh child carrying that same board bytes, so the
pointer is untouched and nothing was promoted.

The refreshed taxonomy
(`phase11_taxonomy/`, aggregate `public_summary.json`) reports
`accepted.unchanged: true` and its five categories still summing to 135:
`connection_not_verified` 49, `drc_regression` 43,
`copper_absent_at_offered_anchor` 26, `unattempted_cap_observed` 11,
`unnamed_net_at_offered_anchor` 6.

## Verification

| command | exit | result |
|---|---:|---|
| `bash tools/reliability/check_phase.sh --strict` | 0 | 473 unit + 129 native, no skips |
| `python tools/reliability/check_engine_patches.py` | 0 | 3 patches apply to the pin and reproduce the engine tree, 6 files byte-equal |
| `python tools/check_separation.py` | 0 | 4/4 checks; wire copies identical (`f787f5be4e1f…`) |
| `git diff --check` | 0 | clean |
| native zone suite (`tests/agent/test_zone_point_query.py`) | 0 | 13 tests over real zone geometry |
| prefilter policy suite (`tests/agent/test_zone_prefilter_unit.py`) | 0 | 11 tests, no engine |
| private driver CLI mapping (`zone_prefilter_enabled`) | 0 | `--zone-prefilter` True, `--no-zone-prefilter` False, bare False, both refused |
| private run `verify_on2` with `--zone-prefilter` | 0 | `enabled: true`, 2 pairs ranked, 27 of 37 candidates ranked late, 42 queries / 140 cache hits, board hash unchanged |
| private `phase9_taxonomy.py` refresh | 0 | accepted generation `unchanged: true`, categories sum to 135 |
| accepted-generation sha256, before and after the campaign | - | `6c4f8ab81b83…f593477cf1b`, identical |

## Limits and risks

* **The campaign is not evidence the prefilter helps.** It changed which plans
  were tried (58 transactions not spent on foreign-pour plans) and closed
  nothing either way. No control run was made, so "better" is not claimed - only
  "different, bounded, and never a gate".
* **The samples are a sample.** A candidate is a plan; the query answers about
  points. The prefilter tests a plan's waypoints plus a capped sample of its own
  start→target corridor on both copper faces, but the router still chooses where
  vias land and how it shoves, so a "clean" candidate is not a promise. Only
  positive contact is used, and only to rank.
* **The trial never reached the refusal edges.** Under the phase-10 budgets those
  pairs were already answered by inherited history, so the run walked
  substitution offers and stopped on stagnation. A future phase that wants to
  measure the prefilter's effect on the 43 refusal edges must either regenerate
  the candidate set for them or give the run budget to reach them, and should run
  an explicit control with `--no-zone-prefilter`.
* **Empty-but-present fill layers are reported as `no_fill`, and the prefilter
  treats a covering zone with no stored fill as unknown.** That is the
  conservative reading of "declared pour, no stored geometry", and it passes the
  candidate through rather than calling it safe.
* **Cost is bounded but not free.** 1 328 point queries over 25 pairs, each query
  walking the zones on its layer. The cache (18 060 hits) is what keeps that off
  the per-candidate path; a future change that invalidates the cache per
  mutation would make this expensive and should be rejected.
* **The native DRC's own jitter is unchanged.** The run recorded
  `drc_jitter_suspected` (7 findings resolved on an unmodified board), the same
  known behaviour phase 10 documented.
* **The trial's prefilter counts predate the cache fix, so they are indicative,
  not authoritative.** `run_zone1` ran with the net-blind verdict cache; within a
  pair (one net) that is harmless, but where two pairs' sampled points coincide
  a classification could have been taken from the other pair's net. The 289 / 53
  split and the 58-candidate figure therefore describe what that run did, not a
  clean measurement of the ranking. The outcome - zero accepted, board
  byte-identical, pointer untouched - is unaffected: the prefilter never touched
  an acceptance decision. `verify_on2`, which re-runs the path with the corrected
  cache, is the current evidence.
