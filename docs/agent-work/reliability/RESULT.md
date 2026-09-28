# RESULT — agent reliability layer (phase 1, accepted)

Status: **PHASE 1 ACCEPTED** (2026-09-26). Astra verified the pinned DRC enum
mapping (code 14 relevant, code 12 the connectivity signal), the code-14 rejection
path and the exact unreadable-transaction reproduction (`accepted=False`,
`committed=False`, zero tracks, verified rollback); `git diff --check` passed.
Review history: three correction cycles preceded acceptance — nine findings
([correction contract](PLAN.md#correction-contract)), five faults from targeted
fault injection ([second cycle](PLAN.md#second-correction-contract)), and two
copper-integrity defects ([third cycle](PLAN.md#third-cycle-contract)). All three
sets are fixed and listed with their evidence in §3.

**Acceptance is scoped.** What is accepted is this fork's first experimental
reliability phase, on **synthetic evidence only**: 159 unit + 24 native tests
(183 total) over generated boards, current APIs and the JSON demo. It is not a
whole-board routing result and not a live LLM policy integration. Implementation
stays local, uncommitted and unpushed.

| | |
|---|---|
| Fork | <https://github.com/JaredReabow/PCBWorld> (fork of `LGAI-Research/PCBWorld`) |
| Workspace | `/Users/leo/Documents/PCBWorld-reliability` |
| Base commit | `b3d62f5c37e7528670d112e03d9a90029f23f4f3` (upstream `main`, v1.0.1) |
| Branch | `feat/agent-reliability-actions` |
| Engine | submodule `7a31e0c` + local patch; private build `2613fb07`; reference checkout untouched (`cc526ce6`, `.so` mtime 00:58) |

## 1. What the layer now guarantees

Every claim below is enforced by code and has a test; anything that could not be
verified is named in §5 instead of being asserted.

* **A token binds to live state.** `session_id.revision.fingerprint`, where the
  fingerprint covers copper *and* routing session state. A foreign session's
  token, an older revision, or a board mutated behind the session's back (even
  with an unchanged item count) is refused before dispatch. `token=None` is
  refused unless a session was explicitly constructed with `require_tokens=False`;
  the JSON tool adapter always requires one.
* **Validation is strict.** Supported schema versions only; strict integer
  coercion for layers and net ids (bools and fractional values refused); unknown
  actions, parameters and fields refused with a stable reason; malformed or
  non-finite coordinates refused; the structured JSON form is consumed end to end.
* **The applicable rule context is proven, not requested.** Engine can report it,
  `.kicad_pro` was read from disk, no rule-load error, and a present
  `<board>.kicad_dru` is the file the routing engine loaded. Any gap fails closed
  (`unsupported`).
* **Copper is accepted only by native DRC delta.** No new *relevant* violation
  under that context, compared by identity, with connectivity findings treated as
  progress. Rejection rolls back and verifies the restoration.
* **Transactions are atomic by default.** Every step is checkpointed; a failed
  step is restored and verified by canonical copper (nanometre, sorted, includes
  via layer span) *and* session state; `provisional=True` is the explicit opt-in
  that keeps verified progress.
* **Failure is honest.** An unreadable engine is `unverified`; a rollback the
  backend cannot confirm quarantines the session (mutations refused, snapshot
  marked `dirty`, candidate probing halted) instead of reporting
  `committed=False` while copper remains.

## 2. Verification

| Command | Exit | Result |
|---|---|---|
| `bash tools/reliability/check_phase.sh --strict` | **0** | unit `159 passed`, native `24 passed`, `native_executed: 24`, no skips; evidence in `docs/agent-work/reliability/evidence/phase_check.json` |
| `PCBWORLD_KICAD_RL_MODULE_DIR=<empty> bash tools/reliability/check_phase.sh --strict` | **1** | `PROBLEM: native group did not run and --strict was requested` — a phase cannot be green with no native execution |
| same command without `--strict` | **0** | native reported as `skipped ... - no native acceptance claimed` |
| `.venv/bin/python tools/reliability/demo_structured_actions.py` | **0** | model-free transcript: snapshot → token-required refusal → `start_route` → stale-token refusal → `connect_targets` accepted with `added_relevant_count: 0` |
| `pytest tests/test_engine_api/ --ignore=tests/test_engine_api/test_connect.py` | **1** | `512 passed, 2 failed, 3 skipped` — unchanged from the first submission and reproduced against the **unpatched reference build** (§6) |

## 3. Findings, as fixed and evidenced

| # | Evidence in the tree |
|---|---|
| 1 token staleness | `tests/agent/test_state_and_rules.py::test_external_geometry_change_with_the_same_count_is_stale`, `::test_token_from_another_session_is_refused`, `::test_token_is_required_by_default`; `tests/agent/test_tool_api.py::test_act_tool_requires_a_token` |
| 2 strict validation | `test_modes_and_validation.py::test_unsupported_schema_version_is_refused`, `::test_fractional_or_non_integer_layers_are_refused`, `::test_structured_json_form_refuses_unknown_fields`; `test_transaction_unit.py::test_malformed_endpoint_is_refused_not_crashed`, `::test_session_without_a_board_path_still_has_a_context` |
| 3 rule safety | `test_state_and_rules.py::test_assert_rules_applicable_*` (5 refusal cases + implicit-rules pass); `test_transaction_unit.py::test_unanswerable_rule_context_refuses_every_mutating_call`, `::test_project_not_loaded_from_file_refuses_mutations`; native `test_native_rules.py::test_a_rule_file_that_fails_to_load_refuses_mutations` |
| 4 native DRC acceptance | `drc_gate.py` + `test_transaction_unit.py::test_new_relevant_violation_rejects_the_route_and_rolls_back`, `::test_preexisting_violation_is_preserved_not_charged_to_the_attempt`, `::test_connectivity_findings_do_not_gate_a_commit`; native `test_native_contract.py::test_default_via_that_violates_min_hole_is_rejected_and_rolled_back`, `test_native_rules.py::test_custom_rule_corridor_is_rejected_with_verified_restoration` |
| 5 transaction integrity | `state.py::canonical_rows` / `unverifiable_properties`; `test_state_and_rules.py::test_geometry_digest_detects_a_via_layer_span_change`, `::test_canonical_rows_are_order_independent_and_nanometre_exact`; `test_transaction_unit.py::test_unverifiable_rollback_quarantines_instead_of_claiming_success`, `::test_partial_progress_is_rolled_back_without_provisional` |
| 6 endpoints / session | `session.py::endpoint`; `test_state_and_rules.py::test_endpoint_refuses_a_point_with_no_copper`, `::test_endpoint_refuses_an_ambiguous_cluster`, `::test_endpoint_refuses_copper_without_a_resolvable_net`; `test_transaction_unit.py::test_transaction_refuses_to_attach_to_an_active_route`, `::test_from_env_requires_an_explicit_desync_acknowledgement` |
| 7 shove proof | `test_native_contract.py::test_shove_displaces_foreign_copper_and_probing_restores_it` — asserts `2 in shove.evidence["changed_nets"]` and then that the rows are identical to the pre-probe rows |
| 8 gate integrity | `tools/reliability/check_phase.py::evaluate_report`; `test_gate_integrity.py` (10 cases); `conftest.py` fails (does not skip) when a present build cannot be verified |
| 9 usability | `pcb_world/agent/tool_api.py` (+ `tool_schemas()`), `tests/agent/test_tool_api.py`, `tools/reliability/demo_structured_actions.py` |

### Second-cycle findings (fault injection), as fixed and evidenced

Every case below was reproduced by root against the previous build; the
regression lives in `tests/agent/test_fault_injection.py` (27 tests).

| # | Evidence in the tree |
|---|---|
| A unverified path kept copper | `::test_connectivity_failure_after_start_route_rolls_the_action_back` (restores, `tracks == []`, cache invalidated), `::test_connectivity_failure_with_unverifiable_restore_quarantines`, `::test_unreadable_post_probe_rolls_back`, `::test_a_persistently_unreadable_engine_quarantines_after_the_attempt`, `::test_provisional_does_not_override_unverified_connectivity`, `::test_checkpoint_failure_before_mutation_is_reported_without_change`, `::test_atomic_false_without_provisional_is_refused`, `::test_step_handles_are_released_on_an_exception_path`, `::test_validation_failure_never_reaches_a_checkpoint` |
| B DRC side-channel / context downgrade | `::test_a_reported_rule_load_failure_refuses_take_violations`, `::test_connect_reports_unsupported_when_drc_silently_falls_back`, `::test_rule_file_content_change_between_runs_is_refused`, `::test_rule_file_disappearance_between_runs_is_refused`, `::test_rules_cache_identity_includes_the_rule_content`, `::test_a_context_pointing_at_a_missing_file_cannot_erase_loaded_rules`; unit `test_state_and_rules.py::test_a_missing_context_can_never_downgrade_a_loaded_file` |
| C adapter strictness | `::test_adapter_requires_a_token_even_when_the_session_opts_out`, `::test_adapter_rejects_a_non_string_token`, `::test_adapter_validates_shapes_and_booleans_before_mutating`, `::test_adapter_waypoints_none_is_a_no_op_and_bad_geometry_stays_stable`, `::test_act_tool_refusals_are_json_serialisable_without_nan`, `::test_already_connected_is_a_successful_tool_call` |
| D result semantics | `::test_rollback_verification_notices_unrestored_session_state`, `::test_rollback_evidence_reports_the_state_it_compared`, `::test_probe_results_separate_would_commit_from_the_restored_board`, `::test_unknown_copper_state_is_not_reported_as_no_change` |
| E snapshot token honesty | `::test_a_partial_snapshot_mints_no_usable_token` |

### Third-cycle findings (final blockers), as fixed and evidenced

| # | Evidence in the tree |
|---|---|
| 1 wrong DRC enum | `tests/agent/test_drc_classification.py` (parser, constants, classification of 1/12/13 as connectivity and 5/14/15 as relevant, violation identity); `tests/agent/test_native_drc_enum.py::test_the_enum_matches_the_engine_source_this_build_came_from` (parses `build_rl/kicad_src/pcbnew/drc/drc_item.h`), `::test_a_real_hole_spacing_violation_is_relevant` (engine-produced code 14), `::test_a_real_dangling_via_is_a_connectivity_signal` (engine-produced code 12), `::test_the_gate_rejects_a_new_hole_spacing_violation` (the new record rejects the route and rolls back) |
| 2 unreadable transaction probes | `tests/agent/test_fault_injection.py::test_transaction_unreadable_post_route_read_rolls_back` (root's exact reproduction), `::test_transaction_late_post_drc_unreadability_cannot_report_success`, `::test_transaction_persistent_late_unreadability_quarantines`, `::test_finish_refuses_to_claim_success_on_an_unreadable_probe` |

Compact reproduction output for the two blockers:

```
enum: UNCONNECTED=1 DANGLING_VIA=12 DANGLING_TRACK=13 DRILLED_HOLES_TOO_CLOSE=14 (header parity OK)
native: code 14 "Drilled hole too close to other hole" -> relevant (rejected); code 12 "Via is not connected" -> connectivity
root repro -> unverified | accepted False | committed False | copper_state restored | snapshot unverified | tracks 0 | dirty False
late post-DRC (persistent) -> unsupported/unverified | accepted False | committed None | copper_state retained_unknown | dirty True
```

Compact reproduction output (the same scenarios root ran):

```
A: unverified tracks [] rollback True live_ckpts 0
B: unsupported drc_unavailable | DrcContextError: DRC did not run under the requested rules
C: False token_required tracks []
D: evaluated True would_commit True committed False copper_state restored
E: token '' allowed ()
```

## 4. Measured native behaviour (synthetic boards)

* Legal coverage: direct connect, already-connected short circuit,
  obstacle/walkaround (the wall is not moved), alternate-layer via (with an
  explicitly legal 0.6/0.3 mm via), locked obstacle walked around with unchanged
  geometry, cancelled-route recovery, and all three named modes dispatched.
* Rejection coverage: a default (0.25 mm drill) via is refused with
  `Hole size out of range (board setup constraints ...)`
  (`[rule values redacted]`), `drc_regression`, rollback verified, tracks and vias back to zero.
  A 1.0 mm clearance rule over a 1.0 mm corridor is refused the same way, with the
  pre-existing DRC picture restored exactly.
* Shove: the shove candidate displaced NET2 (`changed_nets: [1, 2]`) and the probe
  restored every row.
* Rules: the routing-time engine reports the project's `.kicad_dru`; the same
  untouched copper is clean under the default rules and in violation under a
  synthetic 1.0 mm rule (the validator honours the file); a malformed rule file is
  refused; a requested-but-missing rule file is surfaced by `run_drc` and refuses
  the next mutation.

## 5. Remaining limits (stated, not hidden)

1. **The router still does not enforce a rule file on the copper it places.**
   Measured: with a 1.0 mm rule loaded, it routes a ~0.25 mm-clearance path. The
   mitigation is the acceptance gate (nothing that violates the context is kept),
   not a PNS fix. Removing the gate would require the engine-side change described
   in `patches/engine/README.md`.
1a. **DRC error codes are positional, not a stable API.** The gate's constants are
   the values of `enum PCB_DRC_CODE` in the pinned engine build source; they are
   documented as such and checked by a native test that parses that header. A
   different engine build (or one whose copied source is absent) makes the native
   enum test *fail* rather than silently mis-classify — the fail-closed answer,
   since a wrong mapping is what let code 14 through as "connectivity noise".
2. **Ownership assumption.** The layer assumes it owns the engine. A token binds
   session id, revision and a fingerprint of the **tracked** properties (visible
   copper rows + routing session state); the DRC gate binds rule-file content,
   project file and pad identity. Changes outside those — a zone refill, a board
   design-setting edit, a netclass change made directly through the C++ API —
   are *not* detected. Do not mutate the engine behind a session.
3. **What rollback verification cannot see.** The wire mirrors expose track
   endpoints/layer/net/width and via position/span/diameter/drill — not track
   type or arc mid-points, the item lock flag, or solder-mask margins. Rollback is
   therefore exact over the **visible rows plus session state**, which is what the
   evidence says; it is not a byte-identical board restoration. The unverifiable
   properties are listed in `unverifiable_properties()` and travel in every
   rollback evidence block.
4. **Endpoint identity is conservative by design.** A pad-less copper island, or a
   cluster holding two nets, is refused rather than guessed at.
5. **Cost.** The gate runs one DRC per mutating call (the baseline is cached while
   the copper and the rule context are unchanged). Correctness-first; a large
   board pays for it.
6. **Env integration is deliberately unsupported.** `AgentSession.from_env`
   requires `acknowledge_env_desync=True`; the docs no longer suggest mixing
   `env.step` with session calls.
7. **Not attempted here:** wiring `connect_targets` into `methods/llm_agent` as a
   first-class tool, DRC-delta ranking across candidates, and a PNS-side fix.

### Scope of the accepted evidence (restated plainly)

* **Synthetic, scoped evidence.** 159 unit + 24 native tests, 183 total, all over
  boards generated by `tests/agent/synthetic_boards.py`; `check_phase.sh --strict`
  exits 0 with no skips, and the demo drives the current APIs
  (`AgentSession`, `pcb_world/agent/tool_api.py`) end to end without a model.
* **No whole-board claim.** Nothing here says a real board routes completely, or
  that any production design is manufacturable.
* **No live LLM policy integration.** `connect_targets` is exposed and documented
  as a JSON tool, but no `methods/llm_agent` policy emits it yet.
* **No PNS custom-rule fix.** The router still does not enforce a project rule
  file on the copper it places; post-route DRC acceptance is the guard, and it
  refuses rather than keeps violating copper.
* **Exclusive engine ownership.** Outside mutations are detected only for the
  tracked properties (visible copper rows, routing session, rule/project file
  identity, pads); zones, board design settings and direct netclass edits are not
  fingerprinted.
* **Partial mirrors.** Track type/arc mid-point, the item lock flag and
  solder-mask margins are not exposed by the wire mirrors, so rollback is exact
  over the visible rows plus session state rather than byte-identical.

## 6. Environment limitations (not caused by this change)

Two upstream engine-API tests fail on this host and reproduce identically against
the unpatched reference build: `test_ipc_server_rss_budget.py` (the RSS reader is
Linux-only, so `rss_mb()` is `None` on macOS) and
`test_board_outline_shapes.py::…twin_bit_identical` (`ModuleNotFoundError: torch`).
`tests/test_engine_api/test_connect.py` cannot be collected (no `kipy`), and the
torch-dependent cases of `test_constant_consistency.py` cannot run here. The one
upstream test touched is `test_router_provenance.py`, whose assertion no longer
trips on the Python 3.13 traceback echoing a `-c` snippet's source line; it still
proves the refusal precedes any board load.

## 7. Not claimed

* No commit, push, PR or publication; the working tree is the deliverable.
* No claim that the patched engine is release-validated; the patch is applied to
  this workspace's engine submodule and its private build copy only.
* No private board, log, rule file or API key entered this repository; all
  fixtures are generated by `tests/agent/synthetic_boards.py`.
* No model or network use in any test, harness or demo.
* Whole-board routing success is not claimed anywhere.
