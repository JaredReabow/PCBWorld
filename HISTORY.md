# History

Append-only development log for this fork. Newest entries at the bottom; never
edit or remove an earlier entry. Release-level notes live in
[CHANGELOG.md](CHANGELOG.md), and the phase documents under
`docs/agent-work/` carry the detailed accounts.

## 2026-09-26 — fork created, agent reliability phase 1

* Forked `LGAI-Research/PCBWorld` to `JaredReabow/PCBWorld`; cloned to
  `/Users/leo/Documents/PCBWorld-reliability` at upstream `b3d62f5` (v1.0.1);
  feature branch `feat/agent-reliability-actions`.
* Added `pcb_world/agent/`: versioned structured actions with pre-dispatch
  validation, named routing modes mapped through the canonical mode table,
  authoritative snapshots with a monotonic revision/token, a transactional
  `connect_targets` / `probe_candidates` API, and fail-closed rule-context
  gating.
* Patched the engine (`kicad-patches/rl/`) so the routing-time DRC engine loads
  the project's own `<board>.kicad_dru`, recorded as
  `patches/engine/0001-routing-rule-context.patch`; rebuilt a private build copy
  (C++ content hash `53a35434`). The reference engine checkout and build were
  left untouched.
* Added `tests/agent/` (62 unit + 18 native tests over synthetic boards) and
  `tools/reliability/check_phase.{sh,py}`.
* Recorded a measured limit: the loaded rule file is honoured by validation but
  not by routing geometry on this build, so rule-required transactions fail
  closed. Details in `docs/agent-work/reliability/RESULT.md` §5.
* Nothing committed or pushed in this entry.

## 2026-09-26 — correction pass (root review findings 1-9)

* Tokens are now `session_id.revision.fingerprint` over copper **and** routing
  session state; a same-count external geometry change and another session's token
  are both refused, and `token=None` requires an explicit `require_tokens=False`
  session (the JSON tool surface always requires a token).
* Validation is strict: supported schema versions only, no bool/fractional
  integer coercion, unknown actions/parameters/fields refused, malformed or empty
  endpoints refused as `malformed_coordinate`, and a session without a board path
  derives its rule context from the engine.
* The caller-supplied enforcement boolean is gone. The rule context is proven
  automatically on every mutating call (engine can report it, `.kicad_pro` read
  from disk, no rule-load error, the board's `.kicad_dru` is the file loaded);
  unknown context fails closed.
* Added `pcb_world/agent/drc_gate.py`: a mutation is kept only when the engine's
  own DRC reports no new relevant violation under that context, compared by
  violation identity, with connectivity findings treated as progress. Rejection
  rolls back and verifies the restoration.
* `canonical_rows` quantises to nanometres, sorts items and includes a via's full
  layer span; rollback verification now also checks session state, and
  `unverifiable_properties()` declares what the mirrors cannot expose.
* `connect_targets` is atomic by default with an explicit `provisional=True`
  opt-in; results describe the restored final state; an unverifiable rollback
  quarantines the session (mutations refused, snapshot dirty, probing halted).
* `endpoint()` resolves net/layer through the engine's own cluster query and
  refuses unknown or ambiguous copper; `allowed_actions` advertises no mutation
  for unknown state and no `connect_targets` while a route is open; a connectivity
  rebuild failure is `unverified`; `from_env` requires
  `acknowledge_env_desync=True`.
* Native coverage: a default (0.25 mm) via is rejected by the gate with verified
  rollback; a 1.0 mm rule over a 1.0 mm corridor is rejected with the DRC picture
  restored; the shove candidate demonstrably displaces NET2 and the probe restores
  it exactly; a malformed rule file refuses mutation; a requested-but-missing rule
  file is surfaced.
* Phase gate: `--strict` requires a native build, `EXPECTED_NATIVE_TESTS` executed
  tests and zero skips, and fails on load/provenance errors; the evaluation logic
  is unit-tested. Added the JSON tool adapter and a model-free demo transcript.
* Current evidence: `bash tools/reliability/check_phase.sh --strict` → unit 121,
  native 20, exit 0; upstream engine-API suite unchanged at 512 passed / 2
  pre-existing environment failures / 3 skipped. Nothing committed or pushed.
* The engine patch's C++ comments and bindings no longer claim that loading the
  project rule file makes routing obey it; they state the measured limit and point
  at post-route DRC acceptance. Rebuilt and restamped: the private build's C++
  content hash is now `2613fb07` (the pre-correction build was `53a35434`).

## 2026-09-26 — second correction cycle (fault injection findings A-E)

* **A — no unverified copper survives.** Every post-checkpoint failure path in
  `_act_gated` (connectivity rebuild, unreadable post-probe, unexpected
  exception) now restores, verifies copper *and* session state, invalidates the
  DRC cache and quarantines when the restore cannot be established. Checkpoint
  creation is guarded, the transaction loop releases every step handle in
  `finally`, an unreadable connectivity result is never usable progress (not
  even with `provisional=True`), and `atomic=False` without `provisional=True`
  is refused as `underspecified`.
* **B — the DRC side-channel is checked.** `take_violations` re-reads the
  engine's rule-load channel after every run and raises `DrcContextError` on a
  reported failure, a vanished file, or a changed context identity (rule
  content, project file, pads); the gate stores that identity with the baseline
  and refuses cross-context comparisons. `assert_rules_applicable` treats the
  engine's reported file as authoritative, so a caller context can only agree
  with it or be refused — never downgrade it.
* **C — the JSON adapter is strict.** A non-empty token is required
  independently of the session policy; point shapes, waypoint sequences and
  booleans are validated before dispatch; infinite values are refused;
  `_point3` returns `None` instead of NaN so refusals are `allow_nan=False`
  serialisable; `already_connected` is a successful call and refusals carry a
  top-level `reason`.
* **D — result semantics.** Rollback verification compares and reports target
  and layer as well as head/net/active; `committed` is three-valued (`None` =
  unknown) with an explicit `copper_state`; probe results carry `evaluated` /
  `would_commit` and always report `committed=False` for the restored board.
* **E — docs and tokens.** The docs state the ownership assumption (exclusive
  engine; only the tracked properties are fingerprinted) and that rollback is
  exact over the visible rows, not a byte-identical board restoration; a
  geometry-less snapshot returns an empty token and advertises no actions.
* Evidence: `bash tools/reliability/check_phase.sh --strict` → unit 150,
  native 20, exit 0; 27 new fault-injection regressions. Nothing committed or
  pushed.

## 2026-09-26 — third cycle: final acceptance blockers

* **Wrong DRC error-code enum.** `pcb_world/agent/drc_gate.py` claimed
  `DRCE_DANGLING_VIA = 14`; the pinned engine (`drc_item.h`) has
  `UNCONNECTED=1`, `DANGLING_VIA=12`, `DANGLING_TRACK=13`,
  `DRILLED_HOLES_TOO_CLOSE=14`, `DRILLED_HOLES_COLOCATED=15`. The mistake
  excluded drilled-hole spacing violations from acceptance. The table is
  corrected, documented as positional values of the pinned engine (not a stable
  API), and bound to that engine by `parse_drc_enum` plus a native test that
  reads the build's `kicad_src/pcbnew/drc/drc_item.h`; native records produced by
  the real engine prove 14 is relevant (rejected with rollback) and 12 is the
  connectivity signal.
* **Unreadable transaction probes.** `_execute_plan` used post-step,
  pre-acceptance and final probes without checking them, so the result could say
  `ok`/`accepted`/`committed` while the snapshot said `unverified` with copper on
  the board. Every such read is now checked and routed through
  `_unreadable_transaction` (gate invalidated → restore → verify → quarantine),
  and `_finish` enforces the invariant centrally: an unreadable final probe
  forces `unverified`, `accepted=False` and `committed=None` with
  `copper_state='retained_unknown'` when copper was being kept.
* Evidence: `bash tools/reliability/check_phase.sh --strict` → unit 159,
  native 24, exit 0, no skips; the earlier `act` connectivity-failure and DRC
  side-channel regressions still pass. Nothing committed or pushed.

## 2026-09-26 — phase 1 ACCEPTED

* Astra reviewed the final changes and independently verified: the pinned DRC
  enum mapping (code 14 relevant, code 12 the connectivity signal, against
  `build_rl/kicad_src/pcbnew/drc/drc_item.h`), the code-14 rejection path, and the
  exact unreadable-transaction reproduction (now `accepted=False`,
  `committed=False`, zero tracks, verified rollback). `git diff --check` passed.
* **The first experimental reliability phase of this fork is accepted.** Scope:
  `pcb_world/agent/` (validated structured actions, session-bound snapshot
  tokens, proven rule context, native-DRC acceptance, atomic transactions), the
  JSON tool surface, the engine rule-context patch, and the phase harness, on
  scoped synthetic evidence — 159 unit + 24 native tests, 183 total.
* Stated limits unchanged: no whole-board routing claim, no live LLM policy
  integration, no PNS custom-rule fix (post-route acceptance refuses violating
  copper instead), exclusive engine ownership with tracked-property detection
  only, and partial wire mirrors (no track type/arc mid-point/lock flag/
  solder-mask margin).
* Next planned phase is integration, starting with `connect_targets` as a
  first-class `methods/llm_agent` tool — not another review loop.
* Implementation remains local, uncommitted and unpushed.

## 2026-09-26 — phase 2: resumable routing agent (implemented, ready for review)

Builds on accepted phase 1 without changing it. The accepted phase-1 source is
captured first as a private tarball
(`_baseline_phase1/phase1-accepted-source.tar.gz`, sha256 `28847242…`).

* Added the agent loop: `pcb_world/agent/observations.py` (board-derived layer
  mapping, net-pair enumeration, compact planner observation under a character
  budget), `pcb_world/agent/scheduler.py` (deterministic candidates, ranking,
  attempt history, stall detection, provenance-verified run state) and
  `pcb_world/agent/runner.py` (deterministic-first loop, bounded planner use for
  blocked pairs, limits, best-board checkpointing, structured report, provider
  error taxonomy).
* Added the supported integration (`methods/llm_agent/tools/pcbworld_tools.py`,
  `methods/llm_agent/policy/structured_agent.py`) and the CLI
  `tools/reliability/route_agent.py`; no provider implementation was rewritten.
* Safety fixes found by running the loop on a real board: the DRC cache is
  re-seeded after a verified rollback; a zero-length `line` step that the engine
  refused is dropped; `routing_target` is advisory (compared, reported, listed in
  `unverifiable_properties()`) instead of fatal; sub-0.25 mm cross-layer pairs are
  classified `coincident_cross_layer` → `unsupported_pair`; planner usage is
  recorded on failed replies; the saved artifact carries the **input** project
  and rules verbatim instead of the engine's re-serialized project; and a run
  defaults to `KICAD_ENGINE_REUSE=0`, recorded in `state.metrics["determinism"]`.
  A resume that names a different run directory from its checkpoint is refused
  (`ValueError` in the runner, exit 3 `resume_run_dir_mismatch` at the CLI), because
  the state file goes back where it came from while board artefacts go to
  `--run-dir`.
* Measured on the private V3 pilot: 149 unrouted edges before and after in every
  run, `accepted = 0`; the near-field pairs are sub-0.25 mm cross-layer coincident
  bridges the engine cannot bridge. One usable planner plan (`mark_obstacles`) in
  the bounded budget. Native DRC is **not** fully deterministic at this board size
  (8149 / 8149 / 8152 relevant-pass counts on an identical file), so the report now
  names a delta on unmodified copper (`notes` + `metrics.drc_jitter_suspected`) and
  the acceptance gate stays one-sided.
* Evidence: `bash tools/reliability/check_phase.sh --strict` → exit 0 (unit 225,
  native 30, no skips); `pytest tests/agent` → 255 passed; `git diff --check`
  clean. Private account:
  `/Users/leo/Documents/Helix_Control_V3_pcbworld_improved/report.md`; public
  account: `docs/agent-work/reliability/phase2/`.
* Stated limits: no V3 connection closed, no routing-quality claim, planner usage
  totals are a lower bound, and only one board and one rule file were exercised.
* Nothing committed, staged or pushed.

## 2026-09-26 — phase 5: the installed-KiCad cross-check, resolved

Phase 4 blocked board-level acceptance on a disagreement between the pinned
engine (zero added relevant DRC identities) and the installed KiCad CLI (23 added
clearance errors). Both numbers were real measurements of different things.

* **The installed CLI truncates its report.** KiCad's `DRC_ENGINE` caps findings
  per error code (`ERROR_LIMIT = 199`, `EXTENDED_ERROR_LIMIT = 499` for clearance
  and unconnected items); the pinned source already removes them for our engine.
  The installed 10.0.6 CLI reported exactly 499 `clearance` and exactly 199 in
  five other classes, every run — the caps, not the board. Two runs of one
  *unchanged* file differed by 18 identities, and a byte-identical load/save copy
  by 19: the phase-4 "23 added" was that artefact.
* **A complete reporter was built task-isolated** from the pinned source
  (`ninja -C build_rl kicad-cli pcbnew_kiface`, CLI 9.0.8). It links the *stock*
  copper-clearance provider while the router module links the RL fork, so its DRC
  is an independent implementation. The pinned router was not rebuilt by that
  step (`.so` hash and stamp verified). Complete reports are identical run to
  run: 9758 findings on the source, 9754 on the six-closure candidate, **zero
  added identities in every class**, four resolved `track_dangling` warnings,
  unconnected 149 → 143. Physical comparison of the candidate: 20 new track
  segments, zero changed geometry, zones and fills untouched, footprint geometry
  multiset identical.
* **The gate was rebuilt, not loosened.** `pcb_world/agent/cli_gate.py` refuses a
  report sitting on a known per-class cap (possible truncation), requires at
  least two runs per board and per-class reproducibility, compares exact
  identities with non-increasing counts, records the selected binary, version,
  options, rule hashes, both board hashes and report completeness in the
  generation manifest, and distinguishes `not_configured` / `unavailable` /
  `unverified` / `regressed` / `verified`. There is no count-only acceptance
  path: a candidate that replaces one finding with another at an equal total is
  refused, and that regression is in the suite.
* **Zone refill (contract C) implemented and measured.** `KiCadEngine.fill_zones`
  (engine stamp `2613fb07` → `16e66300`, `zone_filler.cpp` linked into the routing
  module with a GUI stub for the one interactive path) refills derived copper
  under the current rules. On a disposable copy it clears all 6904
  fill-clearance findings (complete CLI `clearance` 7488 → 0) but costs **11
  existing connections** (unrouted 149 → 160, pad groups 335 → 346) and adds 117
  findings in other classes. It is retained as a private generation and is *not*
  adopted as the routing state; the staging-versus-original decision is with root.
* Evidence: `bash tools/reliability/check_phase.sh --strict` → exit 0 (275 unit +
  39 native, zero skips); `git diff --check` clean. Public account:
  `docs/agent-work/reliability/phase5-kicad/`; private account and provenance:
  `/Users/leo/Documents/Helix_Control_V3_pcbworld_improved/report_phase5.md`,
  `provenance_phase5.json`.
* Version boundaries: acceptance uses the pinned engine (`16e66300`) and the
  complete build-tree CLI **9.0.8**; the installed application **10.0.6** is
  capped, is used for diagnosis only, and is never reported as verified.
* Nothing committed, staged or pushed.

## 2026-09-26 — phase 4 recovery boundary (implementation pending review)

* Replaced fixed-name checkpoint promotion with immutable artifact generations,
  content-hashed sidecars, and one atomic accepted-generation pointer. The pointer
  carries its checkpoint snapshot so restart can reconcile a crash between the
  pointer write and `run_state.json`.
* Added a fresh-process saved-artifact gate for rule proof, exact geometry digest,
  reopened progress, and detailed relevant-DRC identities before pointer change.
  A verifier timeout, malformed result, sidecar mismatch, or DRC regression keeps
  the previous pointer active.
* Added per-operation deadlines to the runner-owned KiCad IPC child and ensure a
  timeout kills and reaps that child. The runner no longer queries a quarantined
  session during final accounting.
* Added core resume provenance checks, missing-artifact refusal, safe copied-run
  relative paths, pair-selection cursor, geometry-scoped failed-plan history,
  idempotent waypoint keys, endpoint net/via-span checks, and a 1 nm coincidence
  tolerance so small distinct gaps remain scheduled.
* Persisted planner request reservations before dispatch and retained cumulative
  known token usage across terminal retry failures; transport deadlines/backoffs
  are clamped to the remaining run budget.
* Corrected the phase-3 public record: its former atomic-promotion and final-DRC
  safety claims were not established by the old file layout. Removed exact V3
  net identifiers and anchor coordinates; historical counts remain, with saved-
  artifact acceptance explicitly pending.
* No private board, source project, prompt, coordinate, net identifier, log or key
  was added to the public repository. No new routing/API requests, commits, or
  pushes were made.

## 2026-09-26 — phase 3: corrected integration and real V3 progress

Answers the phase-2 review findings and the pilot's measured bottlenecks. Phase 1
stays accepted; phase 2's documents stand except where a claim was corrected.

* **Layer and endpoint identity.** `LayerResolver.build` reads
  `KiCadEngine.layer_map` and verifies it on samples (0.01 s on V3, was ~12 s of
  board-wide sampling); `_anchor_layer` takes each anchor's layer from the
  ratsnest and resolves spans-copper anchors with a net-checked fallback;
  `AgentSession.endpoint` matches pads on their converted human layer instead of
  X/Y alone. The 0.25 mm "unsupported bridge" heuristic is replaced by a geometric
  coincidence epsilon — **phase 2's "the engine cannot bridge these" was a
  selection error**, and the V3 pass proves it.
* **Fair queue, honest completion.** `scan_net_pairs` returns what was offered and
  why everything else was not; the queue is round-robin across nets with ordinary
  connections before degenerate bridges; exhausted pairs and structural endpoint
  refusals are retired with a reason; completion is the engine's own unrouted
  count, never an empty filtered list.
* **Budgets that hold.** Planner totals survive a resume across planner instances;
  `set_budget` is enforced before every HTTP attempt; only `content` is an answer
  (`reasoning_content` is never executed); a truncation escalates once instead of
  re-sending; the CLI default token cap is the measured-working 8000.
* **Artifact integrity.** Stage → verify (sidecars byte-identical, board
  non-empty) → atomic promote → manifest; inferred `.kicad_pro`/`.kicad_dru` are
  resolved, hashed and copied; the promoted board is reopened in a fresh engine and
  its connectivity and DRC total recorded; a final-DRC regression blocks promotion;
  nothing is promoted from a quarantined session.
* **Real V3 progress.** Six connections closed under full native DRC acceptance
  (149 → 143 unrouted edges, 335 → 329 pad groups, 6175 → 6195 tracks), every one
  through the phase-1 transaction. The promoted artifact reopens at DRC 8139
  against a 8149 baseline with `added_relevant_count: 0`. All six closures were
  deterministic; the model's 14 executed plans were refused by the router, and its
  20-request budget was enforced exactly.
* **Documented blockers, not worked around.** Zone fills use a finer clearance than the
  recovered rule file requires (`[rule values redacted]`), so the baseline carries 7488
  `source-fill-clearance` findings and routes inside a fill add more; a zone refill
  under the current rules is the honest next step and the engine has no such
  operation. No rule was relaxed, no class ignored, no count tolerance added, and
  the scoped incremental DRC was deliberately not used as an acceptance gate.
* Evidence: `bash tools/reliability/check_phase.sh --strict` → exit 0 (unit 237,
  native 37, no skips); `pytest tests/agent` → 274 passed; `git diff --check`
  clean. Private account:
  `/Users/leo/Documents/Helix_Control_V3_pcbworld_improved/report_phase3.md`.
* Nothing committed, staged or pushed.

## 2026-09-26 — phase 4 recovery corrections validated

* Added a resumed-run final-DRC rejection regression: the accepted artifact pointer,
  progress tracker, report progress, and cumulative accepted count all return to the
  prior checkpoint, while the rejected candidate remains explicitly identified.
* Strict harness passed: 260 unit tests and 37 native tests, zero skips;
  `git diff --check` passed.
* Recovery implementation is validated; board-level acceptance remains pending the
  separately documented KiCad CLI DRC identity discrepancy. No commits or pushes.
## 2026-09-26 — phase-5 acceptance withdrawn pending revalidation

Root review found that the earlier phase-5 board acceptance relied on invalid
terminal and incomplete CLI evidence. Preserve the original source and
six-closure reference as frozen inputs; treat the refill and route repair as
experimental staging only.

* The board reuses pad UUIDs. Terminal identity now combines UUID with physical
  geometry and inventory, and ambiguous collisions fail closed. Earlier terminal
  relation and per-net counts are withdrawn as unverified.
* Refill changed 64 non-copper silk/mask zones, accounting for 95 added silk
  findings. The engine refill is now copper-zone-only; silk and mask fill data
  must be preserved exactly.
* The CLI gate now checks report envelope/schema, stable item identities,
  reproducibility, report and tool hashes, effective sidecars, subprocess-tree
  cleanup, report retention, and remaining time budget. Experimental staging is
  bound to an immutable original reference and cannot move the accepted pointer.
* Focused terminal/CLI/store tests: 38 passed; Python unit group: 295 passed.
  Strict gate currently fails on `RouterProvenanceError`; the native code has not
  been rebuilt because `wx-config` is unavailable. Do not use the stale native
  module or earlier complete-CLI 9.0.8 run as current acceptance evidence. The
  installed 10.0.6 CLI is capped and diagnostic only.
* Regenerated `patches/engine/0002-phase5-integrity-and-copper-fill.patch`; clean
  pinned-engine-source apply and byte-for-byte comparison pass. No routing,
  refill, planner/API calls, commits, or pushes were made in this correction.

## 2026-09-26 — inventory-proven DRC identity, complete engine patch set, production final gates

The DRC gate no longer treats a UUID pair as an identity. `pcb_world/agent/
drc_gate.py` builds a `BoardInventory` from the engine's own track/via/pad
accessors and resolves each violation's item UUIDs against it: a UUID carried by
exactly one item keeps the existing key (and its movement tolerance), and
everything else falls back to a condition-refined identity where a moved
violation is an addition. Measured on
the frozen reference: 121 of 6845 copper UUIDs are reused (821 of 7545 items, all
pads) and 7972 of 7986 relevant keys have no inventory proof, so movement
tolerance now applies only where identity is proven. `tests/agent/
test_drc_inventory_identity.py` pins the rule, including the equal-count physical
pair swap that used to pass.

`tools/reliability/verify_saved_artifact.py` lost its legacy key-set fallback: the
baseline travels as the complete violation multiset (keys, conditions, inventory
ambiguity, provenance) and the child replays it through the same `diff_sets` the
local transactions use, refusing a request without it. A second mode compares the
terminal partitions of the frozen reference and the candidate in one fresh
process. `pcb_world/agent/final_gates.py` and `tools/reliability/
run_final_gates.py` are the production adapters for
`ExperimentalArtifactStore.run_final_validation`, which now runs the terminal gate
before the CLI gate so the CLI's only exemption (a churned `unconnected_items`
pairing) is bound to a fresh proof; `tests/agent/test_native_final_gates.py`
exercises the real child processes on synthetic boards.

Measured with those gates against the frozen original reference (nothing
promoted): the six-closure candidate passes the terminal partition (0 splits,
1060 terminals) but is refused by the native gate (179 added relevant identity
groups, all moved conditions of existing pair keys) and by the CLI gate (equal
count identity churn in relevant classes, driven by the reporter's
representative-pairing choice as the candidate file lists its footprints in a
different order). The copper refill is refused by all three: 19 added native
groups including new isolated copper islands, 33 split terminal relations, and a
CLI verdict that stays unverified because its unconnected pairing churned without
a passing proof.

`patches/engine/0002-phase5-integrity-and-copper-fill.patch` was regenerated
against the 0001-applied tree so it carries all six changed engine files, the
SWIG 4.5.1 compatibility change and `engine_server/wire.py` included;
`tools/reliability/check_engine_patches.py` proves clean application from the pin
`7a31e0c` and byte-equality with this checkout's engine tree, and
`patches/engine/README.md` documents the order, base and file list.
`bash tools/reliability/check_phase.sh --strict` → exit 0, 333 unit + 49 native,
no skips. No commits, pushes, provider changes or routing were performed.

## 2026-09-26 — baseline envelope binding, drift and deadline checks in the final gates

Review found that `mode_capture_baseline` returned a provenance envelope but the
runner staged only the violation multiset, so `mode_verify_generation` replayed a
baseline while hashing the reference path separately: a substituted, more
permissive baseline could pass under a verdict that claimed the reference's
hashes. The staged value is now the full envelope
(`pcb_world/agent/reference_baseline.py`): violations plus policy, the reference
board/project/rules hashes, and the engine that measured it (`cpp_hash`, module
sha256, version, provenance-checked). The verifying child re-measures all of it
before replaying anything and refuses with named problems for a different board,
project, rules, policy, or engine build; the runner's in-process capture and the
child's `capture_baseline` mode build the same envelope through that one module,
so producer and consumer cannot drift. The production adapters also re-hash every
input after each child returns and refuse on drift ("an input changed while the
gate was running"), and each child's timeout is `min(per-child ceiling, remaining
run budget)` when a budget callable is configured. Tests: `tests/agent/
test_reference_baseline.py`, the native capture/wrong-reference/stale-engine cases
in `tests/agent/test_native_final_gates.py`, and the budget/drift cases in
`tests/agent/test_final_gates.py`. `check_phase.sh --strict` → exit 0, 343 unit +
52 native, no skips. No routing, promotion, commit or push.

Root review then rejected the nearest-candidate resolution rule (2.0 mm / 0.1 mm)
as heuristic: it is removed, so a reused UUID is *never* resolved and stays
unverified. Duplicate identifiers are to be repaired on a disposable copy of the
board (canonical metadata repair) instead of being relaxed into the gate.

## 2026-09-27 — complete item inventory, deterministic duplicate-UUID repair

The identity proof stopped being copper-only. `getBoardItems()` in
`kicad-patches/rl/pns_rl_router.cpp` now walks every container the board owns -
the track/via list, pads, board zones and drawings, board groups, and each
footprint with its graphical items, fields, zones and groups - and returns one
`BoardItemInfo` row per item: UUID, kind, source container, parent kind and
reference, layer, net code and a save-stable physical identity string. The wire
schema gained the matching mirror in both `wire.py` copies, and
`pcb_world/agent/drc_gate.py`'s `BoardInventory` is now built from that single
accessor (policy `inventory-identity-v2`). A missing or failing accessor is
incomplete, never a fallback to the copper rows.

Measured on the frozen reference (private evidence in
`phase6_coverage_baseline.json`): the copper-only inventory left 7924 of 7930
relevant findings without an inventory proof; the complete inventory leaves
1177, and the 301 that remain on the canonical board are findings whose item the
engine names only as the nil UUID.

Duplicate identifiers are now repaired on a disposable copy instead of being
guessed at: `pcb_world/agent/kicad_metadata.py` parses the board's real
s-expression (source spans kept, strings and comments handled), rewrites *only*
the identity tokens of occurrences whose UUID is carried by more than one item,
and splices them into the original bytes so nothing else changes - geometry,
nets, rules, footprints, layers, stackup, zone outlines, fill polygons, labels
and locked copper keep their exact bytes and their original order. Each minted
identifier is a UUIDv5 of the occurrence's own parsed structure, so the same
physical object maps to the same new identifier in any file that holds it, and
two structurally identical occurrences (or a reference to a reused identifier,
in the board or in a `.kicad_pro` / `.kicad_dru` sidecar) are refused rather than
split by file order. `verify_equivalence` proves after the fact that only
identity tokens moved; re-running the normalizer on its own output returns the
input bytes.

On the V3 boards that is 8164 renumbered occurrences across 922 reused
identifiers in each of the frozen source (`133edc28…`) and the six-closure
candidate (`255a5cb9…`), with 0 structurally identical collisions and 0
references: all 8164 mapped objects are shared between the two files and all
8164 map to the same new identifier, so the canonical pair differs only where
the boards themselves differ. Evidence and the canonical boards are private in
`phase6_canonical/`; nothing was routed, refilled or promoted.

With the canonical reference the production gates move: the native gate now
**passes** the six-closure candidate (it was refused before with 179 added
identity groups at an unchanged count), the terminal partition passes (335
clusters, 1060 terminals, 0 splits), and the CLI gate reports 19 of its 20
classes *exact* with zero regressions; its verdict stays **unverified** for one
reason only, and it is the reporter's: the 9.0.8 CLI emits the
`unconnected_items` section with a varying number of byte-identical duplicate
rows (17 or 18 on the same candidate at an unchanged raw 143), so the class
total is not reproducible between runs of one board. That is the outstanding
exact unverified, not a waiver and not a board difference. Zero-route
save/reopen of the canonical board is stable twice over (identical inventory,
terminal partition and relevant DRC multiset; the two saved files are
byte-identical), and the original->canonical terminal partition is identical
under the UUID-free physical mapping (1060 terminals, 335 clusters both sides).

Companion harness: `tests/agent/test_native_item_inventory.py` (zones, footprint
graphics, a reused footprint UUID and the save/reopen round trip) and
`tests/agent/test_kicad_metadata.py` (parser, duplicate repair, refusal,
reference and sidecar refusal, idempotence, CLI). `bash tools/reliability/
check_phase.sh --strict` → exit 0, 360 unit + 56 native, no skips. No routing,
promotion, commit or push.

## 2026-09-27 — raw unconnected counts, normalizer self-verification, metadata restore

Three integration fixes on top of that work, then the canonical promotion.

**The CLI's unconnected section is judged on its raw rows.** The reporter emits
a varying number of byte-identical duplicate rows for `unconnected_items` (on the
canonical pair: 143 rows either way, 17 or 18 of them repeats), so the
identity-collapsed total churned 131/131 vs 125/126 while the physical quantity
did not. `pcb_world/agent/cli_gate.py` now takes stability and the count delta for
that one class from `len(unconnected_items)` as reported, keeps the raw schema
and known-cap checks (raw and collapsed), and leaves the identity churn as a
diagnostic that only the bound fresh terminal-partition proof can excuse. A raw
rise is still a regression, an unstable raw total or a missing section is still
unverified, and every relevant class is still compared by exact identity with no
union or count-only substitute. `tests/agent/test_cli_gate.py` pins all four
cases. Re-run against the canonical pair the gate is **verified** with a
diagnostic churn note; the previous verdict was unverified on this artefact
alone.

**The normalizer verifies its own output before it reports ok.**
`normalize_text` re-analyses the spliced text (no duplicated identifier, no
structural collision, no reference to a duplicate), re-checks token-for-token
that only the mapped identity tokens moved, and refuses when a minted UUIDv5
would collide with an identifier the board already keeps or with another minted
one - detection, not trust in the UUID input space. `ok` is set only after those
checks; a caller cannot write a file from a result that has not passed them.
New tests cover the collision and duplicate-mint refusals.

**Dropped non-copper graphic metadata is restored, not lost.** KiCad's writer
only emits `(net N)` for a shape with a positive net code, and a non-copper shape
can never hold one (`PCB_SHAPE::SetLayer` clears it; `BOARD_CONNECTED_ITEM::
SetNetCode` coerces a non-copper net to 0), so the 58 `(net N)` tokens an
importer wrote on `Dwgs.User` graphics disappeared on the first save. New
`pcb_world/agent/serialized_metadata.py` copies exactly those tokens back from
the board a save came from - matched by item UUID and by net *name*, never by a
raw net index - and `KiCadEngine.save` runs it on every save. Geometry is never
copied; the result is re-parsed and verified before it is written, and a second
run changes nothing. On the canonical candidate 58 tokens were restored
(`98907206…` → `929e5631…`), and the complete native inventory, the terminal
partition and the relevant DRC multiset were byte-identical before and after:
the field is inert exactly as the KiCad source says. The routing runner's own
staged artifact keeps the tokens too, which is the "future saves" case.

**Promoted.** With the fixed gate the canonical six-closure candidate passes all
three production gates against the canonical original: native pass (7930
relevant findings, 301 without an inventory proof, none of them a reused
identifier), terminal pass (source 149 unrouted / 335 clusters vs candidate 143 /
329, 1060 terminals both sides, 0 splits, 0 merged, 0 vanished), CLI **verified**
(19 of 20 classes exact, zero regressions, the unconnected churn exempted only by
that bound proof). Accepted generation
`1790435662721972000-929e56319763-f7fc31ff`, board `929e5631…`.

The canonical boards now also carry stem-named sidecar copies
(`canonical.kicad_pro` / `canonical.kicad_dru`) because the engine resolves the
rule file from the *board* filename; without them a fresh engine cannot prove
its rule context and the terminal gate refuses the reference.

`bash tools/reliability/check_phase.sh --strict` → exit 0, 376 unit + 56 native,
no skips. No commit or push.

### Bounded adaptive routing pass (same day)

The routing phase started from the accepted canonical six-closure board
(143 unrouted / 329 pad groups) and ran the accepted safety contract end to end:
every mutating request through the phase-1 API, a full native DRC per
transaction, and no new *relevant* local violation accepted. Two passes of one
bounded run - 200 attempts authorised, 1800 s wall clock - evaluated 116 plans
over 18 of the 105 offered pairs: 12 direct, 12 push-and-shove, 35
obstacle-derived, 36 generic detour, 9 layer/via and one two-waypoint dodge,
plus 5 planner requests (DeepSeek, task-local key; 10 requests and 50,566 tokens
total against the 10-request / 200k-token budget, never printed).

**No new connection closed.** 83 plans failed with `connection_not_verified`
(the router could not produce a connection at all), 22 connected but were refused
by the native gate for adding a relevant violation, and 6 pairs are structurally
unsupported by the identity contract - the engine resolves one endpoint to net 0
(`observed net codes (0, 0)`, and one pair `(72, 0)`), so there is no provable
endpoint to route between. The board is unchanged: 143 unrouted / 329 pad groups
/ 6195 tracks / 209 vias before and after, and the run's own artifact
verification re-read it with **0 added relevant findings** against the reference.
The report notes one honest caveat: on the unmodified board the native DRC
reported 9 connectivity findings as "resolved" with no copper change, i.e.
engine jitter at this size, which is why the delta is read per identity and not
per count.

The six-closure gains are retained exactly, and the run's artifact
(`73518eb2…`, the canonical board re-serialized through the engine *with* the
non-copper metadata restored) was staged against the canonical original and put
through all three production gates in a fresh process: native **pass**,
terminal **pass/complete** (source 149 unrouted / 335 clusters vs candidate
143 / 329, 1060 terminals both sides, 0 splits), complete CLI **verified**
(19 of 20 classes exact, zero regressions). Evidence:
`phase6_final_gates/run_routed_adaptive/…/final_gate_evidence.json` and
`phase6_routing/run_adaptive/routing_evidence.json` (private).

Exact remaining hard constraints: the residual 143 connections are blocked
copper problems, not identity problems - no plan on any tried layer or mode can
close them within the accepted no-new-violation contract, and six pairs cannot be
planned at all because the engine cannot prove their endpoint's net.

## 2026-09-27 — verified re-anchoring, pour-aware targets, refill re-derivation

Root rejected the earlier "everything left is blocked" reading: it was drawn from
18 of 105 offered pairs and it conflated *identity* gaps with *routing* failures.
This pass splits them, with the geometry named.

**The endpoint gap is measured, not assumed.** 34 of the 143 ratsnest edges have
an endpoint the point-identity rule cannot name. Diagnosis
(`phase7_endpoint_diagnosis.json`, private) classifies them by board item:
- **6** are track-only clusters (real copper, no pad) - the verified-anchor rule
  now proves their net from the cluster's own tracks/vias and re-anchors;
- **2** were a track shadowed by an *unanchorable* zone in the engine's hit test;
  `getConnectedPoints` now walks every overlapping item on the layer in the same
  deterministic order instead of trusting the single item `itemAt` returns, so
  the track underneath is found;
- **26** sit on no anchorable copper at all (no pad, track, via or zone-covered
  copper within 0.25 mm; sampling the covering same-net zone's own bounding box
  found no sample the engine proves carries that net). Those pairs are retired
  with the exact coordinates, the covering zones, and the nearest same-net and
  foreign copper - never re-anchored by guesswork.

**New public policy, tested on synthetic mixed-layer/hole/island boards**
(`tests/agent/test_native_pour_endpoints.py`):
`verified_anchor` proves an endpoint's net either from a pad in its own cluster or
from the unanimous net of every track/via the engine anchors there;
`zone_anchor` re-anchors a point that sits in a same-net zone's void, accepting a
sample only when the engine proves the copper carries the scheduled net **and**
all accepted samples belong to one connected component (two same-net islands under
one anchor are refused as ambiguous); `component_waypoints` offers pour-aware
targets inside the *far terminal's own* component, proved by cluster intersection
(which is what makes closing pad -> waypoint close the connection, and which
enforces holes, keepouts, clearance and layer through the engine rather than
through my geometry); `anchor_diagnosis` names the geometry behind a refusal.

**Scheduler.** Pour targets are offered first among the informed plans, the
layer/via detour is offered before the generic sideways offsets so a truncated
candidate list cannot lose the only plan that leaves the layer, a two-waypoint
dodge enters and leaves an obstacle's offset corridor, and a single remaining
deterministic candidate that fails no longer skips the planner.

**Refill branch re-derived after the metadata work.** The copper-only refill of
the canonical accepted board (`fbad2dfd…`) reproduces the phase-5 damage exactly:
**33 split terminal relations**, +14 unrouted edges (143 -> 157) and +14 pad
groups (329 -> 343), with 0 added relevant findings. That is physical, not an
identity artifact of the old duplicate UUIDs, so the branch cannot promote under
the no-new-violation contract without first re-routing those 33 relations - which
is the same routing problem, and it is measured below. The reference for any
refilled staging remains the canonical original source, unchanged.

**Root's review of `serialized_metadata` is fixed.** The restored `(net ...)`
token now carries the *saved* net table's code for the same net **name** (net
codes are file-local indices; copying the source code across a renumbering would
point at the wrong net), with a permuted-code fixture pinning it, and
`RESTORABLE_TAGS` is now only `net`: `locked` is read by shove/cleanup, so a
deliberate unlock survives every save.

`bash tools/reliability/check_phase.sh --strict` → exit 0, 382 unit + 60 native,
no skips. Engine stamp `ac47369d`; patch `0002` regenerated and byte-equal.

### The bounded routing pass found a closure (same day)

From the canonical six-closure board (143 unrouted / 329 pad groups), the
deterministic pass (2400 s, no planner calls - the paid budget was already spent)
offered 113 pairs, attempted 18, evaluated 139 plans across direct,
push-and-shove, detour, obstacle-derived, two-waypoint dodge, layer/via and
pour-aware strategies, and **closed one connection** - the pair whose endpoints
are recorded in the private campaign evidence `[coordinates redacted]` - by the
new pour-aware plan `pour_1_component_anchor`, a waypoint on copper already
connected to the far terminal. The 4 pairs with an unanchorable endpoint were
retired with their exact geometry rather than retried as routing failures.

Board after: **142 unrouted / 328 pad groups / 6197 tracks / 209 vias**
(`bf32c2c099fb…`, generation `1790443642808325000-bf32c2c099fb-60f29049`). All
three production gates pass against the canonical original: native 0 added
relevant (7930 -> 7930, connectivity 219 -> 207), terminal 335 -> 328 clusters
with 1060 terminals, 0 splits, and the complete CLI **verified** with raw
unconnected 149 -> 142 and track_dangling 70 -> 65, every other class exact and
zero regressions. The accepted pointer moved only after those gates and the
improvement; the routing run's own coverage and per-pair reasons are in
`phase6_routing/run_adaptive4/routing_evidence.json`.

## 2026-09-27 — attempt cost profiled, scoped DRC differential, coverage pass closes five

Root asked two questions before any optimization: where does an attempt's ~130 s
actually go, and why does a ratsnest anchor that carries no copper retire the
whole connection? Both are now measured rather than reasoned about.

**Attempt cost, profiled with a timing proxy around the real engine.**
`tools/reliability/profile_attempt_cost.py` runs the production runner against a
real board with every engine call timed, because the existing attempt telemetry
could not answer this: probe-only records deliberately carried `duration_s = 0.0`
and the engine calls underneath them were never timed. On the accepted
142-unrouted V3 board: **eight full native DRC calls = 312 s of a 352 s window
(89 %)**, at 38.7-40.1 s each. A candidate that does *not* close the connection
was already cheap (~8 s, no DRC at all: it is rolled back from the full snapshot
and the restore is verified against the pre-transaction probe). A *closure*,
however, paid three full DRCs - one to accept the probe's copper, one for the
follow-up apply's baseline (the probe's accepted set had just evicted the base
baseline from the gate cache), and one for the apply's own acceptance.

**Two changes followed from that measurement, both preserving the gate.**
1. `RoutingRunner._apply_candidates` replaces the probe-then-apply round: the
   deterministic candidates are executed as real atomic transactions in ranked
   order, stopping at the first accepted one. A candidate that closes nothing is
   rolled back without a DRC; a candidate that closes the connection is judged by
   exactly one full native DRC on exactly the copper that would be kept. Its
   records carry `sweep_member`, so a six-candidate sweep still spends *one*
   attempt against the per-pair budget while every plan it evaluated still blocks
   an identical retry (`AttemptHistory.attempts_for_pair` / `tried_keys`).
2. The scoped native DRC re-check (`run_drc_incremental`) is now an **opt-in**
   verification pass, `AgentSession(incremental_drc=True)` /
   `RunnerConfig(incremental_drc=True)`, defaulting off. On this board it is
   0.2-0.35 s against 39-40 s for the same verdict (~120x).

**The scoped pass was only enabled after a differential, on real copper.**
`tools/reliability/drc_incremental_differential.py` replays each shape from a
checkpoint and compares the scoped pass against a whole-board pass on identical
copper (equal digests), on the frozen V3 board, which carries real pours:

| case | relevant multiset | connectivity count | verdicts | verdict |
|---|---|---|---|---|
| direct connect | identical | identical | agree | incremental 0.32 s vs full 39.7 s |
| connect in shove mode | identical | identical | agree | 0.35 s vs 39.9 s |
| shove an existing track | identical | identical | agree | 0.30 s vs 39.1 s |
| rollback (restore, then re-read) | identical | identical | - | DRC state travels with the checkpoint |
| rule bytes changed, same path | - | - | - | **refused** (stale rule context) |

The comparison keys on the violation itself rather than on UUIDs: replaying one
operation in one engine mints new UUIDs for the copper it creates, so a
UUID-keyed diff measures the UUID stream. Connectivity findings are compared by
*count* only - their reported coordinates are ratsnest tie-breaking, and the gate
never accepts or refuses on them (`acceptable` reads `added_relevant`). The
engine keeps its own guards: no signature or a changed rules path falls back to a
full run, and a design-rule write or zone refill clears the incremental state.
`tests/agent/test_native_drc_incremental.py` pins the same contract on synthetic
boards, including that a default session never calls the scoped pass.

**Coverage: a bare anchor no longer retires the connection.**
`component_anchor_candidates` searches the scheduled net's *own* inventory
anchors for the nearest ones the engine proves carry that net (pad in the
cluster, or every native track/via there agreeing), and `reanchor_pair` now tries
them after the same-cluster proof and the covering-zone sample. Every such
endpoint is labelled - `provenance="candidate"`, `moved_mm`, and for the pour
path the same treatment - so no report can present an alternative endpoint as the
one the ratsnest offered. When the offered anchor is replaced, the offered pair
is retired *for that copper generation* and the attempts are filed against the
pair actually attempted, which stops the scan re-offering an unusable anchor
every iteration.

**Bounded campaign from the accepted board.** `phase8_route.py` (private) carried
the previous run's 246 attempt records forward *only* for pairs whose local copper
is provably unchanged - a bounded window around both anchors, not a whole-board
digest - so 35 of 35 examined pair neighbourhoods were identical and no failed
plan was re-run (the fair queue would otherwise have started again at the same
eighteen pairs). It then ran the deterministic sweep with the scoped pass for
5 693 s of a 12 000 s budget: **932 attempts, 4 228 plan evaluations, stop reason
`pairs_exhausted`** (every offered pair's deterministic plans evaluated on that
copper generation), and **five new closures**, all on net 6.

| | accepted 7-closure board | after this pass |
|---|---|---|
| unrouted edges (native) | 142 | **137** |
| pad groups | 328 | **323** |
| tracks / vias | 6 197 / 209 | 6 206 / 209 |

Best board `f353d35c029d…`, generation
`1790451898090613000-f353d35c029d-c6b5df62`. Rollback discipline held across the
campaign: 3 786 records report `copper_state = restored` (3 591 of them after
applied steps), **zero** `retained_unknown`, and zero accepted-but-uncommitted.

**Acceptance.** Every one of the six promoted generations carries a fresh-child
`saved_artifact_verification` with `added_relevant_count: 0` plus a fresh
two-board terminal partition. The best board was then staged against the
canonical original (`phase6_final_gates/run_cov2`, generation
`1790453581125990000-f353d35c029d-71cd6596`) and passed all three production
gates: native 0 added relevant (7 930 -> 7 930; connectivity 219 -> 202, total
violations 8 149 -> 8 132), terminal
335 -> 323 clusters with 1 060 terminals and 0 split relations, and the complete
CLI 9.0.8 **verified** - raw unconnected 149 -> 137 (-12), track_dangling
70 -> 65 (-5), every other class exact, zero regressions.

`bash tools/reliability/check_phase.sh --strict` -> exit 0, 385 unit + 67 native,
no skips; `check_engine_patches.py` byte-equal; `tools/check_separation.py` 4/4.
The engine C++ was not touched, so patch `0002` is unchanged.

## 2026-09-27 — component-graph scheduling, and a router-rule defect behind the refusals

Root's review asked for two things before more routing: stop retiring a connection
because a *point* was empty, and use the refusals themselves as evidence. Both
found real problems.

**The ratsnest is a drawing; the terminal partition is not.**
`native_components(session)` reads the engine's own cluster membership
(`get_pad_cluster_members` over the complete pad inventory) and yields, per net, the
components that really exist - with a stable `component_id`, their proved anchor
points, layers and bounding box. On the 136-unrouted board that is 323 components
across 191 nets, in 0.04 s. `component_pairs` turns the same state into offers:
the nearest *distinct disconnected* component pairs per net (bounded, nearest
first), each anchor proved with the cluster rule. The scan merges those into the
same fair queue as the ratsnest edges and deduplicates by geometry, so one
physical connection is one offer (53 nets contributed 106 component pairs; 60 of
them were geometry the ratsnest had not named).

**Substitution is labelled as substitution.** `reanchor_pair_variants` widens the
search in three steps - the anchor's own cluster (`provenance="original"`), the
covering same-net zone sample (`proof="pour"`, which proves *the sample* sits on
that net's copper and explicitly not that the pour's island is the copper the
ratsnest meant), and the net's own components, unbounded in radius
(`provenance="candidate"`, `component_id`, `moved_mm`). A component already joined
to the other endpoint is reported `already_connected` and never offered, because
substituting into it would close nothing. `classify_offer` is the single place
that decides "original" vs "substitution", and the offer carries it
(`source`, `substituted`, both component ids) into the attempt record. An edge with
no provable substitute stays in the queue exactly as drawn and is counted in
`unresolved_offers`: a pair is named, never silently dropped.

**The refusals named a real defect, not a missing clearance.** A new diagnosis
(`phase8_drc_refusal_diagnosis.py`) replayed 24 of the 514 recorded
`drc_regression` refusals - plans that *closed* the connection and were refused by
the gate - and grouped them by class and geometry: `Clearance violation` 75,
`Hole clearance violation` 35, `Hole size out of range` 11, with the violation a
median of 0.113 mm from the plan's straight line (i.e. on the copper, not beside
it). Chasing one to its message found the cause:

```
Hole size out of range (board setup constraints ...) [rule values redacted]
```

The PNS size cache was not populated from the project: the router placed 0.25 mm
drills on a board whose setup (*and* whose own netclass) says 0.3 mm, so every via
it added was illegal and dragged the neighbouring hole-clearance checks with it.
`AgentSession._sync_routing_sizes` now adopts the project's own netclass via
diameter/drill, floored by the board's minimum hole and annular ring, and records
what it adopted. That is not a rule relaxation - it is the value the board already
declares - and the test that used to pin "the default via is refused" now *asks*
for an illegal drill to keep the same property under test.

**The refusals also steer the next plan.** `AttemptRecord` carries
`drc_classes`, `drc_hints` and `closed_before_refusal` (`connected` cannot say it:
the copper is rolled back, so the recorded board no longer shows the connection -
the session sets `connected_before_refusal` before the rollback).
`generate_candidates` turns each hint into one sideways avoidance at the
violation's own position (`drc_avoid`) and one layer escape there (`drc_escape`),
and offers **via jogs** (`via_jog`) when the pair's layers differ or a refusal is
hole-class: a via has to sit somewhere, and a hole-dense spot cannot be fixed by
moving sideways within a layer.

The first version of that loop livelocked, and the run caught it: naming the
alternatives by a hint's position in the pair's growing history minted a "new"
plan key for the same copper each scan, so `tried_keys` could never retire it
(1525 distinct keys over 1624 records, one name repeating five times). Candidates
are now named by their **geometry**, hints are taken **one per class from its
earliest record**, and the set is ordered canonically - so the same copper yields
the same keys, and a growing history cannot invent new plans.

**The scoped DRC pass is now differentialled on the shapes it is used for.**
Root's correction was exact: the first differential covered only a direct connect
and a shove, while the new strategies insert vias and cross layers. The harness
now also replays a real via insertion and span, a layer transition (track -> via
-> track), a via-containing attempt that is discarded, and an intentional
clearance regression, and it **fails a case that produced no copper** instead of
counting a vacuous agreement. On the campaign board:

| case | produced | relevant multiset | added relevant (scoped vs full) |
|---|---|---|---|
| direct connect / shove / existing-track shove | yes | identical | 0 vs 0 |
| via insertion and span | via 209 -> 210 | identical | **8 vs 8** |
| layer transition | tracks 6206 -> 6209, via +1 | identical | **10 vs 10** |
| discarded via attempt, then rollback | yes | identical | 8 vs 8 |
| intentional clearance regression | yes | identical | **1 vs 1** |
| rule bytes changed behind the same path | n/a | n/a | **refused** |

**Campaign from the accepted board.** Three deterministic segments (4 673 s +
2 978 s + 374 s of a 12 000 s cap; the middle one is the livelock above, which is
why the third exists) carried their history forward **only for records measured on
the same board generation** - local neighbourhoods are deliberately not used, a
shove elsewhere can change the path a connection takes. On the final generation
that is **3 356 distinct (pair, plan) evaluations over 315 pairs**, from all three
offer sources: ratsnest 1 713, **component 789**, **substitution 882**; by
strategy, `drc_escape` 280, `drc_avoid` 250 and `via_jog` 245 were evaluated
alongside the established kinds. One connection closed - net 4, by
`pour_0_component_anchor` from a **component** offer - taking the board from
137/323 to **136 unrouted / 322 pad groups / 6 208 tracks / 209 vias**. Rollbacks:
3 195 `restored`, 6 `unchanged`, **zero** `retained_unknown`. The run ended with
`pairs_exhausted`.

**Acceptance.** Best board `ed5b783945c6…`, generation
`1790457591505422000-ed5b783945c6-c35663e1`; staged against the canonical original
as generation `1790467784620017000-ed5b783945c6-205eb4fb` in
`phase6_final_gates/run_graph1`. Native gate: 0 added relevant (7 930 -> 7 930;
connectivity 219 -> **201**, total 8 149 -> 8 131). Terminal: 335 -> **322**
clusters, 1 060 terminals, 0 splits, 0 merged. Complete CLI 9.0.8 **verified**:
raw unconnected 149 -> **136** (-13), `track_dangling` 70 -> 65, every other class
exact, zero regressions. Two generations of this campaign were promoted only after
the runner's own fresh-child native gate, and the accepted pointers of the earlier
generations are untouched.

`bash tools/reliability/check_phase.sh --strict` -> exit 0, 385 unit + 88 native,
no skips; `check_engine_patches.py` byte-equal; `tools/check_separation.py` 4/4.
Engine C++ unchanged, so patch `0002` is unchanged.

## 2026-09-27 — per-net via sizes, fair component windows, free-position via search

Root's review of the component and size code found two substantive problems, and
the next routing bundle followed from them.

**The via size is resolved for the net actually being routed.** The first version
probed a fixed list of net codes once at session start and applied one class to
the whole board. `resolve_via_size(engine, net_code)` now asks the engine for that
net's *own* effective class (`get_netclass_for_net`) and derives the size from the
declared diameter/drill and the board's minima; `AgentSession._via_size_for_net`
applies it at **transaction entry**, where the route's net is first known, and
re-applies it when the net's size differs from the one in force. Declared and
adopted values are kept separately, with the floors, so evidence never has to guess
which number a field means. Values must be finite numbers - a string, a `nan`, an
`inf` or a non-positive sentinel is unusable rather than coerced. Diameter and
drill are set as a **pair**: if the second setter fails, the router holds a
combination this session cannot read back, so it quarantines itself rather than
routing on unprovable state (a first-setter failure changes nothing and does not).
The adoption is a starting point, not a proof: the transaction's native DRC and the
saved artifact's fresh whole-board gate are what establish that the copper is
lawful, and the docstring no longer tells a board-specific story.

**Bounded windows now rotate, and say what they left out.** `component_pairs` used
to slice the first twelve components by bounding box and the nearest four pairs,
forever, on an unchanged generation - the remaining components were never
considered. Components are now ordered by proximity and read through a **rotating
window** (`window`, persisted in the run state and advanced each scan), and each
component's anchors rotate with the same offset. `native_components` derives a
component's identity from its **physical membership** (a digest of the sorted
native terminal ids, not the row index it arrived in) and expands each terminal
over the copper layers it actually occupies (a through-hole pad is a legal
endpoint on every layer it spans). `ComponentPairScan.coverage` reports, per net
and in total, how many components and pairs were examined and how many the window
and the cap left out - and the runner now stops with **`pairs_exhausted_capped`**
plus an explicit note when anything was omitted, instead of reporting a global
exhaustion it did not establish.

**Free-position via search, bounded and cancellable.** `_via_free_positions`
probes the engine's via prefilter over a bounded grid (default: a 2 mm disc at
0.15 mm pitch, which is ~558 points, against a 600-probe budget) around the
pair's anchors **and** its midpoint, accepting positions spread one per distance
band so the candidates are not four points inside one 0.15 mm neighbourhood. It
skips spots within 0.35 mm of a violation the gate already refused that pair for,
abandons immediately when the run is stopping, obeys a wall-clock deadline, and
records probes, bands and truncation as evidence. The root's correction is
explicit in the code and the docs: `pad_block_reason(for_via=True)` answers only
"not on a through-hole pad (and not grazing a same-net one)" - it says nothing
about tracks, zones, holes or clearance across the via's span - so it is a
**prefilter for choosing candidates** and the transaction's native DRC keeps the
authority. Refusals also gained a **shove-mode layer escape** beside the
walkaround one, because the copper a hint names may be movable rather than
immovable.

Two flaws the work surfaced and fixed: the via-free candidates were emitted inside
the jog block, so `max_via_jogs=0` silently disabled them; and the search filled
its whole candidate budget from the *start* anchor, never reaching the target's
side, which is where the far side of a blocked hop usually is. The budget is now
shared per anchor.

**Validation run (2400 s of the 12 000 s budget, as instructed).** From the
accepted 136-unrouted board, with the broader fair queue (`max_per_net=4`), the
rotating windows and the scoped DRC pass:

| measure | value |
|---|---|
| attempts / evaluations | 153 attempts, 4 045 distinct plans on the current generation |
| offer sources | ratsnest 1 728 / **component 1 399** / **substitution 948** |
| new strategies evaluated | `via_free` 55, `drc_escape` 325, `via_jog` 273, `drc_avoid` 273 |
| via searches | 47 searches, **11 654 probes**, 1 truncated by the probe budget |
| window coverage at the end | window 153; 17 components outside the window, 106 pairs outside the per-net cap |
| rollbacks | 3 876 `restored`, 6 `unchanged`, **0 `retained_unknown`** |
| refusals | 573 recorded with class and hint geometry |

One connection closed - net 5, by `pour_0_component_anchor` from a **component**
offer - giving **135 unrouted / 321 pad groups / 6 210 tracks / 209 vias**. The run
ended at its 2400 s boundary: the runner clamps each engine call to the remaining
budget, so the call that crossed the deadline was reaped and the run reports
`EngineServerCrashed` with the best board already checkpointed and promoted.

**Acceptance.** Best board `6c4f8ab81b83…`, generation
`1790471639479510000-6c4f8ab81b83-4969cfa5`; staged against the canonical original
as generation `1790472116906669000-6c4f8ab81b83-1d75038c` in
`phase6_final_gates/run_via1`. Native gate: 0 added relevant (7 930 -> 7 930;
connectivity 219 -> **200**, total 8 149 -> 8 130). Terminal: 335 -> **321**
clusters, 1 060 terminals, 0 splits, 0 merged. Complete CLI 9.0.8 **verified**: raw
unconnected 149 -> **135** (-14), `track_dangling` 70 -> 65, every other class
exact. The earlier accepted generations and their pointers are untouched.

The via-free strategy did **not** close a connection in this run (all 55
evaluations failed to connect rather than being refused), which is the honest
input to the next step: the prefilter-only positions need to be chosen from the
DRC's own refusal geometry - and, for the pair checked by hand (its geometry is
in the private evidence `[coordinates redacted]`), no legal via was found within
1.4 mm on either side. That is a physical placement limit for that hop, not a
rule to change.

`bash tools/reliability/check_phase.sh --strict` -> exit 0, 396 unit + 103 native,
no skips; `check_engine_patches.py` byte-equal; `tools/check_separation.py` 4/4.

## 2026-09-27 — via-state corrections and the bounded continuation trial

Root's review of the accepted board found remaining correctness defects in the via
policy; this cycle fixes them and runs one bounded trial of the continuation
search, then stops.

**Five corrections, each with the test that would have caught it.**

* **A -> B -> A no longer keeps B's size.** `_via_size_for_net` was answering from a
  per-net cache ("this net was applied once"), which cannot know what the router is
  holding *now*. It re-resolves on every entry and applies only when the resolved
  size differs from the one in force; the regression test asserts the exact setter
  sequence `[0.6, 0.9, 0.6]` and that the session's record names A at the end.
* **An invalid constraint is not "no constraint".** `resolve_via_size` used to read
  a non-finite or negative floor as `0.0`. `_floor` now keeps an explicit `0.0`
  (KiCad's "no constraint") and reports anything absent, non-numeric, non-finite or
  negative as an invalid constraint that makes the size **unusable**, with the
  offending fields named in `invalid_constraints`.
* **Adopted sizes keep native precision.** Rounding to four decimals could land
  below a declared minimum; the exact floats are what reach the engine and the
  evidence, and a floor of `0.30000000004` is pinned by test.
* **Any setter failure quarantines.** The engine exposes no readback for its active
  sizes, so a raise from either setter is an unknown outcome - whether or not the
  call "should" have been a no-op. A setter that mutates and *then* raises is tested
  explicitly.
* **An unusable size refuses via mutation.** A plan whose steps include a via is
  refused with `via_size_unusable` rather than routing with whatever the router
  happens to hold; same-layer plans are unaffected. The public docstrings now say
  the adoption is a starting point and that lawfulness is established by the
  transaction DRC and the fresh whole-board gate.

**The continuation search.** `_via_free_positions` now seeds from the target, the
midpoint, the start, and the positions the gate has already refused that pair at;
spreads a 3 mm disc at 0.15 mm pitch with the probe and candidate budgets shared
per anchor; reserves slots for spots whose far-layer copper is proved to be the
**target's own component** (the via itself closes the hop); and records probes,
bands, truncation, continuation counts and an explicit note that a sampled disc is
a sample. The component-pair source now takes each component's nearest neighbours
over the whole net rather than over the rotating window - a window-relative search
had proposed two components 85 mm apart - and offers an optional gap ceiling,
**off by default** because one net on the frozen board genuinely spans 86 mm.

**Trial (2400 s, `phase8_routing/run_via2`, no further campaigns).** 266 attempts,
1 434 distinct plans over 141 pairs; offer sources ratsnest 941 / component 462 /
substitution 37; `via_free` 107, `drc_escape` 75, `via_jog` 59, `drc_avoid` 38;
39 via searches with 28 526 prefilter probes and **60 continuation-proved
positions**; 270 refusals with class and hint geometry; 1 391 rollbacks
`restored`, 7 `unchanged`, and **one `retained_unknown`** - which stopped the run.

That event is the cycle's most useful finding. Net 74's outstanding connection is
genuinely 86.05 mm (the engine's own ratsnest draws it). A `pour_0_component_anchor`
plan applied 101.18 mm of copper in two steps, the acceptance DRC call then failed
(`drc_unavailable`), and the rollback could not be verified, so the session
quarantined itself and the run stopped with `session_quarantined`. Fail-closed is
the right behaviour, and **nothing was promoted**: the run's accepted artifact is
still `6c4f8ab81b83…` (135 unrouted / 321 pad groups / 6 210 tracks), whose
fresh-child native gate, fresh two-board terminal partition and complete CLI 9.0.8
verdict were re-run for this report and all pass. The open question - why a large
transaction's rollback cannot be verified after a DRC-unavailable failure - is the
concrete next diagnostic, not a rule to relax.

`bash tools/reliability/check_phase.sh --strict` -> exit 0, 398 unit + 116 native,
no skips; `check_engine_patches.py` byte-equal; `tools/check_separation.py` 4/4.

## 2026-09-27 — the acceptance DRC reaped at the run's own time limit

The open question above is answered, and it is not an engine defect.
`RoutingRunner._native_timeout_s()` bounds every native call by the run's
remaining budget (`min(engine_call_timeout_s, time_limit_s - elapsed_s)`), and the
loop checked `time_limit_s` only at the top of an iteration. An attempt that
started just inside the limit could therefore cross it mid-flight, and the
acceptance DRC — dispatched *after* the plan applied copper — got seconds of
allowance against a whole-board pass the profile already measured at ~41 s. The
client reaped the call at its deadline and killed the owned engine child, which is
the process holding the transaction's checkpoint; the rollback's `restore` then had
no child left, so `_restore_and_verify` could only report `restore_exception`, the
session reported `copper_state="retained_unknown"`, and the quarantine followed.
The earlier segment of the same campaign died the same way from a plain read.
Fail-closed and correct, but on a clock accident rather than on anything about the
board. Nothing was promoted.

Reproduced on a disposable copy of the accepted generation through the production
transaction path, in two deadline regimes: with the production allowance the same
copper is refused for real (`drc_regression`, verified rollback); with the
run-budget clamp it is `drc_unavailable` with `retained_unknown` and a quarantine,
and the attempt record reproduced the failing run's record on every value the run
state had kept. The copied board was byte-identical afterwards and a fresh engine
reopened it at the unchanged geometry digest — the reaped child takes its own
in-memory copper with it and does not write through. The quarantine stays either
way: "the file looks fine" is not a verified restore.

Fix. `RunnerConfig.attempt_headroom_s` (floor, default 0) and
`attempt_headroom_factor` (default 1.25) make the loop stop with
`stop_reason="time_limit_headroom"` before it scans or mutates copper when the
remaining budget cannot cover the worst whole-board DRC or whole attempt this run
has measured, with the arithmetic in `metrics["time_limit_headroom"]`. No gate is
weakened: the guard only refuses to *start* work whose verification cannot fit.
`AttemptRecord.failure_exception` and `AttemptRecord.rollback_detail` now carry the
failure's own words (bounded and compacted) into `run_state.json`, so an
unverifiable rollback is diagnosable instead of re-derivable; older checkpoints
load unchanged. `RunnerConfig.priority_nets` lets a trial brief look at named
geometry first on every scan without starving the round-robin.

Mechanism, reproduction, tests and limits:
[docs/agent-work/reliability/phase8/DECISION.md](docs/agent-work/reliability/phase8/DECISION.md).

`bash tools/reliability/check_phase.sh --strict` -> exit 0, 406 unit + 116 native,
no skips; `check_engine_patches.py` byte-equal (the pinned C++ is untouched);
`tools/check_separation.py` 4/4.

## 2026-09-27 — bounded engine lease (correction to the entry above)

Review found the headroom guard above is a *heuristic*: it compares the run's
remaining budget against the worst DRC or attempt seen so far, so an attempt that
starts with apparently enough room and then needs much more than any previous one
could still have its acceptance DRC dispatched under what was left of the budget —
and reaping that call kills the child holding the checkpoint. The guard is now the
scheduling half of the fix, not the guarantee.

The guarantee is a bounded engine lease. The run has two budgets that buy
different things: the soft scheduling budget (`time_limit_s`) decides whether to
start work, and the hard operational ceiling (`engine_call_timeout_s`) bounds each
individual call. While a lease is held, every native call is bounded by
`min(engine_call_timeout_s, lease remaining)` and not by the soft budget, so a
transaction whose DRC or rollback outgrows its history still finishes and is
verified. A lease covers the whole engine-touching part of an iteration — the
digest, the scan, the outstanding count, and the attempt they select — and the
run's closing verification of the board it is about to report. One window opens
per unit of work and nesting cannot extend it, so a sweep cannot compound its
overrun.

The bound is two windows, not one. The closing verification opens its own window
after the attempt's has closed, so a run that crossed its soft deadline inside an
attempt and then spends time on the closing DRC overruns by the sum of the two.
The iteration's scan window and its attempt window cannot both lie past the
deadline — `_headroom_note` refuses to start an attempt once the deadline is spent
— which leaves at most one iteration window plus the closing window.
`metrics["engine_leases"]["max_overrun_s"]` is the total actually observed, and it
can exceed `max_window_s`, the largest single window; saved-artifact gates run
outside any lease under the remaining soft budget and add no overrun of their own.
`RunnerConfig.transaction_lease_s` (default 300 s) is that window's floor, widened
to `engine_call_timeout_s` and to twice the worst measured DRC or attempt; 0
disables the lease and restores the old clamp for callers who would rather have a
hard wall-clock stop. A lease that *expires* is a hard-bound violation, not a
scheduling decision, so the existing fail-closed quarantine still follows.

Stopping changed with it. The pre-work guard keeps `time_limit_headroom`
(`attempt_headroom_s` is now a 30 s floor, not 0) and the candidate sweep stops
starting new transactions once the soft deadline passes; an attempt already in
flight is allowed to finish and the loop then stops with
`time_limit_completed_attempt`. The same lease covers the closing DRC, which under
the plain clamp had a zero allowance at exactly the moment a bounded run finishes
— the shape the previous segment of the campaign recorded as
`exception:EngineServerCrashed` out of `get_pad_groups`. Transplanted history is
now labelled in the state itself (`metrics["transplant"]["inherited_records"]`, and
a note that only later records came from this run), so an inherited outcome is
never reported as this cycle's.

Verified on the frozen board by one short targeted run (no routing campaign): the
soft limit was crossed by an attempt on purpose, and the run ended
`stop_reason="time_limit_completed_attempt"` with `engine_leases = {granted: 5,
expired: 0, max_overrun_s: 49.656}`, a proved rollback on the attempt record, and a
completed closing DRC. A deterministic fake-clock regression runs the same
scenario on a double whose DRC is accounted against the runner's own allowance
callback, with a negative control (`transaction_lease_s=0`) that shows the same
overshoot reaping the live checkpoint, a case proving a call past the hard ceiling
still quarantines, and cases pinning the allowance and the no-extension property.

`bash tools/reliability/check_phase.sh --strict` -> exit 0, 413 unit + 116 native,
no skips; `check_engine_patches.py` byte-equal; `tools/check_separation.py` 4/4.

## 2026-09-28 — phase 9: failure taxonomy, clearance search, coverage

* Added `tools/reliability/failure_taxonomy.py`: every ratsnest edge on one board
  generation classified exactly once, with the categories summing to the edge
  count, edge count kept apart from the component-pair count, attempt history
  joined only by the canonical pair key or native component identity on the same
  digest, and a public view that carries no net name, coordinate or refusal
  position. Contract in `tests/agent/test_failure_taxonomy.py`.
* Added the violation-centred clearance search (`clearance_search_probes`, the
  `drc_clear` candidate family) and moved the refusal-derived candidates ahead of
  the contextual pour/obstacle families, because the runner truncates to
  `candidate_limit` and the evidence families were never reached: over 1 318 s on
  the V3 board, 36 pairs carried recorded violations and zero refusal-derived
  candidates were evaluated. `RoutingRunner._refusal_unanswered` completes it by
  making an unanswered refusal outrank an untouched pair in the selector.
* Added fresh-first coverage: `scan_net_pairs(..., fresh=...)` plus
  `AttemptHistory.records_on` / `worked_on` and `RunnerConfig.coverage_first`.
  Coverage counts records, not real attempts; the first version used the narrower
  measure and 57 of 64 new records landed on pairs that already had history.
* Ran one bounded campaign over the accepted V3 generation (1779.5 s across two
  segments, zero planner requests) plus two short targeted verifications and one
  calibration segment. Zero closures; `best_board_sha256` and the accepted
  generation are byte-identical, and nothing was promoted. The campaign did move
  reach: 3 → 116 distinct pairs in the same window, and 15 previously-unattempted
  connections received their first attempt.
* Taxonomy of the accepted board: 135 edges, 639 component pairs. As found —
  35 `drc_regression`, 42 `connection_not_verified`, 26 copper-absent anchors, 6
  unnamed-net anchors, 26 capped/unattempted; 79 attempted, 56 not. After the
  segments: 43 / 49 / 26 / 6 / 11; 94 attempted, 41 not.
* `bash tools/reliability/check_phase.sh --strict` -> exit 0, 451 unit + 116
  native, no skips; `check_engine_patches.py` byte-equal (engine pin untouched);
  `tools/check_separation.py` 4/4; `git diff --check` clean. Nothing committed or
  pushed in this entry.

## 2026-09-28 — phase 10: attributed refusals, two aimed families, one campaign

* Renamed the taxonomy category `capped_unattempted` to
  `unattempted_cap_observed`, in the tool, the meaning table, the tests, the
  phase-9 report and this file's phase-9 entry. The evidence behind it is
  run-level (the run state keeps scan counters, not a per-net breakdown), so the
  old name asserted more than it could show. Counts unchanged: 26 as found, 11
  after phase 9's campaign, 135 in total.
* Attempt records now carry `offered_pair_key`: the offered connection an attempt
  stands for, while `pair_key` stays the geometry actually attempted. A
  substitution offer (`NetPair.offered_key`) carries the edge the scan drew, the
  history join resolves an edge by offered key before falling back to component
  membership, and unattributable records are counted rather than guessed at. The
  schema is backwards compatible: an absent key means "not recorded", never an
  error.
* Added two generic, bounded, class-specific candidate families:
  `clearance_extent_probes` / `drc_extent` (a waypoint past an observed
  obstacle's own extent on the pair's legal layer, at the board's own minimum
  clearance) and `RoutingRunner._hole_escape_seeds` (via-site seeds placed from
  the actual drilled geometry near a hole-class refusal, with `pad_block_reason`
  still a prefilter and the native DRC still the only authority). Neither names a
  net, a coordinate or a board.
* `_refusal_unanswered` now uses the configured `drc_candidate_limit`,
  `drc_clearance_probes`, `drc_clearance_mm` and `drc_extent_probes` instead of
  the generator's defaults, and accounts for the two families the session
  computes - by the pair's records and by the run's own via-search evidence, so a
  search that ran and found nothing counts as evaluated.
* Diagnosed the closed-but-refused group before aiming at it: 40 replays produced
  164 clearance and 92 hole-clearance violations on layers 0/2/4/6, against zone
  (256), via (163) and track (93) items resolved through the gate's own item
  inventory. The dominant shape is the plan's own via against a foreign copper
  zone.
* Ran one bounded refusal-focused campaign from the exact accepted generation,
  zero planner requests, 1 379.9 s across two reported segments (2 471 prior
  records carried, 4 075 dropped as other-digest). 465 own records, 39 distinct
  pairs, **16 of the 43 refusal edges reached with refusal-derived plans** (327
  refusal-derived records: `drc_clear` 124, `via_free` 74, `drc_extent` 45,
  `via_jog` 42, `drc_escape` 31, `drc_avoid` 11) against phase 9's 39 evaluations
  over 7 pairs. **Zero closures.** 95 records closed the connection and were
  refused; every record's copper state is `restored`; `best_board_sha256` and the
  accepted generation are byte-identical and nothing was promoted.
* The refreshed taxonomy shows the attribution working: 29 records attributed by
  offered key, and two previously-unattempted copper-absent-anchor connections
  received their first attempt (26 -> 24), with the categories still summing to
  135. `run_refusal2` reported "0 added / 9 resolved" findings on an unmodified
  board - the native DRC is not perfectly deterministic at this size, so a delta
  is engine jitter and promotion still needs a fresh whole-board pass.
* `bash tools/reliability/check_phase.sh --strict` -> exit 0, 462 unit + 116
  native, no skips; `check_engine_patches.py` byte-equal (engine pin untouched);
  `tools/check_separation.py` 4/4; `git diff --check` clean; fresh visual QA of
  the unchanged accepted bytes (two 3D renders + four per-layer copper SVGs).
  Nothing committed or pushed in this entry.

## 2026-09-28 — phase 11: the zone question, researched and returned unanswered

* Phase 11 was chartered to build a zone-aware candidate prefilter: given a point
  and a copper layer on the live board, is that point on filled zone copper, and
  is it the candidate's own net or another net. Phase 10's own diagnosis is what
  asks for it - the refusals there are dominated by the plan's own via landing
  against a foreign pour, and neither of its two families models a zone.
* The charter carries an explicit gate: change the mirrored wire/engine API or
  `patches/engine/` only if research proves no reliable read-only query exists,
  and in that case stop and return the proposed minimal contract to the
  orchestrator before implementing it. Research reached that branch.
* Verdict: the pinned engine exposes **no trustworthy read-only
  point-in-filled-zone query**. Three independent facts, each sufficient:
  the PNS world sync admits copper pours only as rule-area keepouts with a null
  net, so the pours are not in the router's obstacle model at all
  (`router/pns_kicad_iface.cpp`, `syncZone`); the zone row from
  `get_board_items()` carries a bounding box and no geometry, and reports
  `UNDEFINED_LAYER` for a multi-layer zone, which the one existing zone consumer
  skips silently (`pcb_world/agent/observations.py`); and staleness is
  unreportable by KiCad's own design - the sexpr parser clears `NeedRefill()` on
  load and `zone.h` says a cleared flag "does not imply filled areas are up to
  date". The bound surface was enumerated rather than sampled: 110 entries, of
  which only `get_board_items()` and `get_keepouts()` touch zones.
* Measured the only available proxy rather than asserting it was weak. A
  read-only survey of the accepted generation through the pinned KiCad 9.0.8
  build: 105 copper zone-layer fills, 130 fill outlines, **0 holes** (KiCad
  fractures fills), 64 island flags all on netless artwork zones, 92 of 105
  copper zones `IsFilled()`, 0 `NeedRefill()`. The bounding-box test - exactly
  what `get_board_items()` supports - flags 74.8% of sampled points on every
  copper layer against 50.8%-67.0% true fill coverage. It blurs by 12%-47% and
  cannot separate own-net from foreign pour, so it is not a prefilter.
* Considered and rejected three ways to reach the goal without an interface
  change: the bbox proxy (measured above); a new Python `.kicad_pcb` zone-fill
  parser (needs its own arc-aware point-in-polygon, duplicates KiCad's logic, and
  describes the file while candidates are evaluated in memory); and importing
  the build tree's `pcbnew` SWIG module into the environment process (used
  read-only for the survey, but as a shipped path it loads a second copy of the
  GPL library and a second board across the boundary the workspace keeps).
* Proposed the minimal contract and stopped: a read-only `hitTestZones(queries)`
  returning per point and copper layer `in_outline`, `in_fill`, `fill_net_code`,
  `outline_net_code`, `is_island`, `in_keepout`, `keepout_flags` and
  `fill_current`, with unknown as a first-class answer the caller passes through.
  It would add one wire type to both `wire.py` copies, one wrapper and the
  advisory prefilter with tests. The proposal is in
  `docs/agent-work/reliability/phase11/RESULT.md` and needs the orchestrator's
  decision because it is an interface change.
* No engine, wire, patch, test or tool file was modified; `patches/engine/`
  reproduces the pin byte-for-byte. No campaign was run - with no prefilter there
  is nothing to evaluate - and nothing was promoted: the accepted generation
  `1790471639479510000-6c4f8ab81b83-4969cfa5` re-hashes to
  `6c4f8ab81b83…f593477cf1b`, unchanged, and its pointer is untouched.
* `bash tools/reliability/check_phase.sh --strict` -> exit 0, 462 unit + 116
  native, no skips; `tools/check_separation.py` 4/4 (wire copies identical);
  `check_engine_patches.py` byte-equal; `git diff --check` clean. Nothing
  committed, staged or pushed in this entry.

## 2026-09-28 - phase 11: the zone query, implemented and bounded

* The research pass on this phase had established that the pinned engine could
  not answer "what zone copper covers this point, and whose net is it", and had
  returned an interface proposal instead of code. The review approved that
  interface with five amendments: return every covering zone rather than one
  hiding net; no boolean named for currency; advisory and ranking-first; real
  geometry rather than a centre point; and explicit tests for overlap, void,
  layer span, keepout, unfilled, no-zone and older engines. All five are
  implemented and pinned.
* New engine patch `patches/engine/0003-zone-point-query.patch` (four files):
  the read-only `get_zone_point_hits()` accessor, the `ZonePointHit` /
  `ZonePointResult` mirror types in declaration order, the binding, and the
  protocol mirror in `engine_server/wire.py` (byte-identical to
  `pcb_world/engine/wire.py`). `check_engine_patches.py` proves pin + `0001` +
  `0002` + `0003` reproduce the engine tree byte for byte, and no existing
  accessor changed shape.
* The accessor answers per query and copper layer with every covering zone:
  identity, net, `in_outline`, `in_fill`, the engine's own distance to that
  layer's *filled* copper, the island flag, rule-area keepout flags, and
  `fill_provenance` (`loaded_unverified` / `no_fill` / `unknown`). It is
  read-only - no mutation, no checkpoint, no resync, callable mid-session - and
  it reports a multi-net overlap as `fill_multi_net` rather than naming one net.
  No boolean claims currency: KiCad clears its refill flag when a board is
  parsed and keeps no fill hash across processes, so the stored fill cannot be
  proved to match the loaded rules.
* Two engine facts found by running it are now pinned by tests: the layer check
  must be `IsCopperLayer(...)` and not a `F_Cu..B_Cu` range (this enum orders
  the stack `F_Cu=0, B_Cu=2, In1_Cu=4`, so a range check silently refused every
  inner layer), and an empty fill poly set must be reported as `no_fill` rather
  than as KiCad's "no segments" distance sentinel, which read as 2 147 mm.
* New advisory prefilter `pcb_world/agent/zone_coverage.py`: a margin derived
  from the board's own clearance plus the wider of track half-width and via
  radius; a per-point state (`none` / `own` / `foreign` / `mixed` / `unknown`)
  where unknown passes through untouched; samples taken from the candidate's own
  waypoints plus a bounded, deterministic sample along its start->target corridor
  on both copper faces; and a cache that lives as long as the zone fill cannot
  change (routing never re-pours) and is dropped by an explicit invalidation or
  a `zone_fill_epoch` bump. `RoutingRunner._zone_ranked` is a stable partition
  that moves foreign-pour candidates behind the rest and removes none, and the
  run records the whole audit under `metrics["zone_prefilter"]`.
* One bounded campaign (`phase11_routing/run_zone1`) from the exact accepted
  generation, zero planner requests, the phase-10 balanced budgets
  (`--candidate-limit 12 --drc-probes 8 --incremental-drc`), 2 936 inherited
  records. It ran 704.9 s and stopped on `no_progress` (stagnation 25): 25 pairs
  ranked, 342 candidates checked and 289 ranked late, 1 328 point queries against
  18 060 cache hits, 284 own records over 25 distinct pairs, all `restored`, 54
  closed and refused, **zero accepted**.
* What that changed, measured: candidates are truncated to `candidate_limit`
  after ranking and these pairs offer 13.68 candidates each on average, so 231 of
  the foreign-pour candidates were still attempted inside the window and **58
  were ranked out of it and spent no transaction** - none of them a duplicate,
  so all 58 are plans the run would otherwise have tried. The prefilter changed
  which plans the sweep spent its budget on without removing any plan or
  touching any acceptance decision. Nothing closed, so no promotion gate ran:
  the accepted generation re-hashes to `6c4f8ab81b83...f593477cf1b`, its pointer
  is untouched, and the refreshed taxonomy reports `unchanged: true` with its
  categories still summing to 135.
* Limits stated in the report, not glossed: the campaign is not evidence the
  prefilter *helps* (no control run, zero closures either way); point samples are
  not a path proof, so only positive contact is ever used and only for ranking;
  the run's own 25 pairs were substitution offers rather than the 43 refusal
  edges, which the inherited history had already answered under these budgets, so
  a ranking-only prefilter had no new plan to offer them; and the DRC's known
  jitter (7 findings resolved on an unmodified board) is unchanged.
* `bash tools/reliability/check_phase.sh --strict` -> exit 0, 472 unit + 127
  native, no skips; `tools/check_separation.py` 4/4 (wire copies identical);
  `check_engine_patches.py` byte-equal across three patches; `git diff --check`
  clean. Nothing committed, staged or pushed in this entry.

### Phase 11 correction cycle (Astra review)

* **A real defect, found by review and fixed.** `ZoneCoverage` cached the
  *classified* verdict under `(point, margin)`, but a verdict carries
  `state`/`nets`, which depend on the net being routed: the second pair to ask
  about the same copper was handed the first pair's own/foreign answer and
  mis-ranked. The cache now holds the engine's net-independent row (plus
  `_Unresolved` markers for points the engine cannot answer) and the verdict is
  derived on every call. Regression test
  `test_one_point_is_own_for_one_net_and_foreign_for_another` asks one point at
  one margin for two nets and asserts opposite verdicts across a cache hit with
  no extra engine row; both invalidation paths stay covered by
  `test_cache_lifecycle_and_refill_invalidation`.
* **The prefilter is opt-in now.** `RunnerConfig.zone_prefilter` defaults to
  False: it changes which plans a sweep spends its budget on and no control run
  has yet shown that change to be an improvement, so a run must ask for it. The
  campaign driver gained an explicit `--zone-prefilter`, kept
  `--no-zone-prefilter` for a paired control, made them mutually exclusive, and
  routes both through `zone_prefilter_enabled()`. Verified: the driver's parser
  maps `--zone-prefilter` to True, `--no-zone-prefilter` and a bare command line
  to False, and refuses both together; a short private run with the flag reports
  `enabled: true`, 2 pairs ranked, 27 of 37 candidates ranked late, 42 engine
  queries against 140 cache hits, board hash unchanged.
* **"Never drops a candidate" was overstated and is corrected everywhere.** The
  ranker removes nothing from the list it is handed, but the sweep truncates to
  `candidate_limit` after ranking, so a plan moved late can miss the attempt.
  `zone_coverage.py` (module docstring, `CandidateZoneRisk`, `candidate_risk`),
  `runner.py` (the config field and `_zone_ranked`), the engine patch README, the
  phase-11 plan, this history, the changelog and the phase-11 report now state
  the two claims separately, and
  `test_a_ranked_late_candidate_can_still_miss_the_truncated_sweep` pins both
  halves.
* **Public/private scan re-run.** The phase-11 entries in this file, the
  changelog, the phase-11 plan and report carry no board-specific coordinates,
  net names, layer references or rule values; the margin basis, the campaign
  digest and the per-pair audit stay in the private tree.
* `bash tools/reliability/check_phase.sh --strict` -> exit 0, 473 unit + 129
  native, no skips; `tools/check_separation.py` 4/4; `check_engine_patches.py`
  byte-equal across three patches; `git diff --check` clean. Nothing committed,
  staged or pushed in this entry.

## 2026-09-28 - phase 12: a measured via opening, and what it could not reach

* Astra accepted phase 11 as an opt-in advisory capability with no board closure
  claimed, and chartered this phase to stop *ranking* old candidates and start
  **placing** the layer transition: a small family that drops the via at a spot
  the zone geometry measured as having room. The 43 closed-but-DRC-refused edges
  from phases 9 and 10 are the target.
* Research first, because the brief forbids assuming a waypoint controls via
  placement. `_build_plan` expands a layer-changing waypoint into `make_via(x,
  y)` at the waypoint's own coordinate, and `make_via` routes to it with
  `arrive_tol_mm` equal to the via's radius and `require_via=True` - so the
  guarantee is "a via within one via radius of the point", and the pre-check
  models pads and holes, never zones. Two consequences: a clear spot is only
  worth what its *approach* is worth, because the router cannot see pours; and
  the arrival tolerance is real.
* **A defect fell out of that research and is fixed.** `make_via` finishes the
  route, so a plan's trailing line to the same point ran against an idle session
  and raised a `KeyError` on an unmapped head layer; every layer-changing
  waypoint therefore failed unless the via alone closed the connection, and no
  test in the tree had ever passed a waypoint on a different layer. The session
  now emits a `restart` step (re-open the route at the via's point on the new
  layer) after each layer change and models the phase the executor sees.
  Measured after the fix: the via lands at the requested coordinate with 0.0000 mm
  error across six corridor positions, and a blocked approach fails the via step
  cleanly with the board restored.
* New family `kind="zone_gap_via"`: `zone_coverage.find_openings()` samples a
  cross-layer pair's own corridor, queries both faces a through via touches in
  one batched call, and offers a point only when every covering zone resolved,
  no foreign pour is closer than the board's own clearance + via radius on
  either face, no observed foreign track/via/pad is closer, and the whole
  approach on the start layer is clear. Own-pour contact sorts first, openings
  stay a band apart, and the count is capped. Off by default
  (`RunnerConfig.zone_gap_via = 0`); the rationale string says the opening is
  measured, not proved legal.
* **Carried-forward review fix.** `ZoneCoverage.verdicts` zipped indices with
  engine rows, so a short IPC answer silently dropped points from
  `candidate_risk`. A row count that is not one per query now marks the batch
  unknown, a `None` row marks that point, and the return path guarantees one
  verdict per point in order. A regression test covers short, long, holed and
  well-formed answers.
* **Trial, and its honest limit.** A whole-board sweep with the family enabled
  (26 nets of the 43 edges pinned, phase-10 budgets) produced 275 records in
  581.9 s, **0 accepted**, board byte-identical - and the family never fired,
  because the selector's pairs for those nets were same-layer pairs. A matched
  per-edge trial then ran the cross-layer edges with the same generator, session
  and budgets, each arm on its own wall-clock budget: 2 of 16 edges reached,
  **0 measured openings** and identical arms on both (3 candidates each, all
  `drc_regression`). For those short, pour-dense hops there is no spot with a
  clear approach and clear pour room, which is the same physical limit earlier
  phases recorded by hand - so the family correctly declined rather than
  offering a spot it could not stand behind. Nothing promoted; the accepted
  generation re-hashes unchanged.
* **Privacy.** The coordinate examples the review named, plus two more of the
  same kind in the phase-5 checkpoint, are redacted in place with an explicit
  `[coordinates redacted]` marker pointing at the private evidence - a
  deliberate, documented exception to append-only. Earlier phases' public
  documents still carry board-specific *rule values*; those are flagged to Astra
  rather than silently rewritten, because they are part of phase 10's accepted
  evidence.
* `bash tools/reliability/check_phase.sh --strict` -> exit 0, 475 unit + 132
  native, no skips; `tools/check_separation.py` 4/4; `check_engine_patches.py`
  byte-equal across three patches; `git diff --check` clean. Nothing committed,
  staged or pushed in this entry.

## 2026-09-28 - phase 13: exact-edge selection, a cheap screen, one matched trial

* Phase 12's own trial taught two things: a **net** pin is not an edge pin (the
  sweep spent its budget on same-layer pairs of the pinned nets, so the family
  never produced a candidate), and a matched pair of arms costs about five
  minutes per edge, so screening must come first and cost almost nothing. Astra
  accepted phase 12's harness behaviour and wrote the checkpoint this phase
  started from.
* **Generic exact-edge selector.** `observations.edge_key()` gives one canonical,
  direction-tolerant identity per connection (rounded to 3 decimals, endpoints
  sorted), and `RunnerConfig.priority_edges` pins those identities ahead of net
  pins in both the scan rotation and the coverage sort. A malformed pin matches
  nothing rather than everything. Nothing about which pairs exist changes.
* **Generic read-only screen.** `zone_coverage.screen_openings()` answers per edge
  with `openings`, `none`, `same_layer`, `unknown` or `budget`, and
  `screen_summary()` reduces a set of them to counts with no coordinates.
  `tools/reliability/screen_zone_openings.py` exposes it with a
  `--public-summary` form. The test asserts the board is untouched: no via, no
  track, no route. A screening that ran out of room reports `budget` and never
  looks like a screening that found nothing.
* **Screening the 16 cross-layer refusal edges** - selected by exact key from the
  phase-9/10 taxonomy - took **0.78 s** and found **4 edges with exactly one
  measured opening each**, 12 with none, and none same-layer/unknown/budget. Each
  opening is at least the board's own clearance plus via radius from foreign pour
  copper on every face the via touches and has a clear approach on its start
  layer. The other twelve have no such spot in the sampled corridor.
* **Matched ON/OFF trial on those four**, same generator, session, budgets and
  ordering, each arm on its own 90 s budget, zero planner requests: 505.5 s,
  **0 accepted in either arm**, accepted generation byte-identical and the
  pointer untouched. The family offered one opening per edge; **two were
  executed and both failed** (one `drc_regression`, one
  `connection_not_verified`) and **two were never evaluated** because the two
  `direct_*` candidates ahead of them consumed the arm budget. Outcome counts
  across both arms: `drc_regression` 10, `connection_not_verified` 11, per-arm
  budget 3.
* **Public/private boundary closed.** The board's own design-rule values were
  quoted in older public reports and history; eight statements across seven files
  are now redacted in place with an explicit `[rule values redacted]` marker -
  deliberately narrow, so measured distances and medians were left alone and no
  conclusion, count or verdict was reworded. Every original line is preserved
  verbatim in the private `phase13_routing/privacy_redactions.json`. This is a
  documented exception to the append-only convention: the privacy rule outranks
  it, and nothing was published or committed either way.
* `bash tools/reliability/check_phase.sh --strict` -> exit 0, 476 unit + 134
  native, no skips; `tools/check_separation.py` 4/4; `check_engine_patches.py`
  byte-equal across three patches; `git diff --check` clean; the accepted
  generation re-hashes to `6c4f8ab81b83...f593477cf1b`. Nothing committed,
  staged or pushed in this entry.

## 2026-09-28 - phase 14: refusal-aware ordering, and openings that got their turn

* Phase 13 left one job unfinished: two of the four offered openings were never
  executed, because the two direct candidates sit ahead of the family in the
  generated order and on that copper a single direct transaction costs about as
  much as a whole arm budget. The screen is cheap; the ordering decides whether
  its answer is ever used. Astra accepted phase 13's selector, screen and privacy
  redaction.
* **Opt-in, generic, evidence-driven ordering.** With
  `RunnerConfig.zone_gap_via_priority` on, a pair whose exact edge carries
  same-generation prior DRC-refusal evidence has its `zone_gap_via` candidates
  moved ahead of the direct candidates that the same evidence names, before the
  sweep's `candidate_limit` truncation. The move is minimal and deterministic -
  each opening that sits after the first already-refused direct candidate moves
  to just in front of it, one at a time - so the non-refused direct plan, every
  other family and the original order inside each group keep their positions, and
  nothing is removed. Evidence from another board digest is ignored rather than
  inherited, a substitution attempt still binds to the edge it was made for, and
  a connection with no evidence about it never enters the path. Off by default.
* **Screen provenance.** `tools/reliability/screen_zone_openings.py` now reports
  the board's SHA256 (plus the project's and, when given, the rule file's)
  instead of null: a screen is evidence about exactly one generation, and a
  missing file returns no hash rather than an invented one.
* **Matched trial, fresh session per arm, per-candidate budget.** Four edges with
  openings, ON/OFF arms, same board, rules and budgets, zero planner requests,
  737.9 s. The policy reordered three edges (two refused direct plans demoted
  behind the opening each) and **the opening was executed on those three** - the
  exact cases phase 13 could not reach. All three were refused by the native DRC
  (`routing_failed` / `drc_regression`), the following direct plan was refused
  too, and **nothing was accepted in either arm**: 0 of 8 ON candidates, 0 of 8
  OFF. Every one of the sixteen executions rolled back cleanly
  (`copper_state=restored`, `rollback_verified=true`), so the accepted generation
  is byte-identical and no promotion gate ran. The fourth edge has no
  same-generation DRC-refusal evidence, so the policy correctly declined to
  reorder, the family fell outside `candidate_limit`, and that opening is
  reported as not executed - a truncation, not a timeout, and not a verdict.
* **Diagnosis, and the next avenue.** *Corrected after review - see the next
  entry.* The first version of this diagnosis guessed at a corridor cause from
  the trial's own output, which had read the wrong evidence fields. The
  corrected measurement is in the correction entry below.
* `bash tools/reliability/check_phase.sh --strict` -> exit 0, 479 unit + 135
  native, no skips; `tools/check_separation.py` 4/4; `check_engine_patches.py`
  byte-equal across three patches; `git diff --check` clean; the accepted
  generation re-hashes to `6c4f8ab81b83...f593477cf1b`. Nothing committed,
  staged or pushed in this entry.

### Phase 14 correction cycle (Astra review)

* **Two reporting defects in the private trial, both real.** The trial read
  `evidence["violations"]` for DRC classes, but the session records the gate's
  verdict under `evidence["drc_delta"]` - so it printed empty classes for three
  records that said `drc_regression` - and it read `result.connected`, which is
  recomputed *after* the rollback, instead of `evidence["connected_before_refusal"]`,
  which the session records before restoring. The script now uses the
  authoritative fields. Nothing else about the trial - the counts, the ordering,
  the rollbacks, the unchanged generation - was affected.
* **DRC evidence retrieval fixed and tested.** `DrcDelta.to_evidence()`'s rows
  now carry the `item_a`/`item_b` identities that caused each finding, not only
  the class and position. Without them a refusal is a class and a coordinate: a
  clearance violation cannot be told apart from a via landing in a pour, a track
  crossing a pad, or any other pair of items, and a report has to guess. This is
  an evidence-only change - no gate, no acceptance path - and it is pinned by a
  test that asserts a refuted finding names both items.
* **The three executed openings re-run in isolation** (one fresh engine and
  session each, bounded, 263.8 s): all three `routing_failed` /
  `drc_regression`, and in every case `connected_before_refusal` is **true** -
  the plan closed the connection and the gate refused the copper it closed it
  with. Every added *relevant* finding names a **zone** (a foreign plane) paired
  with copper the pre-attempt inventory could not name because the transaction
  had just created it (the new via and its trace). The classes are
  `Clearance violation` and `Hole clearance violation`, and the findings sit at
  or beside the opening - the farthest 0.51 mm away, and on one edge every
  finding is 0.000 mm from the opening, i.e. on the via itself.
* **The corridor hypothesis is withdrawn, with the evidence that replaces it.**
  The opening clears the zone query and still fails the gate because the via's
  own copper and hole are inside the clearance a foreign plane requires. The
  margin the screen applies is the board's copper clearance plus the *default
  netclass's* copper half-width, while the routed net's adopted via can be
  larger and the hole-to-copper rule is not modelled at all. The next avenue is
  therefore a read-only opening test derived from the **adopted via geometry**
  and both applicable rules - not a corridor screen, which this evidence does not
  indicate. The fourth opening remains unexecuted by policy/truncation, as
  recorded above.
* `bash tools/reliability/check_phase.sh --strict` -> exit 0, 480 unit + 135
  native, no skips; `tools/check_separation.py` 4/4; `check_engine_patches.py`
  byte-equal; `git diff --check` clean; accepted generation unchanged. Nothing
  committed, staged or pushed in this entry.

## 2026-09-28 - phase 15: the via margin is half answerable, and the screen had a blind spot

* Astra accepted phase 14 after the corrected DRC replay, and chartered this
  phase to make the opening margin rule-authoritative: the real via size for the
  net, the applicable copper clearance, and the applicable hole-to-copper
  clearance - without conflating hole-to-copper with hole-to-hole, and including
  custom `.kicad_dru` rules. Research first, read-only, and an explicit stop if
  the interface cannot answer without guessing.
* **The via size is answerable.** `rules.resolve_via_size(engine, net)` already
  resolves, per net, the effective netclass values, the board's floors and the
  size actually adopted, with a source and a usable flag - all four nets in
  question resolve cleanly. This half needs no new interface.
* **The clearances are not.** KiCad's board settings carry a hole-to-copper
  clearance and the rule language has distinct `hole_clearance` and
  `hole_to_hole` constraints, but the engine's `getDesignRules()` exposes neither
  hole rule - its list stops at a *hole-to-hole* minimum, which is a different
  rule - and the board's rule file is conditional: it carries a clearance rule
  scoped to anything touching a zone at a value stricter than the board minimum,
  plus explicit hole constraints. The applicable value is the rule engine's
  answer for a specific item pair on a specific layer, and the engine exposes no
  rule-evaluation accessor. A screen reading the board minimum would be guessing,
  so the phase **stopped at the contract gate**: no engine or wire change, no new
  screen, and the minimal `probeRules()` contract returned for a decision.
* **One hard finding, from a cheap read-only measurement.** Repeating the
  existing screen while asking **every copper layer a through via spans** - not
  just the two endpoint layers - leaves **1 of the 16** cross-layer refusal edges
  with any qualifying sample, and only **1 of the four earlier openings** still
  clear on every layer. Two of those points sit inside foreign pour on interior
  copper layers the screen never queried. That is the defect behind phase 14's
  refusals, it needs no new interface to fix, and it is reported rather than
  fixed here because a change that disqualifies three quarters of the previously
  reported openings is the orchestrator's call.
* **No transactional trial ran.** With the clearances unanswerable no opening can
  be called rule-complete, so the contract's own condition for a trial is not
  met. Nothing closed; the accepted generation is unchanged.
* `bash tools/reliability/check_phase.sh --strict` -> exit 0, 480 unit + 135
  native, no skips; `tools/check_separation.py` 4/4; `check_engine_patches.py`
  byte-equal across three patches; `git diff --check` clean; the accepted
  generation re-hashes to `6c4f8ab81b83...f593477cf1b`. Nothing committed,
  staged or pushed in this entry.

## 2026-09-28 - phase 16: the via's real layer span, and one opening left standing

* Astra reviewed phase 15, deferred the proposed rule-query API, and asked for the
  finding it accepted instead: the opening screen asked only about the two copper
  layers a plan changes between, while a via's barrel and hole exist on every
  layer it spans. This phase fixes the screen and re-runs the whole chain.
* **`zone_coverage.via_layer_span()`** reads the layers a via spans from the
  engine's own layer map and order and returns them as human layers 1..N, or
  `None` when the map is missing or disagrees with itself. `find_openings()` now
  asks every one of those layers for every corridor sample, and a sample is
  offered only when every layer resolved and none is foreign or mixed. Unknown
  stays fatal: an unreadable span, an inconsistent map or an unresolved zone on
  any spanned layer refuses the opening instead of skipping the layer that might
  have blocked it. The span is the whole stack - the harness places a through via
  - which is a superset of any partial via pair and can therefore only refuse
  more. The screen reports `layers_checked` per edge, and a layer-changing leg's
  own duplication is deduplicated so the query count stays at one per (position,
  spanned layer).
* **Re-screening the sixteen cross-layer refusal edges**, same lower-bound margin
  as before (coverage changed, not the numbers), through the tool, which also
  reports the board SHA256 and the layers asked per edge: **four openings become
  one**, and the 4 -> 1 prediction is verified. The three that disappeared were
  clear only on the layers the screen had asked about.
* **The remaining candidate was executed.** It has no same-generation
  DRC-refusal evidence, so the generic refusal-aware policy correctly does not
  promote it; instead the same generator and budgets ran with a wider candidate
  window (`candidate_limit 3`), fresh engine and session, exact accepted board,
  zero planner requests. All three candidates ran and all three failed with
  `connection_not_verified` - the plan never closed the connection, so there is
  no DRC delta to read and `connected_before_refusal` is absent. Every attempt
  restored its copper with the rollback verified. **0 accepted**, so no promotion
  gate ran and the accepted generation is byte-identical.
* **The deferred rule API would not help these sixteen edges.** The three that
  closed and were refused on clearance and hole-clearance findings no longer have
  a layer-complete opening to measure, and the one that does have an opening
  fails on connectivity, which no rule value changes. That is stated as the
  finding rather than as a reason to build the interface.
* `bash tools/reliability/check_phase.sh --strict` -> exit 0, 483 unit + 138
  native, no skips; `tools/check_separation.py` 4/4; `check_engine_patches.py`
  byte-equal across three patches; `git diff --check` clean; the accepted
  generation re-hashes to `6c4f8ab81b83...f593477cf1b`. Nothing committed,
  staged or pushed in this entry.

### Phase 16 hardening (Astra review)

* **The layer map is checked, not trusted.** `via_layer_span` converted
  `max_layer` outside its guard, so malformed metadata could raise instead of
  refusing, and a repeated layer id passed a mere length agreement. The
  conversion now happens inside the same guard, and the span is refused -
  `None`, which every caller already treats as fail-closed - when `max_layer` is
  missing, non-numeric or unconvertible; when the order repeats a layer; when an
  entry is not a copper layer (the engine's ids are even and non-negative) or is
  negative; and when the map's own `human_to_board` / `board_to_human`
  converters disagree. Every failure is a refusal, never a smaller span.
* **Endpoints outside the proven span are refused.** `find_openings()` now
  requires both endpoint human layers to be in the span, so a caller's
  impossible layer cannot be reported as a measured opening, and
  `screen_openings()` reports such an edge as `unknown` with the span it did
  prove rather than as "no opening".
* **Tests, and unchanged valid-board behaviour.** New unit tests cover malformed
  `max_layer`, missing map metadata, duplicate ids, non-copper and negative ids,
  disagreeing converters, endpoints outside the span in both the finder and the
  screen, and known-good two- and four-layer maps. Re-running the screen over the
  same sixteen edges after the hardening reproduces the previous result exactly
  (one opening, four layers asked, board SHA256 equal to the accepted hash).
* `bash tools/reliability/check_phase.sh --strict` -> exit 0, 488 unit + 138
  native, no skips; `tools/check_separation.py` 4/4; `check_engine_patches.py`
  byte-equal; `git diff --check` clean; the accepted generation re-hashes to
  `6c4f8ab81b83...f593477cf1b`. Nothing committed, staged or pushed.

### Phase 17 - where a plan stops

* **The record now names the step a plan stopped at, and the reason is that it
  could not before.** One bounded replay of a recorded plan whose reconstruction
  said "the route could not be re-opened after the via" showed every step
  succeeding on the current harness, with the plan then closing and being refused
  by the DRC. Same-generation history is not same-harness history: the board
  digest proves the copper, not the code. `AttemptRecord` gained
  `failed_step_kind` / `failed_step_index` (populated by `runner._record` from the
  session's own step list, serialized by `to_dict`, read back as "not recorded"
  for a legacy or unusable value), so a future run state answers the question
  itself.
* **The taxonomy counts them, and publishes only the counts.**
  `EdgeAttempt.failed_step_kinds`, the stopping-point counts on the
  `connection_not_verified` detail, and a board-wide `failed_step_kinds`
  aggregate in the public summary. Step kinds are harness vocabulary; no net
  name, coordinate or rule value goes with them.
* **A bounded current-harness trial** on the exact accepted board, fresh state,
  zero planner requests, priority-netted to the affected nets, 1 500 s soft
  budget: 19 attempts, 109 plan evaluations, 0 accepted, 7 of the 49 target edges
  reached (42 records naming their failing step). Recorded distribution: 12
  `start`, 61 `line`, 11 `via`, 24 closed-then-refused (`drc_regression`), 1
  unanchored endpoint. Where the private reconstruction and the record both
  exist, 42 agree and 0 disagree.
* **Zero accepted, zero promoted.** Twenty-four of the trial's plan evaluations
  did close the connection and were rejected by the native DRC
  (`drc_regression`, `closed_before_refusal`, every one `copper_state:
  restored`), which is a closure its gate refused, not a plan that failed; the
  other 85 did not close it at all. Nothing was accepted, so the ladder did not
  run, the accepted pointer is unchanged and the generation re-hashes to
  `6c4f8ab81b83...f593477cf1b` after the trial.
* `bash tools/reliability/check_phase.sh --strict` -> exit 0, 494 unit + 138
  native, no skips; `tools/check_separation.py` 4/4; `check_engine_patches.py`
  byte-equal; `git diff --check` clean. Nothing committed, staged or pushed.

### Phase 18 - endpoint and anchor census

* **A read-only census of all 49 `connection_not_verified` edges asked a
  question the earlier phases could not: is the endpoint the problem, or the
  geometry?** The join is by exact offered-edge identity - the canonical key of
  net plus both anchors, order-independent - and never by net name: 49 offered,
  49 joined, 0 missing, 0 ambiguous, net code agreeing on all 49. The accepted
  generation re-hashes to `6c4f8ab81b83...f593477cf1b` before and after, the
  board geometry digest is identical, and `checkpoint_count()` moves 0 -> 0:
  no route was started, no DRC was run and no board file was written.
* **Anchor identity is answered; anchor availability is answered but for one.**
  All 98 offered anchors (49 x 2) are named as the scheduled net by the engine's
  own point-identity rule - none absent, none unnamed, none ambiguous - so this
  class is not the 26 `copper_absent_at_offered_anchor` or 6
  `unnamed_net_at_offered_anchor` edges. 97 of 98 are additionally *usable*: a
  pad or unanimous same-net cluster proves a route start and the anchor is
  outside a rule-area keepout. The single exception is one anchor of one
  coincident cross-layer edge that sits inside a keepout on its own layer.
* **Every edge has a proved substitute anchor.** `component_anchor_candidates`
  (unbounded radius, one proved anchor per native component, already-connected
  resolved against the peer) yields 402 substitutes, 304 of them not already
  joined to the peer, 4 to 22 per edge. Quantity is not the constraint; identity
  is, because a substitute may stand for a different component pair than the
  ratsnest edge drew.
* **The readable defect is clearance geometry under a measured proxy, not
  routing history.** The margin is a geometric proxy - the board's minimum
  clearance plus the adopted copper radius, sampled at the census's points and
  layers - and no DRC ran, so nothing here is called rule-clear; conditional
  custom rules and hole clearance stay unreadable (phase 15). Under that proxy,
  foreign copper (any other net, plus netless copper) sits within the margin at
  the start anchor itself for 33 edges, and no leg is clean. No margin-clear lane
  exists at any lateral offset within 3 mm for 44 of 49 edges, and none of the 34
  measurable edges has a margin-clear interior lane. All 8 cross-layer edges have
  **no** margin-clear via spot under the same proxy on the leg across every
  copper layer a through via spans.
  Verdicts: 48 `geometric_obstruction` (47 no-clear-lane, 1 direct-leg-only), 1
  `both` (that keepout anchor), 0 `anchor_identity` alone, 0 `unresolved`.
* **The complementary question was measured too.** Sampling a 1 mm x +/-4 mm
  grid around every leg and recording the largest open radius: 47 of 49 edges
  have at least one margin-clear point within that window (largest 3.99 mm), and
  2 have none. That is free space, not connectivity: an open point is not a
  continuous path, does not show that copper placed near it would pass the
  native DRC, and is not a claim that the board is unsaturated. What it does
  establish is that the declared straight-leg and narrow-band geometry has no
  margin-clear path under the measured proxy for 44 edges while margin-clear
  points exist nearby for almost all of them - so the binding constraint this
  census can see is the plan geometry, not the absence of free space. That is
  the evidence for the phase's one proposed next step: a bounded 2D
  clearance-aware search per edge, then at most one bounded transactional trial
  per edge where a *candidate* path is found.
* **Validation, and the one defect it caught.** A second script opened a fresh
  process, a fresh engine, and a raw O(items) scan - no reuse of the census's
  bucketed index or zone cache - and re-derived the join (49/49), every anchor
  identity (98/98) and every first-contact offset (49/49) with 0 failures. It
  caught a real error on the way: the first index dropped **netless** copper, and
  one leg was reported clean when a netless pad sat inside it. The index now
  keeps netless copper and flags it. A census re-run reproduces the aggregate
  exactly. The verdicts and the 3 mm lane counts are stable at 0.25 / 0.5 /
  1.0 mm sampling; the fine-grained first-contact counts are not (33 -> 35
  at the start anchor at the finer pitch) and are reported with that limit. A
  margin-zero run removes all contact, which is the honest bound: the finding is
  clearance-margin driven, and the applicable clearance where custom rules are
  conditional is still not readable through this engine (phase 15).
* **Nothing came from history.** The phase-17 seven-edge trial and the pre-field
  attempt records were deliberately not read for the classification; the census
  describes what the board offers now, not where a past plan stopped. No public
  harness file changed, so no public test was added or altered. Coordinates, net
  names and rule values stay in the private `phase18_census/` evidence tree;
  `docs/agent-work/reliability/phase18/RESULT.md` carries the aggregate account.
  Nothing committed, staged or pushed.

### Phase 18 correction cycle (Astra review)

* **Wording: the geometric proxy is no longer called "rule-clear".** The census
  runs no DRC and cannot see the applicable clearance where custom rules are
  conditional, or hole clearance (phase 15), so its margin test is a sampled
  proxy. The phase-18 entry above, `CHANGELOG.md` and
  `docs/agent-work/reliability/phase18/RESULT.md` now say *margin-clear under the
  measured proxy*, and "rule-clear" is reserved for a verdict the native DRC
  actually returned - which this phase produces none of. The public RESULT now
  states the distinction explicitly before its geometry tables.
* **Claim: free space is not a route.** The statement that 47 of 49 edges have
  an open point, and that the residual set is therefore "not saturated", has been
  narrowed in all three public files: a margin-clear point is a single point, it
  does not establish a continuous path, and it does not show that copper placed
  near it would survive the native DRC. The finding kept is the narrower one -
  the declared straight-leg and narrow-band geometry has no margin-clear path
  under the proxy for 44 edges while margin-clear points exist nearby for almost
  all of them - and the phase-19 proposal now calls a path found that way a
  *candidate*, with the native DRC still the authority on legality.
* **Chronology: the changelog insertion is audited, and the changelog is
  unchanged in structure.** The fragment Astra's review quoted -
  "by the DRC. Same-generation history ..." - is phase-17 prose that lives in
  this file's phase-17 entry above (line-anchored there since it was written) and
  is not present in `CHANGELOG.md`; the phase-18 changelog insertion carries no
  stray prior-phase text, its headings are unique, and its section boundary was
  already a blank line either side. No earlier HISTORY entry was edited or
  removed; the phase-18 changelog heading now names the phase and its date
  explicitly so the insertion cannot be misread as inheriting the entry below it.
* **No re-verification of behaviour was needed for wording.** Nothing in the
  private analysis changed: the census, validator and clear-area JSON are the
  same artifacts the results were computed from, and no test, engine or wire file
  was touched. `git diff --check` is clean.

### Phase 19 - bounded 2D path test

* **A bounded 2D clearance-aware search over the same 49 edges, read-only, and it
  found no candidate path.** Each phase-11 `connection_not_verified` edge was
  joined to the unchanged accepted board by exact offered-edge identity (49
  offered, 49 joined, 49 searched, 0 missing, 0 ambiguous). Each edge got a
  lattice over a window following its offered segment - 4 mm to each side and
  2 mm beyond each end, escalated to 6 mm / 4 mm for edges the primary window
  could not connect - at 0.25 mm pitch with both offered anchors forced onto it.
  Every copper layer is in the graph, an on-layer move stays on its layer, and a
  through via is a transition at one point that must be clear on every layer it
  spans; the four coincident cross-layer edges declare no direction to offset
  along, so their window is the same-sized axis-aligned square around the shared
  point. **0 of 49 edges produced a candidate path in any of five runs, in either
  window**, and the escalated window reproduced every count exactly.
* **The clearance test, and two item models.** A sample is proxy-clear when
  nothing foreign is inside the margin: foreign and **netless** tracks, pads and
  vias at their own shape, foreign and netless **pour fill** from the engine's
  read-only zone query, and no rule-area keepout. Own-net copper is not an
  obstacle, so an anchor on its own proved copper is not read as blocked, and a
  sample the engine cannot resolve is blocked and counted (177 such samples).
  Phase 18 modelled every pad as the circle of its longer dimension, which for
  the elongated pads this board is full of claims copper far beyond the pad's own
  extents - safe, but loose enough to refuse most anchors before any routing
  question was asked - so the search carries both that apron and the harness's
  own shape-aware extents. The apron runs block 39 and 41 of the 98 anchors and
  are the direct explanation of phase 18's "no clear lane" result, while the
  harness's own extents leave 9 anchors blocked outright - a few by netless
  copper overlapping the anchor, the rest by pour just inside the margin - and
  the other 40 edges with both anchors usable and the start and target in
  different proxy-clear regions of the window.
* **The margin is bounded, not tuned.** The declared 0.025 mm sampling allowance
  is half the independent checker's dense spacing and is carried under the
  1-Lipschitz bound, so samples that clear `margin + allowance` prove the piece
  between them clears `margin`. A separate `endpoint_relief` run holds the two
  offered anchors - exact measured points on their own copper, not samples
  standing in for the piece around them - to the un-inflated margin; without it,
  most of that run's anchor failures would be decided by the sampling allowance
  rather than by the board.
* **The search does find paths on this board where the board has room.** A
  positive control walks a deterministic 2 mm grid of candidate origins for the
  first open 5 mm strip (its position stays private); all five runs find it at
  exactly 5.0 mm with 2 corners, including the strict and phase-18-proxy runs,
  with every pour on the strip counted as foreign. The search itself is covered
  by 14 assertions on synthetic geometry where the answer is known by
  construction: an open window, a wall with a gap (walked around, longer than the
  straight line), a wall across the whole window (`no_path`), a cross-layer edge
  needing a clear via column (connected through it, `no_path` without it),
  determinism, and the corner simplification.
* **Independently checked, and the checker was exercised.** A second script in a
  fresh process with its own engine, its own zone cache and its own raw O(items)
  clearance scan re-derives each offered edge by exact key, re-identifies both
  endpoints with the engine's own point-identity rule, checks continuity and
  layer transitions, and dense-walks every retained piece at 0.05 mm at
  `margin + allowance`. With no candidate path on any of the 49 edges there was
  nothing to check there, so its dense walk was exercised on the control path -
  101 samples, 0 failures, clear margin at every sample - and the two geometry
  sources were cross-checked: the pad and via counts agree, and the track
  difference is accounted for by arc copper the file reader does not count as
  segments (the counts are private).
* **How wide each blockage is.** A supporting read-only diagnostic grows the
  reachable set from each anchor with the same move rule and reports the closest
  approach between the two sides: 4 edges at 0 mm (the sides meet at one point on
  **different layers** - the coincident cross-layer pairs, stopped only by the
  absence of a clear through-via column), 1 edge between 0.5 and 1 mm, 12 between
  1 and 2 mm, 3 between 2 and 4 mm and 29 over 4 mm. The separation is a distance
  between *proxy-clear regions* inside the sampled window: a lower bound on the
  gap the search saw, not a measure of what a real route needs, and not by itself
  a mandate for a design change. Phase 18's open question is narrowed rather than
  closed - **expanding from the 1D leg and its lanes to this bounded 2D proxy did
  not find a path**, a statement about this window, sampling and proxy.
* **No trial was run, because no edge produced a credible path.** The contract
  makes bounded transactional trials conditional on a credible path; here the
  trial machinery had nothing to test. No route was started, no DRC was run, no
  checkpoint was taken (`checkpoint_count()` 0 -> 0), no board file was written,
  and the accepted artifact and scratch copy re-hash to
  `6c4f8ab81b83...f593477cf1b` with the accepted pointer unchanged. Nothing was
  committed, staged or pushed. No public harness file changed, so no public test
  was added or altered and the strict harness gate was not re-run. Coordinates,
  net names, per-edge geometry and rule values stay in the private
  `phase19_search/` evidence tree;
  `docs/agent-work/reliability/phase19/RESULT.md` carries the aggregate account.

### Phase 19 correction cycle (Astra review)

* **The public phase-19 text carried board measurements it should not have.** The
  phase-19 plan restricts per-edge geometry and rule values to the private
  evidence, and the first public draft leaked them: the default clearance, track
  and via dimensions and the derived margin values; a pad's own dimensions and
  the pitch of the footprints it was checked against; the exact position of the
  positive-control strip; the entry slack and inventory counts of the checker
  run; and the maximum and median separation between the two blocked sides. All
  of those are removed from `docs/agent-work/reliability/phase19/RESULT.md`,
  `CHANGELOG.md` and the phase-19 entry above, which now carry aggregate counts,
  method parameters and limits only and say explicitly that the margin is derived
  from the board's own defaults with the values kept private. No measurement
  changed; no private artifact was touched; nothing was re-run, because the
  change is documentation only.
* **The "binding constraint" claim is narrowed.** The earlier text said phase
  18's open question was answered. It now says the question is narrowed rather
  than closed: **expanding from the 1D leg and its lanes to this bounded 2D proxy
  did not find a path** - a statement about this window, this sampling and this
  proxy, not a demonstration that the 1D geometry or the plan was the binding
  constraint. In the same pass, the separation counts are described as distances
  between *proxy-clear regions* inside the sampled window and explicitly not as a
  mandate for a design change, and the next-step section asks two bounded
  questions instead of recommending one.
* **The changelog boundary was audited.** The trailing line "the run stops with a
  `blocked` report naming the refusing gate" is the end of the pre-existing
  phase-5 changelog block under `#### Fixed`, not part of the phase-19 insertion,
  which ends at its own private-evidence sentence with a blank line before the
  phase-18 heading. It was left as it was: the phase-19 edit did not introduce
  it. `git diff --check` is clean and a targeted scan of the three public files
  for the removed values returns nothing.

### Phase 20 - coincident cross-layer via-column diagnosis

* **The question phase 19 left.** Four offered `connection_not_verified` edges
  have two proved endpoints that coincide in XY on different copper layers, and
  phase 19 showed their two proxy-clear sides meet at exactly that point with no
  clear through-via column there. Phase 20 asks what blocks a via column at those
  four points, whether the blocker is copper, pour, keepout, drill or missing
  information, and whether the harness has any option other than the through via
  it already places - a harness question and a physical-design question kept
  apart. All four were diagnosed on the unchanged accepted generation
  (`6c4f8ab81b83...f593477cf1b`), read-only.
* **Every column is stopped by a measured object.** Three edges are refused by
  **foreign pour fill**: the through via's barrel exists on every copper layer,
  and on at least one layer the shared point lies inside the pour, where a via
  would be placed on that pour's copper. At two of those the pour on the endpoint
  layers is also closer than the via's own radius plus the board's clearance, so
  the column fails with the sampling allowance removed - which is why phase 19
  saw those two as `no_path` (both anchors usable) rather than `blocked`. The
  fourth edge is refused by a **rule area that disallows vias** on its target
  layer, and carries a foreign pad just inside the inflated via margin on its
  start layer while that layer stays comfortably outside the track margin. Copper
  items, drill and unknown account for none of the four: the nearest via barrel
  clears the board's own hole-to-hole minimum at every point, no through-hole pad
  lies within 2 mm of any point, and every one of the 293,792 point-layer
  measurements resolved. The phase-19
  statuses reproduce from the same numbers: the two `target_blocked` edges have a
  target layer that is not clear even at the un-inflated track margin, and the
  two `no_path` edges have both anchors clear there.
* **No proxy-clear candidate anywhere in a declared bounded window.** Each shared
  point got an axis-aligned square at **0.05 mm** pitch, **1.5 mm** half-size
  escalated to **3.0 mm**, every point measured on all four copper layers through
  the engine's read-only zone query and the harness's own shape-aware item model.
  Two classes were tested: a **through** column, clear on every layer a through
  via spans (the phase-19 rule, fail closed), and a **span** column, clear only on
  the layers from the lower to the upper endpoint layer - the bracket for a
  blind/buried via the harness cannot place - each at the allowance-inflated
  margin and at the un-inflated one. Result: **0 points clear for either class, in
  either window, at either margin, and 0 unresolved.** Widening the window from
  1.5 mm to 3.0 mm changed nothing and dropping the allowance changed nothing, so
  within this declared window the refusal is the board at those points rather than
  the search's parameters. The clearances are a proxy, not native DRC authority -
  no DRC was run for this phase - and the pour distances come from the loaded
  fill, which is not verified against the current rules.
* **What the harness can place, from the engine's own source.** A capability check
  (22/22) pairs the loaded extension with its C++ by content - the engine's own
  patch-tree content hash against the stamp written beside the extension, the
  build tree named by the CMake cache, and byte identity of every router file the
  overlay provides - and then reads: the router's size settings initialise the via
  type to `THROUGH` and the headless path never re-types them; the placer derives
  its span from the layer-pair map, which stays empty, so a through via spans top
  copper to bottom copper; only the interactive GUI tool ever sets a layer pair;
  the headless interface and the harness's structured action schema expose no via
  type, layer pair or span; and the microvia minima in the rule structs are board
  metadata that no placement path consumes. The same check confirms the pad
  extent convention the phase-19 item model relies on - a pad's own size, with
  cardinal rotation baked in and non-cardinal angles left to the caller, a
  spans-copper sentinel for multi-layer pads, and no drill - at the source level
  rather than only against measured footprint pitch. A non-through via would
  therefore need an engine change; but that capability question is not what
  decides these four edges, because the span-restricted bracket is proxy-clear
  nowhere either, so a blind/buried via of the endpoint span would meet the same
  measured pour and the same rule area. What the measurements show is a **local
  design constraint** at four specific points; whether anything should change,
  and what, is a decision for the plan and the physical design, and this window
  does not establish that a design change is required for every route plan.
* **Independently checked, and the classifier tested where the answer is known.**
  A second script in a fresh process with its own engine, session, layer
  conversion, raw zone query (no wrapper) and a flat O(items) copper scan with its
  own shape distance re-derives the four edges from the taxonomy and the live
  board by exact key, re-measures the column verdict on every layer, and
  re-measures each anchor's clear state at the un-inflated track margin:
  **16/16 layer verdicts and 8/8 anchor verdicts agree, with 0 disagreements**,
  and the two implementations name the same nearest blocking item where one
  exists. The classifier that turns a measurement into one blocker word is covered
  by 41 assertions on synthetic inputs where the answer is true by construction -
  clear, a margin exactly met, keepout winning over clear copper, over blocking
  copper and over an unresolved point, copper versus pour by which slack binds,
  the stable copper-first tie, drill as its own class and losing to a more binding
  copper term, unknown when nothing measured blocks, unknown never hiding a
  measured blocker, determinism, and agreement between the two classifiers.
* **No trial, because no candidate under the measured proxy, and no board was
  touched.** The contract makes a trial conditional on a specific candidate under
  the measured proxy; with 0 of 8 probes producing one, a route would be an
  unbounded re-roll rather than a measurement of a candidate. No legality
  finding is claimed anywhere in this result: native DRC was not run, the margins
  are a clearance proxy rather than rule authority, and pour distances come from
  the fill the loaded board carries, which is not verified against the current
  rules. No route was started, no checkpoint was taken
  (`checkpoint_count()` 0 -> 0), no board file was written, and the accepted
  artifact, its pointer and the scratch copy re-hash identically before and after.
  Nothing was committed, staged or pushed, and no public harness file changed, so
  no public test was added or altered and the strict harness gate was not re-run.
  Coordinates, net names, pour distances, per-edge geometry and rule values stay
  in the private `phase20_via_column/` evidence tree;
  `docs/agent-work/reliability/phase20/RESULT.md` carries the aggregate account.
* **Bound next step is a decision, not another search.** With no proxy-clear
  column anywhere in the declared 3.0 mm square around any of the four shared
  points, the question for these four connections is whether the plan geometry and
  layer assignment change, whether the design changes locally near the shared
  points, or whether they stay unrouted and are documented as such - three design
  options with manufacturing consequences, none of which this window establishes
  as required. Adding a non-through via control to the engine only makes sense
  once a phase finds an edge where a partial-span via is proxy-clear and a through
  via is not.

### Phase 21 - component-proved alternative anchor pairs

* **The question the last three phases leave.** Phase 18 proved every one of the
  49 `connection_not_verified` edges has proved substitutes on its own net but
  never joined two of them; phase 19 searched a window around the *offered*
  segment and found no path; phase 20 explained the four coincident columns.
  Phase 21 asks the remaining question: with **both** endpoints free to move to
  any native-proved same-net anchor, does a *straight* candidate path exist
  between any pair whose two sides are still unconnected? All work is read-only
  on the unchanged accepted generation (`6c4f8ab81b83...f593477cf1b`).
* **The anchors came from the engine's own component partition.** For each of
  the 49 edges the *whole* anchor list of every native component of the net was
  taken (`max_anchors_per_component` opened all the way, so the API's own
  rotation window hid nothing), and every anchor was proved separately - the
  engine's point-identity rule must name the scheduled net and the engine's own
  cluster query must give it a cluster. Anchors in a rule area, or with an
  unanswerable zone query, were refused rather than guessed; duplicate points
  were folded and a point claimed by two components was refused. 201
  components and 1 432 proved anchors. The API's own counts are 1 414 anchors
  across the 98 side-instances; 11 distinct points were refused during proof
  and the offered point itself was added 29 times where the API had not
  listed it, so the pools hold 1 432 and the raw anchor-pair product of
  33 071 becomes the 33 092 pairs the search enumerates. (An earlier draft of
  this entry said no anchor was added; that was wrong - what is verified is
  that the API's fully opened window hides none of its own anchors. A private
  reconciliation script re-derives the identity edge by edge with a residual
  of 0 and reproduces the published pair count exactly.)
* **33 092 pairs, 21 276 of them open, and 0 candidates.** A pair is measured
  only when the two anchors' own clusters are disjoint - the native proof that
  the sides are not already connected - and the two do not share a component;
  10 219 pairs were refused as already connected and 1 597 as one component.
  Discrete copper (tracks, vias, pads; foreign *and* netless) is measured
  exactly by segment-to-shape distance with the first-contact distance bisected
  on the convex point-to-copper function; pour fill and rule areas are sampled
  at 0.125 mm under the 1-Lipschitz allowance, densified to 0.05 mm inside
  0.5 mm of each anchor; a cross-layer pair's two legs *are* the segment, so two
  prefix walks bound the feasible via interval before any column is tested, and
  the column is then measured at the via requirement on every copper layer a
  through via spans. **Every pair was evaluated - none was left to the budget -
  and none produced a candidate path.**
* **Within this candidate family the refusal is attributed, and it is not the
  via.** The clear prefix of the 23 374 measured legs is dominated by 0.5-1.0 mm
  (13 589) and 0.1-0.5 mm (4 335); 11 682 pairs fail because the two legs'
  prefixes never meet, 6 447 same-layer pairs stop on pour inside the
  requirement, 3 134 on copper (1 820 of those on a foreign via, 1 007 on a
  track, 307 on a pad), and only **five** pairs ever reached a feasible via
  interval and then found no clear column. Phase 20's column story holds at the
  four offered coincident points; for a straight run plus one through via the
  binding term is the first millimetre away from a pad. Both halves are
  measurements of this family rather than laws about the board.
* **The escalation turns "no candidate at this pitch" into a statement about
  the board.** A sampled proof needs each sample to clear the requirement plus
  half its local spacing, so the main run records every pair the instrument
  rather than the board refused and re-measures exactly those finer: 0.125 mm
  left 8 unproved, 0.05 mm left 8, 0.025 mm resolved all 8 to a measured copper
  refusal. **21 276 of 21 276 pairs resolve to a measured refusal or a
  candidate, with 0 unexamined and 0 unproved.**
* **Two real defects were found by cross-checking and fixed.** (1) A 1-Lipschitz
  prefix bound applied in the direction that *overstates* a clear prefix: on a
  leg whose corridor ran almost parallel to a foreign copper run the old formula
  claimed far more clear leg than the board has, caught by the independent
  checker's first broad pass, and now replaced by exact bisection on the convex
  distance. (2) The walk stopped at the first sample inside the sampling
  allowance, which mis-labelled 11 272 of 21 276 pairs as instrument artefacts
  while the copper actually dropped inside the requirement a fraction of a
  millimetre later; the walk now continues past allowance-band samples, which
  cut the unproved set to 8. Neither defect was visible from the search's own
  output. The measured magnitudes of both are in the private evidence, not here.
* **Independently checked, and the search is never the optimistic one.** A second
  script runs in a fresh process with a fresh engine and scratch copy, its own
  flat inventory (7 580 records), its own cell index, its own shape arithmetic
  and its own segment-to-segment routine written from the definition. It
  re-derived the 49 edges by exact key, re-proved both anchors and the peer
  separation with the engine's own queries, and dense-walked 4 000 rows at
  0.05 mm against the requirement itself: 4 206 prefix comparisons, 2 769
  agreeing within one dense sample, 1 437 where the checker found *more* clear
  path (the search carries the sampling allowance and stops its pour prefix at
  the previous sample), and **0 where the search claimed more than the checker
  measured**.
* **Positive controls, so "no candidate" is a measurement.** The open strip
  phase 19 used is clear end to end under this phase's own code and comes back
  from the whole pair check as a candidate, at the strictest form of the proxy;
  and a 1 mm grid scan finds 94 of 11 771 grid points with a through-via column
  clear on all four copper layers. The same machinery therefore finds clearance
  where this board has it and answers "no" at these corridors.
* **41 new private assertions, no public change.** `phase21_pair_tests.py`
  covers the geometry primitives, the sample layout, the walk's verdicts
  (including the regression where a legal-but-in-band sample must not hide a
  real contact just beyond it), the pair-status rules, the via column and
  determinism: 54 assertions, `RESULT pass`. No public harness file changed, so
  no public test was added or altered and the strict harness gate was not re-run
  for this phase.
* **No trial, because no candidate, and no board was touched.** The contract
  makes a transactional trial conditional on a credible candidate path; with 0
  of 21 276 there was nothing to trial, so no route was started, no checkpoint
  was taken (`checkpoint_count()` 0 -> 0), no board file was written, and the
  accepted artifact, its pointer and the scratch copy re-hash unchanged
  (`6c4f8ab81b83...f593477cf1b`, pointer sha `57b8db61...`). No legality finding
  is claimed anywhere: native DRC was not run, the margins are a clearance proxy
  rather than rule authority, pour distances come from the fill the loaded board
  carries, and hole-to-hole clearance is not measured. Coordinates, net names,
  per-edge geometry and rule values stay in the private `phase21_anchors/`
  evidence tree; `docs/agent-work/reliability/phase21/RESULT.md` carries the
  aggregate account. Nothing was committed, staged or pushed.
* **Bound next step is a plan-geometry question, not another anchor search.**
  Within the candidate family this phase measures - a straight run, plus one
  through via across layers - every cross-layer pair fails on a leg that stops
  within about 2 mm of its anchor, and 6 447 same-layer pairs stop on pour
  contact along a line. That makes the first millimetre of a straight run the
  binding term *here*; it does not say a route that leaves a pad and turns is
  impossible, and it does not make a redesign inevitable. The smallest
  extension that answers whether these connections are reachable at all
  without changing the board is a bounded *dogleg* test - one turn inside the
  anchor's own clearance void, then a straight run. If that also finds
  nothing, the open questions belong to the physical design - routing room, a
  different layer assignment, or a local pour/placement change near the
  specific anchors this phase measured per edge - and they stay questions.
* **Pre-acceptance review corrections (2026-09-28), recorded here rather than
  rewritten silently.** Four changes were made to this phase-21 entry and to
  `CHANGELOG.md` and the phase-21 public result while the work was still under
  review: (1) a per-leg defect magnitude was removed from all three, because a
  single leg's measured overstatement is per-edge board geometry and the public
  documents carry aggregates only - both defects are now described generically
  with their measurements left in the private evidence; (2) the anchor paragraph
  was corrected - an earlier draft said no anchor was added when in fact the
  offered point is added where the API had not listed it, and what is verified is
  that the API's fully opened window hides none of its own anchors; (3) the two
  pair totals (the component API's raw 33 071 product and the search's 33 092
  enumerated pairs) are now reconciled explicitly, with a private script whose
  residual is 0 and which reproduces the published count exactly; and (4) the
  binding-constraint finding is now scoped to the straight-run-plus-one-through-via
  family this phase measures, so it no longer reads as a prediction that a
  dogleg will resolve these pairs or that a physical redesign is inevitable.
  Nothing was re-run for these corrections: no analysis artifact, search,
  checker, test or engine file changed, and the corrected text is checked with
  `git diff --check` and a targeted scan for private measurements.

### Phase 22 - bounded alternate-anchor doglegs

* **The question phase 21's own next step named.** Phase 21 measured every
  proved same-net anchor pair behind the 49 `connection_not_verified` edges with
  a *straight* segment plus one through via, evaluated 21 276 pairs and found no
  candidate; within that family the clear prefix of the leg leaving a pad was
  the binding term. Phase 22 asks the smallest question that leaves: if a route
  may leave an anchor in a **different direction**, turn once or twice and then
  run, does any pair produce a candidate? All work is read-only on the unchanged
  accepted generation (`6c4f8ab81b83...f593477cf1b`).
* **The pool is phase 21's, and it reconciles exactly.** The anchor proof is
  phase 21's (every native component's whole anchor list, each anchor proved by
  the engine's point-identity rule and its own cluster, keepout and unresolvable
  anchors refused). Re-deriving it here gives 33 092 raw anchor-pair product,
  10 219 refused as already connected, 1 597 as one component, 0 degenerate, and
  **21 276 eligible pairs** - per edge the same number phase 21 called
  `evaluated`, re-derived by a private script with residual 0 across all 49
  edges. The contract caps the search at 16 pairs per edge, so the selected set
  is the head of a deterministic ranking (smallest total anchor movement, then
  anchor coordinates): **560 pairs**, with 21 edges having fewer than 16
  eligible pairs and the four coincident cross-layer edges searched first. The
  subset is bounded on purpose and is not a claim about the other 20 716
  eligible pairs.
* **A declared, finite dogleg family.** Per anchor, 28 escapes: eight compass
  headings plus the direction toward the peer, at 0.4 mm, 0.8 mm and 1.2 mm,
  plus the zero-length escape so every pair's first variant is phase 21's
  straight segment; 784 combinations per pair, at most two bends, and exactly
  one through via on a cross-layer variant - which must sit **on** the measured
  polyline. The measurement is phase 21's instrument unchanged: discrete copper
  (foreign and netless) exactly by segment-to-shape distance, pour fill and rule
  areas sampled at 0.125 mm under the 1-Lipschitz allowance (0.05 mm inside
  0.5 mm of each anchor), an unresolvable point or rule area a refusal, and the
  two prefix walks along the polyline bounding the via's feasible arc-length
  window exactly.
* **Outcome: 0 candidates in 438 563 variants, and the bottleneck moved.** No
  variant of any of the 560 selected pairs produced a candidate; 0 pairs were
  left unexamined by the budget. Pair verdicts (the pair's own best variant):
  230 blocked on copper and 156 on pour among the 390 same-layer pairs, 164 with
  the two clear prefixes never meeting plus 5 with a feasible window and no
  clear column among the 170 cross-layer pairs, and 4 unproved at the declared
  pitch. Bounded escalation re-measured exactly those 4 at 0.05 mm and then
  0.025 mm, resolving all four to measured pour refusals, so all 560 selected
  pairs resolve to a measured refusal or a candidate. **Most of the family never
  left a pad: 379 700 of the 438 563 variants (86.6 %) were refused by an
  escape** - the first turn out of an anchor, 287 936 from the start side and
  91 764 from the target side - and with both escapes clear the main leg stopped
  36 924 variants while the two sides never met in 21 818
  and only 121 ever reached a feasible window with no clear column. The via is
  no longer the binding term at the four coincident points this family reaches.
  A turn does buy length: taking each pair's best variant, 429 of the 560
  selected pairs reached more than 1 mm of proved clear path (53 zero, 26 up to
  0.5 mm, 52 to 1 mm, 115 to 2 mm, 239 to 5 mm, 75 beyond).
* **Independent check, and controls that could have failed.** A second process
  with a fresh engine and its own scratch copy used the independent instrument
  phase 21 already validated - its own inventory, cell index, arithmetic and
  dense walk, sharing nothing with this search - re-derived the 49 edges by
  exact key, re-proved both endpoints and their separation, rebuilt every
  recorded polyline from its escape points and dense-walked it at 0.05 mm:
  **560 rows, 560 comparisons, 307 agreeing within one dense sample, 253 where
  the checker found more clear path, and 0 where the search over-reported.** The
  board-level positive controls found a clear 2 mm-escape / 2 mm-run /
  90-degree dogleg at the 763rd of its scanned origins (762 refused, 531 on pour
  and 231 on copper) and 5 clear through-via columns in a 703-point four-layer
  grid. Private unit tests pass 3 171 assertions, including a comparison of the
  cached, pruned implementation against an uncached, unpruned statement of the
  contract over every variant of four synthetic cases.
* **No trial, because no candidate, and no board was touched.** The contract
  makes a trial conditional on a credible candidate under the measured proxy;
  with 0 of 560 selected pairs producing one and the escalation resolving the
  rest to measured refusals, a route would be an unbounded re-roll rather than a
  measurement. No route was started, no checkpoint was taken
  (`checkpoint_count()` 0 -> 0), no board file was written, and the accepted
  artifact, its pointer, its geometry digest and the scratch copies re-hash
  identically before and after. **0 transactional trials** were run. Nothing was
  committed, staged or pushed, and no public harness file changed, so no public
  test was added or altered and the strict harness gate was not re-run.
  Coordinates, net names, per-edge geometry and rule values stay in the private
  `phase22_doglegs/` evidence tree; `docs/agent-work/reliability/phase22/RESULT.md`
  carries the aggregate account.
* **Bound next step is one of two bounded measurements, not a redesign.** The
  first turn is now the measured bottleneck, so the natural next step is an
  escape search that is not bound to a fixed heading ladder - a short
  free-direction escape phase per anchor - which would separate "no direction
  exists" from "the ladder did not contain one". The alternative is a bounded
  mid-leg waypoint family, because with both escapes clear it is the main leg,
  not the via, that refuses the variant. A physical-design answer - routing
  room, a different layer assignment or a local pour/placement change near the
  specific anchors measured per edge - remains an option with manufacturing
  consequences, and nothing in this result establishes that it is required.

### Phase 23 - what refused the 24 real closures

* **The question.** Phase 17's fresh trial closed a connection and was refused by
  the native gate in 24 of its 109 plan evaluations. Phase 23 asks what those
  refusals actually were, whether a shared harness or rule-application defect
  produced them, and - if one is proved - fixes exactly that. Source: the trial's
  own 24 `drc_regression` records on the exact accepted generation, the phase-17
  taxonomy, and up to three reversible reproductions from the immutable board.
  Zero planner calls; no route config touched.
* **All 24, accounted.** They span 4 edges and 5 class fingerprints, and are 24
  distinct (edge, plan) attempts with no duplicates. Every one closed the
  connection, was refused, rolled back with the rollback verified, and left the
  board on the accepted digest; none recorded a failing plan step because every
  step ran. The recorded classes are a clearance class and a hole-clearance class
  and nothing else - no connectivity, zone-outline or rule-load failure appears in
  any of them. The 87 refusal positions the records kept all sit within 0.5 mm of
  the plan's own route and on a layer that plan's own copper occupies.
* **Three reproduced, and the cause is the same in each.** One record per edge was
  replayed exactly through the same public transaction on a byte-identical scratch
  copy, whole-board native DRC. Every added relevant finding is one item that did
  not exist before the transaction - the plan's own track or via - against one
  existing board zone, at exactly zero measured clearance in most rows and below
  the required minimum in the rest. Each transaction restored its own copper and
  the accepted pointer and board hash were re-verified before and after every
  call. No candidate was promoted; nothing was accepted.
* **The board already reports the same two classes 7 637 times.** Two whole-board
  passes over the unmodified accepted generation returned the identical relevant
  set: 8 130 findings, 7 930 relevant, of which 7 488 are the same fill-clearance
  rule and 149 the same hole-clearance rule; 7 636 touch a board zone and 164 of
  the fill-clearance findings sit at exactly zero clearance against copper that
  predates any routing. The 7 488 cluster tightly around one value, i.e. the pours
  were filled at a finer clearance than the recovered rule file states. So the
  condition the refusals report is a property of the board's stored fills, not of
  the plans: the router does not model pours and the gate correctly measures the
  fill that is stored.
* **A real evidence defect, proved and fixed.** A refusal record could not say how
  many findings it added by class. `AttemptRecord.drc_classes` was counted from the
  DRC delta's `added_relevant` rows, and that list is capped - so a refusal was
  recorded with at most eight findings' worth of classes however many it added: on
  this trial 9 of the 24 records under-count, the worst recording eight where the
  delta added 50, and the public refusal-class aggregate inherited it. `DrcDelta`
  gains `class_histogram()`, `to_evidence()` carries a complete
  `added_relevant_class_counts`, and the runner records that, keeping the rows as
  the fallback and as the source of the deliberately few hint positions. Two new
  tests fail against the unpatched module and pass with it. Additive and
  evidence-only: nothing reads these fields to decide anything.
* **Native-DRC stability, measured.** Twenty fresh processes each ran one
  whole-board pass over the unmodified generation: 18 returned 8 130 / 7 930 with
  an identical relevant multiset, and 2 returned 8 133 / 7 933, the entire
  difference being one class, `Items shorting two nets`, 30 vs 33. In the one
  captured with positions the three extras are a single location reported on three
  copper layers; the other 7 933 process was captured as counts only, so whether
  its extras are the same three is unknown. Four consecutive passes inside one
  process were identical, and no baseline/candidate pair in this phase moved that
  class - the gate compares two passes of one session.
* **No promotion, and no gate change.** Nothing was accepted, so the promotion
  ladder did not run; the accepted pointer, its bytes, the generation directory and
  every scratch copy re-hash identically, and the accepted board still hashes to
  `6c4f8ab81b83...f593477cf1b`. `bash tools/reliability/check_phase.sh --strict`
  is 496 unit + 138 native with no skips; `tools/check_separation.py` 4/4;
  `check_engine_patches.py` byte-equal; `git diff --check` clean. No engine or
  wire file, footprint, zone, rule value or violation class was changed, and
  nothing was staged, committed or pushed. Per-edge geometry, net names and rule
  values stay in the private `phase23_drc/` evidence tree; the aggregate account is
  `docs/agent-work/reliability/phase23/RESULT.md`.
* **Bound next step.** One measurement then a decision: refill the zones under the
  current rules on a disposable copy and ask the native DRC whether the two
  refusing classes, and the 7 637 pre-existing findings they share, disappear -
  the refill holding its own generation and whole-board verification. If they do,
  the answer is that the fills must be recomputed against the rule file; if they do
  not, the refusal is a genuine clearance the router must respect and the next
  phase belongs in pour-aware placement.

### Phase 23 correction - semantics of the refusal attribution (T23 review)

A separate review requested documentation and evidence semantics only. Three
corrections were applied in place to
`docs/agent-work/reliability/phase23/RESULT.md`, the phase-23 `CHANGELOG.md`
entry and the private `phase23_drc/NOTES.md`. No code, test, board, rule, engine
or evidence artifact changed, no suite was re-run for wording, and **no earlier
entry was rewritten**; where the phase-23 entry above and this one disagree, this
one holds.

* **1. Item-level attribution is 3 plans and 14 of their 15 added identities.**
  The three reproduced deltas added 15 *distinct relevant identities* between them
  and retained 14 delta rows (8 of 9, 5 of 5, 1 of 1); in those 14 rows one side is
  copper issued inside the transaction and the other is an existing board zone.
  The fifteenth identity is counted by the histogram but has no retained row, so it
  carries no item-level attribution. The other 21 of the 24 plans have class-level
  evidence and route-consistent hint geometry only, so the same physical shape is a
  supported hypothesis there, not proof. The phase-23 entry above reads more
  broadly than that and is corrected by this point.
* **2. The complete histogram is not the capped record, and it counts identities,
  not multiplicities.** The first reproduced plan's complete histogram is clearance
  5 + hole 4 against its stored capped fingerprint of 4 + 4, so the complete counts
  matched the stored record for two of the three plans and **not** for the first;
  "all class counts matched" was wrong. The histogram counts distinct added
  relevant identities - the delta's keys - never occurrence multiplicities, which
  the payload carries in a separate field.
* **3. The artifact check is before/after the run plus the per-transaction
  in-memory digest.** What was checked is that the accepted board file and the
  pointer have the same hashes before and after the run, that no engine call wrote
  to the generation directory or the scratch copies, and that each transaction's
  own in-memory copper digest was equal before and after. "Re-verified before and
  after every call" overstated it.
* **The next refill measurement carries its own validity gate, and persistence
  proves nothing by itself.** The bounded next action is: perform a zone refill
  under the pinned rules on a disposable copy only if the engine reports the refill
  supported; save and reopen the refilled board and prove from the reopened file
  that the fill geometry actually changed; then re-run the whole-board native DRC
  and classify every finding - the two refusing classes and the 7 637 pre-existing
  findings they share - as resolved, persistent or new, with an unsupported or
  incomplete refill reported as its own outcome that says nothing about the
  findings. Only a proved refill can support a statement, and a persistent finding
  under a proved refill is a *candidate* genuine clearance that a further
  measurement must confirm - never an implication of persistence.
* **Verification for this correction.** Documentation only: a targeted scan for
  the three semantics above and a scan for private data (net names, coordinates,
  rule values) over the corrected public text, plus `git diff --check`. The public
  code files carrying the phase's fix are byte-for-byte unchanged from the revision
  the strict gate ran against - `pcb_world/agent/drc_gate.py`
  `sha256 4b9b4a565cd8a62f7c01480767f66ab803d7f99af72551b153e48bf4d999edf8`,
  `pcb_world/agent/runner.py`
  `sha256 36b0408f6addbebc78347f0e533574c8ff9b72d95ddc86e0b2ca60d294cd35a1`,
  `tests/agent/fake_engine.py`
  `sha256 404e866a9bfbfa21f665f8046a3d7dad1087788de052d8da0ea7f9ba0ef290e4`,
  `tests/agent/test_drc_classification.py`
  `sha256 b9cbec5ff9db83cee5934da23fd6cda4bc1ca47de56cb049b3e48a7498934c08`,
  `tests/agent/test_runner_scripted.py`
  `sha256 de5ed8069789cdd39c66393995fe96b22b755b713f221ea9f0608e61acc7b579`
  - and their modification times predate this pass. Nothing was staged,
  committed or pushed.

### Phase 23 correction 2 - cause scope in the phase-23 entry (T23 residual review)

A second focused review asked for two residual phrasing defects. They are
corrected in place in `docs/agent-work/reliability/phase23/RESULT.md`, the
phase-23 `CHANGELOG.md` entry and the private `phase23_drc/NOTES.md`. **No earlier
entry is rewritten**, so this note records the same corrections for the phase-23
entry above:

* **Its board-profile paragraph concluded too much.** "So the condition the
  refusals report is a property of the board's stored fills, not of the plans" is
  withdrawn. The 7 488 + 149 pre-existing fill findings **support the same
  hypothesis** for the refusals that were not reproduced - they show the board
  itself already carries the refusing condition at scale - and they do not prove
  the cause of all 24. The corrected wording says exactly that.
* **Its opening line read as a single cause for all 24.** "The 24 plans were
  refused for the plan's own new copper, and for one reason" is replaced. All 24
  refusals are **reconciled**; retained rows from the three reproductions
  demonstrate new copper against existing zone fills, and the same cause for the
  other 21 remains a **supported hypothesis**. The CHANGELOG's "The answer: ..."
  bullet heading is rescoped the same way, to "The reproduced cause: ...".

No code, test, contract or evidence artifact changed and no run was repeated:
verification for this note is the targeted semantic scan and private-data scan
recorded in the T23 correction entry above, plus `git diff --check`. Nothing was
staged, committed or pushed.

## 2026-09-28 — the refill measurement: supported, and not free

Phase 24 asked one question of the accepted generation and answered it with a
disposable copy. The supported path is the pinned engine's own refill
(`KiCadEngine.fill_zones(rules)` over native `fillZones`); on the accepted board's
copy it returned success in 2.6 s and changed the stored derived copper of 95 of
177 zones (fill vertices 88 596 -> 140 822). The saved board was reopened in a
fresh process and reports the same fill digest, the same non-zone digest and the
same DRC result, so the change is a property of the bytes, not of the session.

Everything that is not a zone is untouched. The 7 221 top-level non-zone items
hash to the same multiset before and after (6 210 tracks, 209 vias, 321
footprints, board graphics, text, nets, layer stack), no zone was added, removed
or re-typed, and zone declarations are identical apart from the writer's
`(fill yes ...)` flag appearing on three imported zones. The source board is
byte-identical after the run, the accepted pointer and the canonical original are
unchanged, and nothing was staged, committed or pushed.

The findings moved exactly as the phase-23 mechanism predicted and the connection
did not survive it. Two whole-board native passes on the accepted copy
reproduced phase 23's relevant multiset to the identity (8 130 total / 7 930
relevant, `2df69ffe72f5e522`). After one refill the same board measures **533
total / 313 relevant** (`ca2220dc9235845f`): **7 488** fill-clearance identities
and **147 of 149** hole-clearance identities resolved, with 294 relevant
identities persistent and 19 added. The added ones are the cost: **19 newly
isolated copper fills**, **+19 pad groups**, **+19 ratsnest edges** and **33
previously connected terminal pairs no longer connected**, the same 33 in the
one-process around-the-refill comparison and in the fresh-process comparison. The
board's connectivity depends on pours drawn at a finer clearance than its rule
file requires, so refilling under the current rules is a design decision, not a
free repair - and this phase asserts nothing about whether the persistent
findings are genuine clearance.

The complete pinned CLI was run against the canonical original (the reference the
accepted generation's own promotion gates were staged against) with both sides on
the same project and rule bytes, two runs per board, reports complete. It did not
accept the candidate: `clearance` 7 488 -> 0 with **zero added identities**,
`solder_mask_bridge` 90 removed, `starved_thermal` 37 added and 100 removed,
`isolated_copper` 4 added, `track_dangling` 1 added, `items_not_allowed` 3
removed, `shorting_items` 30 -> 30 with nothing moved, and unconnected items
149 -> 154 with the non-reproducible pairing the gate already refuses to judge
without a bound native terminal proof. A supplementary run with the accepted
generation as reference isolates the refill's own contribution and shows the same
candidate counts. The reporter surfaces classes the native severity configuration
does not, which is why the ladder requires both gates. The CLI verdict is
`unverified`, not a pass.

Two measurements bound how the result may be read. The known per-process
shorting variation is not in play: `Items shorting two nets` is 30 in all six
whole-board passes, appears in neither delta's added or resolved set, and the
CLI's own `shorting_items` class moved zero identities; the only difference
between the within-process and cross-process native deltas is one connectivity
pairing. And a save through the pinned writer rewrites the project sidecar into
its own schema (version, colour keys, netclass ordering, list-valued
assignments, and a per-netclass `via_annular_width` token that is not a KiCad
netclass field), with every clearance, track, via and hole value identical - so
both sides of every comparison used the reference's own project bytes, and a
future candidate needs its sidecars normalised at staging.

`bash tools/reliability/check_phase.sh --strict` -> exit 0, 496 unit + 138
native, no skips; `tools/check_separation.py` 4/4; `check_engine_patches.py`
byte-equal; `git diff --check` clean. Zero planner calls, zero promotion, and the
private `phase24_refill/` evidence retains the per-item geometry, nets,
coordinates and rule values the public aggregates deliberately omit.

## 2026-09-28 — phase 24 correction cycle (T24)

Astra reviewed phase 24 and T24V independently reproduced its measurements from
the same two files; the corrections below are applied **in place** in
`docs/agent-work/reliability/phase24/RESULT.md`, the phase-24 `CHANGELOG.md`
entry and the private `phase24_refill/NOTES.md`. **No earlier entry in this file
is rewritten**, so this note records the same corrections for the phase-24 entry
above. No code, script, engine, rule, gate, evidence number or board changed, and
nothing was staged, committed or pushed.

* **Causality for the 24.** "The findings moved exactly as the phase-23 mechanism
  predicted" read as a cause proof for the historical refusals. It is not one:
  phase 24 performed a **board-wide** refill and **replayed no transaction** of
  the 24 closure-then-refusal plans. The corrected text says the class those
  refusals shared is removable board-wide on this generation, and that phase 23's
  attribution (item-level for 3 of 24, supported hypothesis for the other 21) is
  neither extended nor weakened.
* **The isolated-fill count.** The class goes `1` -> `19`, which is a **net `+18`,
  nineteen new identities and one resolved**, not "19 more" without
  qualification. Both the raw count and the identity split are now stated, in the
  result, the changelog and the private notes.
* **Persistent versus new.** The `294` persistent relevant identities **exclude**
  the 19 newly isolated fills; the candidate's `313` relevant identities split as
  `294 + 19`. The earlier "294 ... and 19 isolated fills that were not isolated
  before" conflated the two.
* **Which comparison the numbers come from.** `7 666 resolved / 50 added
  connectivity` is the **cross-process** pair; the in-session pair around the
  refill call is **`7 665 / 49`**, and the one identity of difference is a
  connectivity pairing (the variation the previous entry already characterises),
  not copper. Both are now labelled and neither is quoted unlabelled.
* **What the 33 relations are.** The 33 are the comparator's **witness pairs**
  (one anchor terminal per reference cluster, one pair per terminal that lands in
  a different candidate cluster), not an exhaustive count of lost pairs. The
  structure underneath is **7 fragmented reference clusters - six split in two,
  one split into fourteen - needing at least 19 rejoins**, which is the same 19
  the pad-group, ratsnest and isolated-fill counts show.
* **No merged-count claim.** The `merged_pairs` field of the terminal comparison
  result carries a dataclass default and is never computed by that comparison, so
  the "0 merged pairs" row is withdrawn rather than reported as a measurement.
* **Promotion conditions.** The next-bounded-action section no longer offers a
  branch that could accept the broken board. It now requires the **original
  connectivity restored** and **all existing gates** - the whole-board native DRC
  under the pinned rules, the native terminal partition with no fragmented
  reference cluster, and the complete pinned CLI against the canonical original -
  on the candidate's own generation before the accepted pointer may move. The
  alternative branch is recorded as an engine capability decision for its own
  phase, not something this phase takes.
* **The sidecar rewrite is a superset, not an equality.** Every value the two
  project files share is equal, but the candidate's `rules` block **gains 11
  keys** the imported file did not carry, so "every clearance, track, via and
  hole value is identical" is corrected to that, with the added keys named in the
  private notes. Both sides of every comparison were staged with the reference's
  own project and rule bytes.
* **An unarchived anecdote.** The sentence describing a first CLI attempt that hit
  the sidecar rule is removed: the refusal is a property of the gate's stated
  rule and only the archived run is evidence.

**Independent verification, recorded here because it is the strongest statement
in the phase.** T24V re-derived the geometry comparisons from the same two files
and reports (`/tmp/p24_verify/indep_cmp.json`, `/tmp/p24_verify/indep_zone_v2.json`):
the non-zone multiset is equal with **0 items added and 0 removed across all
7 221 items** under each of three separate encodings (decimal-quantised,
quote-aware and byte-raw), and the declaration comparison finds **exactly three
edits, each one `(fill ...)` -> `(fill yes ...)` and nothing else**
(`all_decl_changes_are_fill_yes_only: true`). The result document now carries
that as corroboration of section 2 rather than only this phase's own digest.

**Verification for this correction.** Documentation only. A targeted scan of the
four touched documents for the withdrawn phrasings and for private data (net
names, coordinates, item geometry, rule values), plus `git diff --check`. No
board, script, engine, rule or gate file was touched, and no measurement was
re-run: the numbers are the ones T24V reproduced against the frozen result hash
`08c8dc4b...`.

## 2026-09-28 — phase 24 correction cycle, residual count phrasing

Astra's follow-up review of the corrected phase-24 documents found one residual
phrasing defect, corrected **in place** in
`docs/agent-work/reliability/phase24/RESULT.md`, the phase-24 `CHANGELOG.md`
entry and the private `phase24_refill/NOTES.md`. **No earlier entry in this file
is rewritten**, so this note records it for the phase-24 entry above. Docs only:
no board, script, engine, rule, gate, evidence number, pointer, stage, commit or
push changed.

The three places listed the isolated-fill class as "19 more copper fills", as a
"`+19`" alongside the pad-group and ratsnest rises, and as one of "three views of
that one event". All three implied that the raw class count grew by 19 and that
the new isolated fills correspond one-for-one with the 19 required component
rejoins. Both implications are wrong: the class goes from a raw `1` to `19`, which
is a **net `+18` - nineteen new identities and one resolved** - and the pad-group
rise, the ratsnest rise, the 19 minimum rejoins and the isolated-fill class are
**separate measurements of one refill**, not an item-for-item correspondence.
The corrected text states the raw `1` -> `19` transition, the net `+18`, the
nineteen-new/one-resolved split, and says explicitly that no such correspondence
is asserted.

**Verification for this correction.** Documentation only: the targeted
docs-to-evidence consistency check over the archived phase-24 JSON (counts,
profile hashes, the in-session/cross-process split, 7 fragments / 33 witnesses /
minimum 19 rejoins, and 11 added rule keys), a scan for the withdrawn phrasings
and for private data (net names, coordinates, item geometry, rule values), and
`git diff --check`. The measurement is unchanged; the numbers are the ones T24V
reproduced against the frozen result hash `08c8dc4b...`.

## 2026-09-29 — phases 25-29 journalled, integrated, and backed up (T29I)

Phases 25-29 ran, were independently verified and were accepted by Astra, but
none of them owned `HISTORY.md` or `CHANGELOG.md`, so their accounts lived only
in `docs/agent-work/reliability/phase25`-`phase29`. T29I is the task that owns
both files, so this entry records them from their frozen documents and then
records the integration itself. Every number below is the number the named
document carries. No frozen board, rule, project, accepted pointer, pinned
binary, patch or accepted report was edited to write this entry, and no
measurement was re-run for it.

### Phase 25 — the reconnection pilot

One of the nineteen component joins phase 24's zone refill cost was restored on
a disposable copy of the refilled board, placed by the pinned native router under
the pinned project and rules, and it survives a legal refill of its own zones.
The candidate adds zero relevant DRC identities and zero fragmentation against
the refilled board it was built from, and takes the fragmentation against the
accepted generation from 7 clusters / 19 minimum joins to 6 / 18 (and the
canonical original from 33 broken relations to 32). It is not promotable:
eighteen joins remain, the complete pinned CLI gate returns `unverified` for
reasons the phase attributes, and the accepted pointer did not move. Detail:
[phase 25 RESULT](docs/agent-work/reliability/phase25/RESULT.md).

### Phase 26 — the campaign, and where the CLI findings come from

The campaign's selection order, attempt ladder and retention rule were declared
before it ran. All eighteen remaining links were attempted on disposable copies:
**six joins were retained**, each surviving a legal refill, save and reopen of
its own zones, and twelve were refused with no quarantine and every rollback
verified. The candidate stays unpromotable — it still carries the refill's whole
DRC debt and the complete pinned CLI refuses it against both the accepted
generation and the canonical original. The separate CLI attribution
(`phase26/CLI_ATTRIBUTION.md`) re-measured three frozen boards with the complete
pinned reporter, four fresh processes each, staged with the canonical original's
project and rule bytes, and separated the classes the refill causes from the
classes the reporter's own configuration causes, plus one class that is not
reproducible at a constant count. Detail:
[phase 26 RESULT](docs/agent-work/reliability/phase26/RESULT.md).

### Phase 27 — a verifier that decides on the reopened bytes, and the shorting hold

T27A tightened the retention verifier: it pins the four boards a decision reads,
decides every board-level condition on the **reopened** post-refill bytes,
refuses on an incomplete or unpinned input, removes the single-pass reading of
the native condition, and replaces the campaign's component-keyed attempted set
with a stable route-offer identity. Its diagnostic single-instance mode may
report conditions but can never return a retention verdict. T27B diagnosed the
"items shorting two nets" hold read-only: on byte-identical boards the pinned
native reporter returns 30, 33 or 36 rows for that class across runs, only the
number of copper layers each pair is reported on moves, and the item-pair set is
invariant at 30 pairs across 193 passes and the pinned CLI's own 20 frozen
reports. That makes the pair set the reproducible quantity and the row count not
one, but a pair-set gate also needs each UUID to name one unchanged physical
item, which the original import does not guarantee. Detail:
[phase 27 VERIFIER](docs/agent-work/reliability/phase27/VERIFIER.md) and
[phase 27 SHORTING](docs/agent-work/reliability/phase27/SHORTING.md).

### Phase 28 — the shorting reporter repaired at the source

Both copper-clearance providers canonicalise a `(BOARD_ITEM*, BOARD_ITEM*)` pair
by pointer address before touching their per-run `checkedPairs` cache, but only
the R-tree filter applied the same swap; the visitor therefore wrote
`has_error` to an entry the filter never read back and one pair was reported once
per copper layer it shared. T28A fixed the visitor in the RL provider and in a
new overlay copy of stock KiCad 9.0.8's provider — the one `kicad-cli` loads from
`_pcbnew.kiface` — delivered as the sequential engine patch
`patches/engine/0004-reporter-cache-canonical-order.patch` with an added `cp -p`
in `engine/build_rl_router.sh`. Mutex scope, layer tracking, `has_error`,
cancellation, the genuine-short and clearance branches and severity handling are
untouched, and no pair-set comparator, class waiver or rule change was added. The
harness gained a deterministic multilayer fixture, a six-test regression over
both providers, a required `patches` group in the phase gate with three tests
pinning it, and a `check_engine_patches.py` that accepts a patch adding a file
the pin does not carry. The differential across three frozen boards, twelve fresh
native processes and six fresh CLI invocations each returned the same 30 shorting
pairs with stable multiplicity and no lost genuine finding. T28C then re-measured
the six frozen phase-26 restoration steps against the repaired loaded provider and
replayed them through the accepted T27A verifier: **all six retain**. Detail:
[phase 28 RESULT](docs/agent-work/reliability/phase28/RESULT.md) and
[phase 28 REPLAY](docs/agent-work/reliability/phase28/REPLAY.md).

### Phase 29 — the last ordering dependence, fail-closed evidence, and the rebaseline

T29N diagnosed read-only the family that declares its netless same-logical-pad
partners *before* the netted pad: the R-tree filter claims the pair's layer for
the netless member and the netted member's later visit is filtered out, so
nothing is reported at all. T29A repaired it in both front ends as engine patch
`patches/engine/0005-netless-first-pair-release.patch` — the visitor releases the
layer claim when the visited pad is netless, the partner is netted and the pair is
the same logical pad, leaving an exempt pair's single claim and a filed pair's
de-duplication intact — and rebuilt the module (`819a26f1…`), the kiface
(`c23f8cb9…`) and the stamp (`b47d4f0c`). No rule, waiver, severity, clearance,
proximity or exemption changed. T29G made serialized native evidence fail closed:
a malformed or incomplete violation multiset used to be readable as "no
findings", and the gate now validates the reader's shape, provenance and
accounting before any comparison, with the saved-artifact gate checking the
baseline before the engine is opened. T29P measured the reduced EasyEDA-export
`.kicad_pro` against the full KiCad-rewritten one on identical board bytes and
found the same effective policy for everything this board and generation
exercise — 7934 relevant identities on the accepted generation and 317 on the
restoration baseline, the same eight netclasses, the same class on each of the
191 populated nets, under both the pinned native engine and the pinned
build-tree CLI — with eight one-block hybrids resolving identically, so no single
block is load-bearing. T29C then re-measured all six historical restorations
under the accepted strict consumer: all six retain with complete CLI, terminal
and native evidence. Its representation comparison is now a whole-board
structural contract that accounts for every node on both sides and folds only two
re-spellings, each at one exact position, named and counted, with duplicate and
missing identities refusing. The candidate's mandatory original-relative
common-fill comparison still refuses — 19 added isolated-copper identities and
five fragmented clusters needing 12 minimum joins — and nothing was promoted.
T29AV, T29GV and T29CV verified each packet independently, and Astra accepted
T29N's diagnosis and the T29A, T29G, T29P, T29GV, T29C and T29CV results. Detail:
[phase 29 NETLESS_ORDER](docs/agent-work/reliability/phase29/NETLESS_ORDER.md),
[phase 29 REPAIR](docs/agent-work/reliability/phase29/REPAIR.md),
[phase 29 EVIDENCE_VALIDATION](docs/agent-work/reliability/phase29/EVIDENCE_VALIDATION.md),
[phase 29 PROJECT_POLICY](docs/agent-work/reliability/phase29/PROJECT_POLICY.md),
[phase 29 REBASELINE_REPLAY](docs/agent-work/reliability/phase29/REBASELINE_REPLAY.md)
and [phase 29 REBASELINE_REFRESH](docs/agent-work/reliability/phase29/REBASELINE_REFRESH.md).

### Integration (T29I)

* **Version.** `pyproject.toml` moves from `1.0.1` to `1.1.0`. The
  `README.md` `<!--VERSION-->` marker is owned by another task's scope and still
  reads `v1.0.1`; the one-line follow-up and the reason it was not made here are
  recorded in `docs/agent-work/reliability/phase29/INTEGRATION.md`.
* **The acceptance run, on the exact staged set.**
  `bash tools/reliability/check_phase.sh --strict --expected-native 146` exits 0
  with **640 unit tests and 146 native tests passing and no skips**, and the patch
  group reporting that the five-patch engine series applies to the pinned commit
  and reproduces this checkout's engine tree byte for byte. `git diff --check` is
  clean.
* **Allowlist.** The backup stages an explicit list of public harness paths —
  the agent package, the engine-side interface edits, the methods and tools, the
  engine patch series, the agent and engine tests, the reliability documents and
  the two journals — and nothing else. Private board evidence lives in its own
  workspace outside this repository and is not in any phase of this commit.
* **Leak scan, and one finding.** The staged set is scanned for secrets,
  private board paths, real-board identifiers and coordinate-like decimals. Two
  pre-existing phase-5 documents carry real-board identifiers — a component pad
  reference and three net names — written before the phases began leak-scanning
  their own reports. They are `docs/agent-work/reliability/phase5-kicad/RESULT.md`
  and `docs/agent-work/reliability/phase5-kicad/CHECKPOINT.md`. T29I does not own
  those accepted documents, so it held them out of the first backup instead of
  rewriting an accepted report, and records the exact lines and the proposed
  minimal correction in `docs/agent-work/reliability/phase29/INTEGRATION.md` for
  Astra to decide.
* **Backup.** T29I freezes the staged set, records its per-file hashes and staged
  diff, and stops before committing so Astra can review it. The single detailed
  commit on `feat/agent-reliability-actions`, and the push to the existing fork
  `JaredReabow/PCBWorld`, follow that acceptance; this entry ships in it. No
  force-push, no history rewrite and no private artefact.

## 2026-09-29 — T29I correction cycle: the phase-5 identifiers redacted, version reconciled

Astra reviewed the T29I packet and returned **REQUEST_CHANGES** with a narrow
scope: redact only the real-board identifiers the leak scan had identified in the
two phase-5 documents, bring the `README.md` version marker into line with
`pyproject.toml`, and refresh the packet. Astra also extended the ledger's
`write_paths` for T29I to cover `README.md` and both phase-5 documents, and
accepted the 48 patch-format whitespace findings and the one cosmetic trailing
space in `pcb_world/agent/zone_coverage.py` as they stand. The T29I entry above
is left exactly as it was written - this file is append-only - so the two
statements it makes about the held-back documents and the version marker are
superseded by this entry rather than edited.

**What changed, and only what changed.** Eleven identifier occurrences across
seven lines are gone from the public record, replaced by the plain description
the same documents already use for withdrawn material: the component pad
reference in
`docs/agent-work/reliability/phase5-kicad/RESULT.md` (lines 251 and 260) and in
`docs/agent-work/reliability/phase5-kicad/CHECKPOINT.md` (lines 512 and 703), the
three supply net names and one KiCad-generated net name in the CHECKPOINT's
terminal-partition row (line 512) and per-net relation bullet (lines 685-686),
and the two supply net names in its added-identity row (line 511). Every
historical finding, withdrawal notice and aggregate count is preserved
unchanged: the four isolated-copper identities with their 3 + 1 split, the 33
split relations with their 20 + 12 + 1 breakdown, the fourteen relations lost on
five nets with their net indices, and the GND 34 -> 44 measurement. Both
documents are now staged, so the reference to `phase5-kicad/RESULT.md` in
`patches/engine/README.md` resolves.

`README.md` line 7 moves from `v1.0.1` to `v1.1.0`, so the marker and
`pyproject.toml` agree. Nothing else in the README changed.

**What did not change, and the proof.** No executable source, no test, no pinned
binary, no engine patch, no T30R input and no board, rule, project or pointer was
touched, and the trailing space in `pcb_world/agent/zone_coverage.py` line 1040
is left exactly where Astra accepted it. The staged bytes were re-checked after
the edits: every source, test and patch path still has the same SHA-256 it had
when the strict harness passed, so the passing run still covers the staged code,
and no third native run was needed for a documentation-only change. The leak
re-scan of the staged bytes reports no secret, no private-board identifier, no
UUID-shaped token outside synthetic fixtures and constants, and no
coordinate-like decimal; `git diff --cached --check` reports exactly the 48
patch-format findings plus the one accepted cosmetic space, with nothing new.
All 26 hashes the ledger records for accepted artifacts still match their staged
blobs, and the refreshed per-file manifest in
`docs/agent-work/reliability/phase29/INTEGRATION.md` matches the staged index row
for row. The packet stops before the commit again, for Astra's final acceptance.

## 2026-09-29 — the backup verified from committed objects, and the docs-only follow-up

The commit described two entries above is on the fork, and it was checked against
the objects GitHub holds rather than against the working tree. T29IV read every
check through `git show HEAD:<path>` and one `git ls-remote`, so its review covers
the committed bytes: the tree `85e79f68ce2562760b09b09c9ce4576799e75d67` (equal
to the Astra-accepted tree), the single `b3d62f5` parent, all 170 rows of the
integration manifest, all 26 artifact bindings the ledger then carried, the 49
accepted whitespace findings, the remote branch tip, and a fresh privacy scan
over the 171 committed blobs that found no secret, no coordinate-like geometry
and no board identifier beyond the project-name and workspace convention the
reports already declare. It also re-ran the engine patch reproduction, which
reapplies the five-patch series to the pinned commit and rebuilds nine files
byte-for-byte. Verdict **PASS**, with four documentation-accuracy observations
O1-O4 and no leak. Detail:
[phase 29 INTEGRATION_REVIEW](docs/agent-work/reliability/phase29/INTEGRATION_REVIEW.md).

**Astra accepted both tasks.** T29I and T29IV are `accepted` in the ledger, with
the accepting review recording the artifact hashes it snapshotted across the
whole phase-29 closure, and the review page is registered as an artifact of its
own. The `T30R` entry the checkpoint names is untouched and still running on its
private disposable lineage; this follow-up neither reads nor writes it.

**The follow-up is documentation-only.** It touches exactly four paths - this
file, the integration page, the T29IV review page and the parallelism ledger -
and it carries the post-push receipt the integration page was frozen without:
the commit, its tree, its parent, the remote and the branch tip read fresh from
`git ls-remote origin`, with a normal push and no rebase, tag or amend. Two
counts in that page were low and are corrected rather than left standing: the
pad-reference bucket is three files, not two, because the synthetic CLI-report
fixture in `tests/agent/test_cli_gate.py` carries a pad-plus-component
description string and the same sentence already named that fixture for its
supply-net token; and four paths name the board project without the private
path, not two - the two the page listed plus
`docs/agent-work/reliability/phase18/PLAN.md` and `pcb_world/agent/runner.py`. The
integration page's 170-row manifest is now labelled as the historical snapshot
of the commit it was generated for, and it is deliberately not regenerated
against this tree, so its two rows for this file and for the ledger no longer
describe the follow-up and are not claimed to.

**Two things the follow-up stopped claiming.** The review page's remaining
real-board net indices are replaced by the aggregate relation count and per-net
split the same paragraph already carried, and the page no longer treats a
non-forced push as established: the reading is bounded to local objects, because
server-side history is not readable from here. Its check 4 is recorded as not
provable from this host, with what is established stated narrowly - the branch
tip is a direct descendant of the published `main` tip, and no local evidence of
a rewrite exists. Every test conclusion, the O4 limitation, the patch
cross-registration and the record that four of five patch digests are first
registered on the review page are all preserved.

**What did not change, and the proof.** No executable source, no test, no engine
patch, no pinned binary, no board, rule, project or accepted pointer was touched,
and the private board workspace was not read. Every source, test and patch path
still carries the SHA-256 the accepted strict run measured, so that run still
covers the code and no native run was repeated for a documentation-only change.
The ledger validates with zero errors and zero warnings, and its 21-entry review
snapshot binds the corrected integration and review pages by content hash. The
follow-up's own commit and tree are recorded in the handoff packet returned to
Astra, because a file cannot carry the hash of the commit that contains it.
