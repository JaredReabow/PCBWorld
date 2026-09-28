# PLAN — agent reliability layer (phase 1)

> ## PHASE 1 ACCEPTED — 2026-09-26
>
> Astra reviewed the final changes and independently verified the two closing
> blockers: the pinned DRC enum mapping (code 14 relevant, code 12 the
> connectivity signal, checked against `build_rl/kicad_src/pcbnew/drc/drc_item.h`)
> and the exact unreadable-transaction reproduction (`accepted=False`,
> `committed=False`, zero tracks, verified rollback). `git diff --check` passed.
>
> **Acceptance scope.** This accepts the *first experimental reliability phase* of
> the fork: `pcb_world/agent/` (validated structured actions, session-bound
> snapshot tokens, proven rule context, native-DRC acceptance, atomic
> transactions), its JSON tool surface, the engine rule-context patch, and the
> phase harness. Evidence is **scoped and synthetic**: 159 unit + 24 native tests
> (183 total) over generated boards, `bash tools/reliability/check_phase.sh
> --strict` exit 0 with no skips.
>
> **Not covered by this acceptance** (unchanged limitations, RESULT.md §5): a
> whole-board routing result; live LLM policy integration (`connect_targets` is
> not yet a tool in `methods/llm_agent`); a PNS-side custom-rule fix (the router
> still does not enforce a rule file on placed copper — acceptance refuses the
> result instead); board data outside the tracked mirrors (zones, board design
> settings) under the exclusive-engine-ownership assumption; and the partial wire
> mirrors (no track type/arc mid-point/lock flag/solder-mask margin).
>
> Implementation remains local, uncommitted and unpushed. Next planned phase is
> integration (see [CHECKPOINT.md](CHECKPOINT.md#next-phase-resume-here)), not
> another review loop.

> **Correction pass (2026-09-26, second entry in this file).** Root reviewed the
> first submission and returned nine findings: token staleness was revision-only,
> validation accepted unsupported schemas / fractional layers / malformed
> endpoints, the rule gate was opt-in and bypassable by a caller boolean,
> acceptance compared violation *counts*, `geometry_digest` ignored via layer
> spans, endpoint identity guessed at pad layer integers, the shove test never
> proved displacement, the phase gate could go green with no native execution,
> and the advertised JSON schema was not actually consumed. The contract for the
> correction pass is [Correction contract](#correction-contract) below; the
> account of what was done is in [RESULT.md](RESULT.md).

> **Second correction cycle (2026-09-26).** A targeted fault-injection check on
> the corrected build found five material defects: an unverified path could leave
> copper behind (`A`), the DRC side-channel was not re-checked after a run and a
> caller context could downgrade a loaded rule file (`B`), the JSON adapter
> inherited the session's token opt-out and mis-validated shapes/booleans (`C`),
> rollback verification still ignored target/layer and candidate results conflated
> "would commit" with "is committed" (`D`), and the docs overclaimed what a
> snapshot and a rollback cover (`E`). Contract below; account in
> [RESULT.md](RESULT.md) §3.

> **Third cycle (2026-09-26, final blockers).** Two independently reproduced
> copper-integrity defects: the DRC error-code table mis-assigned
> ``DRCE_DANGLING_VIA`` (12, not 14) so drilled-hole spacing (14) was excluded
> from acceptance; and ``_execute_plan`` used post-step / pre-acceptance / final
> probes without checking them, so an unreadable read could still be reported as
> ``ok`` with copper committed. Contract below; account in [RESULT.md](RESULT.md).

Fork: <https://github.com/JaredReabow/PCBWorld> (fork of `LGAI-Research/PCBWorld`).
Local workspace: `/Users/leo/Documents/PCBWorld-reliability`.
Base commit: `b3d62f5c37e7528670d112e03d9a90029f23f4f3` (upstream `main`, "PCBWorld v1.0.1").
Branch: `feat/agent-reliability-actions`.
Engine (submodule, unmodified upstream `7a31e0c982a84fcda75be3ce69966027135bd7c1`) plus one
local patch — see D4.

## Why this phase exists

The upstream environment is honest about geometry but thin on *agent-facing reliability*: an
agent that issues a routing command gets back `success: bool` and a reward delta, not a
statement about what the engine now believes, and a failed multi-step attempt can leave partial
copper behind. A previous external run (Helix_Control_V3, recorded in the reference workspace,
not part of this repository) hit exactly those gaps: action "success" that added segments without
closing a connection, probes that did not restore state between candidates, and routing-time
DRC that ran with default netclass rules while validation-time DRC ran with the project's
custom rule file.

This phase ships executable reliability features inside the real PCBWorld fork, not a wrapper
around somebody else's board.

## Non-goals (explicit)

Routing any Helix board; changing placements, physical policy, or any fabricated design;
EasyEDA harness work; vision rollout; substituting a different or stronger model; promising
whole-board success; building the PCBWorld-Engine fork itself (D4 is a patch plus an honest
native status).

## Deliverables

### D1 — Structured, validated actions next to the existing text/RL API

* `pcb_world/agent/actions.py`: versioned `StructuredAction` schema (`schema_version`).
* Named routing modes `mark_obstacles` / `shove` / `walkaround` mapped **centrally** through
  `pcb_world.core.action_schema.MODE_LETTER_TO_INT` → `m=0, p=1, w=2`. The mapping is never
  re-typed in the agent layer (module-ownership rule).
* Validation before any mutation: non-finite / malformed coordinates, coordinates outside the
  board outline, unknown layer, non-copper layer, unknown mode, and wrong-phase operations
  (`make_line`/`make_via`/`finish` without an active route) all raise `InvalidActionError`
  with a machine-readable reason — nothing is dispatched to C++.
* Backwards compatibility: `StructuredAction.to_env_action()` emits exactly the legacy dict
  (`action_type` + params), so both the RL index path and the LLM text path keep working.

### D2 — Authoritative state snapshots

* `pcb_world/agent/state.py`: `AgentSnapshot` after every action **and** every failure, carrying
  a monotonically increasing `revision`, an opaque `token`, route-active/head/target/layer when
  known, `allowed_next_actions`, the classified `outcome`, and structured `evidence`.
* Outcome vocabulary: `ok`, `already_connected`, `no_active_route`, `stale_state`,
  `invalid_action`, `routing_failed`, `unsupported`, `unverified`.
* A stale `token` is rejected **before** mutation.
* Nothing is invented: if the engine raises while reading session state, the snapshot is marked
  `unverified` and carries the error; it never reports zero DRC/zero violations it did not read.
  Obstacle detail is reported only when the backend supplies it, otherwise `unknown`.

### D3 — Transactional connection attempt

* `pcb_world/agent/session.py`: `AgentSession.connect_targets(start, target, mode, waypoints=,
  layer_plan=)` runs a deterministic start → advance → finish sequence.
* One engine checkpoint per attempt; full `restore()` on failure or on "not verified connected",
  which also reverts shove-displaced copper and controller session state (the engine checkpoint
  stores board + engine config + routing session).
* Candidate probing takes a checkpoint per candidate and restores after each, so every candidate
  is evaluated from an identical state.
* Success is decided by **actual connectivity** of the requested copper groups
  (`KiCadEngine.get_pad_groups()` / `get_connected_points()`), never by segment counts, reward,
  or dispatch success.
* Waypoint progress inside a transaction is kept when the whole connection has not closed yet —
  a useful advance is not rolled back just because the target is still open.
* C++ engine use is serialised through a process-wide re-entrant lock.
* If the backend cannot prove connectivity or cannot roll back, the result is `unsupported` /
  `unverified` and **nothing is committed**.

### D4 — Rule consistency (interactive routing vs validation)

* Defect: `engine/kicad-patches/rl/pns_rl_router.cpp` creates the routing-time `DRC_ENGINE` with
  `InitEngine(wxFileName())` (default netclass rules) even when a `.kicad_pro` was loaded,
  while `runDRCEngine(rules_path)` loads the explicit rule file. Routing can therefore ignore
  constraints that validation then reports.
* Fix: resolve the project's design-rules path exactly as pcbnew's
  `PCB_BASE_EDIT_FRAME::GetDesignRulesPath()` does (`<board>.kicad_dru` resolved through
  `PROJECT::AbsolutePath`), load it in the routing-time engine, expose
  `was_routing_rules_loaded_from_file()` / `get_routing_rules_path()`, and make a *requested*
  rule file that fails to load raise instead of silently degrading.
* Fail closed in Python: every mutating call proves the applicable context first (see the
  [correction contract](#correction-contract) — the original `require_rules=` opt-in and the
  caller-supplied `enforcement_verified` boolean were both removed after review).
* Verification status is reported honestly: the patch is written and shipped as a reviewable
  patch file, and native verification is reported as achieved or blocked depending on whether a
  rebuilt engine could be produced locally in this phase.

### D5 — Regression harness

* `tools/reliability/check_phase.sh` — the one documented command. Runs unit coverage always,
  native-engine coverage when a router build is available, and labels the two.
* Unit tests: mode naming equivalence, legacy back-compat, invalid/stale actions do not mutate,
  cancelled-route recovery, failure-state classification, transaction rollback bookkeeping,
  identical-state candidate probing, rule-context fail-closed.
* Native tests (real engine, synthetic fixtures only): direct connect, obstacle/walkaround,
  shove of an unlocked existing track, locked blocker, legal + illegal via, alternate layer,
  exact geometry + connectivity rollback, blocked-target rollback, and a custom-rule check that
  a synthetic `.kicad_dru` actually changes the routing-time result.
* Geometry comparison covers coordinates, layer, net, width and drill — never counts alone.
* Native coverage is skipped loudly (and no acceptance is claimed) when the engine is missing.
* No network and no model calls anywhere in the harness.

### D6 — Documentation, history, changelog

`PLAN.md` / `CHECKPOINT.md` / `RESULT.md` under `docs/agent-work/reliability/`, an append-only
`HISTORY.md`, a `CHANGELOG.md` entry, and a short `docs/agent-work/reliability/README.md`
pointing at the user-facing entry point.

## Roadmap beyond this phase

Phase 1 is accepted. The roadmap below is the *next planned phase* (integration),
in the order the checkpoint resumes it. Items marked (shipped) landed in phase 1.

1. **Next phase — LLM policy integration.** Feed the transaction API into
   `methods/llm_agent` as a first-class tool so the agent emits `connect_targets`
   instead of five mechanical actions, and measure the change on the D3 split.
   The JSON surface (`tool_api.py`) already exists for this.
2. **Transaction-level DRC delta reporting — shipped in phase 1.** Violations
   introduced by a committed attempt, compared by item identity, so a commit is
   refused on a rule regression and not only on disconnection (`drc_gate.py`).
3. Candidate ranking: order probed strategies by their DRC delta (fewest added
   violations, then fewest steps) instead of a sequential loop.
4. **PNS custom-rule enforcement** — the engine-side change that would let the
   router honour a project rule file on the copper it places, removing the need
   for post-route rejection.
5. Extend the three unmapped EasyEDA clearance rows (Slot Region, Line,
   Text/Image), which have no KiCad object type yet.
6. Native failure-taxonomy evidence: capture the engine's own obstacle reason
   where the PNS walkaround can supply one, replacing `unknown`.

## Correction contract

Scope: the same workspace, branch and fork; no new features beyond making the
existing reliability claims true and testable.

| # | Finding | Correction |
|---|---|---|
| 1 | Staleness compared one stored token only | Tokens are `session_id.revision.fingerprint`, where the fingerprint covers copper **and** routing session state; `_token_status` re-reads the live engine under the lock. Regression tests cover a same-count external geometry change and a token from another session. `token=None` is refused unless the session was constructed with the explicitly documented `require_tokens=False`; the model-facing tool adapter always requires a token. |
| 2 | Loose validation | `schema_version` must be in `SUPPORTED_SCHEMA_VERSIONS`; layers and net ids go through a strict integer coercion (no bools, no truncation); unknown action/parameter/field names are refused; empty or short endpoints are refused as `malformed_coordinate`; the structured JSON form `{name, params, schema_version}` is consumed by `coerce_action`; a session without a board path derives its rule context from the engine instead of raising. |
| 3 | Rule safety opt-in/bypassable | The caller boolean is gone. Every mutating public API proves the applicable context automatically (`assert_rules_applicable`): engine can report it, `.kicad_pro` was read from disk, no rule-load error, and when a `<board>.kicad_dru` exists the routing engine loaded exactly that file. Unknown context fails closed. Enforcement is no longer claimed at all — see #4. |
| 4 | Acceptance by counts / by a flag | Mutations are accepted only if the engine's own DRC, under the proven context, reports **no new relevant violation** (`pcb_world/agent/drc_gate.py`). Violations are compared by stable identity (item UUIDs, else geometry + nets); connectivity findings are excluded as progress. Rejection rolls the mutation back and verifies the restoration. |
| 5 | Transaction integrity | `canonical_rows` quantises to nanometres, sorts items, and includes a via's full layer span; rollback verification checks copper **and** session state (route active / head / net); the properties the mirrors cannot expose are declared (`unverifiable_properties()`). `atomic=True` is the transaction default, `provisional=True` is the explicit opt-in for keeping verified progress; results describe the restored final state; a rollback that cannot be verified quarantines the session, and candidate probing halts. Checkpoint release is in `finally`. |
| 6 | Endpoint and session contracts | `endpoint()` resolves the copper cluster through the engine's own connectivity query and takes the net from the pads/vias inside it: no copper, no resolvable net or a multi-net cluster is refused (`endpoint_unknown` / `endpoint_ambiguous`). `allowed_actions` advertises no mutation for unknown state or a dirty session, and does not offer `connect_targets` while a route is open. A connectivity rebuild failure is `unverified`, never a verified action. `from_env` requires `acknowledge_env_desync=True`. |
| 7 | Shove proof | The native shove test uses a bar that the router demonstrably displaces, asserts NET2 is in the candidate's `changed_nets` **before** the restore, and asserts the restored rows are byte-identical. Mode coverage is renamed and documented as dispatch-only. |
| 8 | Gate integrity | `check_phase.py --strict` requires a native build, at least `EXPECTED_NATIVE_TESTS` executed tests, zero skips, and treats load/provenance failures as errors. A build that exists but cannot be verified now fails the native fixture instead of skipping it. The evaluation logic is unit-tested (`tests/agent/test_gate_integrity.py`). |
| 9 | Usability | `pcb_world/agent/tool_api.py` implements the advertised JSON surface (snapshot / act / connect_targets, token required, structured refusals) with a schema description, and `tools/reliability/demo_structured_actions.py` is a model-free transcript of a full session. |

## Second correction contract

| # | Finding | Correction |
|---|---|---|
| A | An error after the checkpoint (connectivity rebuild, unreadable post-probe, unexpected exception) could leave copper | `_act_gated` wraps the whole post-checkpoint region: every failure path calls `_rollback_action`, which restores, verifies copper **and** session, invalidates the DRC cache, and quarantines when the restore cannot be established. Checkpoint creation is guarded (nothing mutated yet), the transaction loop releases every step handle in `finally`, and a connectivity result that cannot be read is never usable progress, not even with `provisional=True`. `atomic=False` without `provisional=True` is refused as `underspecified`. |
| B | `run_drc` returning `[]` while a rule file failed to load, and a caller context that could point at a missing file while the engine had a real one | `take_violations` re-reads the engine's DRC rule-load channel after **every** run and raises `DrcContextError` on a reported failure, a vanished file or a changed context identity (rule content, project file, pads). `DrcGate` stores that identity with the baseline and refuses a comparison across contexts. `assert_rules_applicable` now treats the engine's reported file as authoritative: a caller context can only agree with it or be refused — never downgrade it to implicit rules. |
| C | The adapter accepted `token=None` on an opted-out session, `waypoints=None` raised, an infinite layer overflowed, `provisional="false"` read as true, and refusals could carry NaN | `tool_api` requires a non-empty token string independently of the session policy, validates point shapes (finite numbers only), `waypoints` sequences and booleans before dispatch, and surfaces a top-level `reason`. `_point3` returns `None` (JSON `null`) instead of NaN; `already_connected` is reported as a successful call. |
| D | Rollback verification ignored target/layer; candidate results claimed `committed=True` after a restore; "no change" and "couldn't restore" shared `committed=False` | `_restore_and_verify` compares and reports `route_active`, head, **target**, current net and **layer** (idle sentinels normalised to `None`). `committed` is three-valued (`None` = unknown) with a `copper_state` field; probe results carry `evaluated` / `would_commit` and always report `committed=False` for the restored board. |
| E | Docs claimed full-board fingerprinting and byte-identical rollback; `snapshot(include_geometry=False)` minted a token that was stale by construction | Docs now state the ownership assumption (exclusive engine; outside mutations are detected only in the tracked properties) and that rollback is exact over the **visible** rows. A geometry-less snapshot returns an empty token and advertises no actions. |

## Third-cycle contract

| # | Finding | Correction |
|---|---|---|
| 1 | `DRCE_DANGLING_VIA = 14` (actual 12) meant code 14 — drilled holes too close — was treated as connectivity noise and excluded from acceptance | The constants now match the pinned engine (`UNCONNECTED=1`, `DANGLING_VIA=12`, `DANGLING_TRACK=13`, `DRILLED_HOLES_TOO_CLOSE=14`, `DRILLED_HOLES_COLOCATED=15`), are documented as **positional values of the pinned engine, not a stable API**, and are bound to it by `parse_drc_enum` + a native test that reads `build_rl/kicad_src/pcbnew/drc/drc_item.h`. Native records the engine really produced are used to prove 14 is relevant and 12 is the connectivity signal, and a new code-14 record is rejected with rollback. |
| 2 | `_execute_plan` ignored unreadable post-step, pre-acceptance and final probes: the result could report `ok / accepted / committed` while the snapshot said `unverified`, leaving copper | Every post-step, pre-acceptance and final read is checked; an unreadable one goes through `_unreadable_transaction` (invalidate gate → restore → verify → quarantine when the restore cannot be established), and `_finish` enforces the invariant centrally so no result can contradict its snapshot: an unreadable final probe forces `unverified`, `accepted=False`, and `committed=None` with `copper_state='retained_unknown'` when copper was being kept. |
