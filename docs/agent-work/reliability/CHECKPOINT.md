# CHECKPOINT — agent reliability layer (phase 1)

## Current state — 2026-09-28 (phase 10)

Phase 10 attributes the refusals phase 9 found, aims two generic bounded families
at them, and spends one bounded campaign on the result; see
[phase10/PLAN.md](phase10/PLAN.md) and [phase10/RESULT.md](phase10/RESULT.md).

Changed this cycle: `NetPair.offered_key` in `pcb_world/agent/observations.py`;
`clearance_extent_probes`, the `drc_extent` family, `AttemptRecord.offered_pair_key`
and `offered_edge_key` in `pcb_world/agent/scheduler.py`; the configured
refusal budgets, the `_refusal_unanswered` correction and `_hole_escape_seeds` in
`pcb_world/agent/runner.py`; the `unattempted_cap_observed` rename and the
offered-key attribution in `tools/reliability/failure_taxonomy.py`
(`tests/agent/test_coverage_and_clearance.py`,
`tests/agent/test_failure_taxonomy.py`). The engine pin is untouched, so
`patches/engine/` is unchanged and `check_engine_patches.py` is still byte-equal.

Gate: `bash tools/reliability/check_phase.sh --strict` → exit 0, 462 unit + 116
native, no skips; `tools/check_separation.py` 4/4; `git diff --check` clean.
Nothing committed or pushed.

Board outcome: **nothing promoted.** The campaign reached 16 of the 43
closed-but-refused edges with refusal-derived plans (327 refusal-derived records,
against phase 9's 39 over 7 pairs) and closed zero connections; every record's
copper state is `restored`, `best_board_sha256` and the accepted generation are
byte-identical, and the accepted pointer is unchanged. The diagnosis that aimed
the families is the phase's main finding: these refusals are dominated by the
plan's own via against a **foreign copper zone**, and neither new family models a
zone boundary, so the next phase needs a zone-aware placement question rather
than another waypoint family.

## Current state — 2026-09-28 (phase 9)

Phase 9 adds a complete, hash-bound taxonomy of every outstanding connection on
the V3 board and two generic strategies chosen from it; see
[phase9/PLAN.md](phase9/PLAN.md), [phase9/TAXONOMY.md](phase9/TAXONOMY.md) and
[phase9/RESULT.md](phase9/RESULT.md).

Changed this cycle:
`tools/reliability/failure_taxonomy.py` (new, with
`tests/agent/test_failure_taxonomy.py`); `clearance_search_probes` and the
`drc_clear` candidate family in `pcb_world/agent/scheduler.py`; the
evidence-before-proximity candidate order and `RoutingRunner._refusal_unanswered`
in `pcb_world/agent/runner.py`; `scan_net_pairs(..., fresh=...)` and
`coverage_promoted` in `pcb_world/agent/observations.py`;
`AttemptHistory.records_on` / `worked_on` and `RunnerConfig.coverage_first`
(`tests/agent/test_coverage_and_clearance.py`). The engine pin is untouched, so
`patches/engine/` is unchanged and `check_engine_patches.py` is still byte-equal.

Gate: `bash tools/reliability/check_phase.sh --strict` → exit 0, 451 unit + 116
native, no skips; `tools/check_separation.py` 4/4; `git diff --check` clean.
Nothing committed or pushed.

Board outcome: **nothing promoted.** One bounded campaign over the accepted
generation (1 779.5 s across two segments, zero planner requests) plus two short
targeted verifications closed no connection; `best_board_sha256` and the accepted
generation are byte-identical and the accepted pointer is unchanged. The campaign
did move reach — 3 → 116 distinct pairs attempted in the same window, and 15
previously-unattempted connections received their first attempt — and it found
two real defects (coverage counted the wrong history; refusal-derived candidates
were truncated away by the candidate limit).

## Current state — 2026-09-27 (phase 8)

The last private campaign's `drc_unavailable` + unverifiable rollback is
diagnosed, reproduced and fixed; see
[phase8/DECISION.md](phase8/DECISION.md). It was the runner's own budget clamp and
not an engine defect: a native call dispatched near the run's time limit was
reaped at its deadline, and the reaped child held the transaction's checkpoint.

Changed this cycle: the bounded engine lease (`RunnerConfig.transaction_lease_s`,
`_transaction_lease`, `_lease_seconds`, the `metrics["engine_leases"]` accounting)
so the soft scheduling deadline cannot truncate a transaction, the scan, the reads
that select one or the closing verification; the `time_limit_headroom` /
`time_limit_completed_attempt` boundary stops with
`RunnerConfig.{attempt_headroom_s,attempt_headroom_factor}`;
`AttemptRecord.{failure_exception,rollback_detail}`; `RunnerConfig.priority_nets`;
the `metrics["transplant"]` label for inherited records; and the
`tests/agent/fake_engine.py` hooks (`restore_exception`, `drc_fail_after_calls`,
`allowance_provider`/`advance_clock`/`drc_duration_s` accounting). The engine pin is
untouched, so `patches/engine/` is unchanged and `check_engine_patches.py` is still
byte-equal.

Gate: `bash tools/reliability/check_phase.sh --strict` → exit 0, 413 unit + 116
native, no skips; `tools/check_separation.py` 4/4. Nothing committed or pushed.

Current work (2026-09-27) is the complete item inventory plus the deterministic
duplicate-UUID repair; see
[phase5-kicad/CHECKPOINT.md](phase5-kicad/CHECKPOINT.md) § *Current state —
2026-09-27*. Everything below is the accepted phase-1 record.

Updated: 2026-09-26, at acceptance. The pre-correction and pre-fault-injection
states remain documented in the review history above and in the working tree.

Status: **PHASE 1 ACCEPTED** (2026-09-26). Astra verified the pinned DRC enum
mapping, code-14 relevance / code-12 connectivity classification, and the exact
unreadable-transaction reproduction (`accepted=False`, `committed=False`, zero
tracks, verified rollback); `git diff --check` passed. Nothing committed or
pushed.

Contract: [PLAN.md](PLAN.md) + [correction contract](PLAN.md#correction-contract)
+ [second](PLAN.md#second-correction-contract) and
[third](PLAN.md#third-cycle-contract) cycles. Account and evidence:
[RESULT.md](RESULT.md).

| Item | State |
|---|---|
| Fork | `https://github.com/JaredReabow/PCBWorld` (parent `LGAI-Research/PCBWorld`) |
| Workspace | `/Users/leo/Documents/PCBWorld-reliability` |
| Base commit | `b3d62f5c37e7528670d112e03d9a90029f23f4f3` (upstream `main`, v1.0.1) |
| Branch | `feat/agent-reliability-actions` |
| Remotes | `origin` = fork, `upstream` = `LGAI-Research/PCBWorld` |
| Engine | `engine/` at `7a31e0c` + local patch (`patches/engine/0001-routing-rule-context.patch`) |
| Engine build | private copy `build_rl/`, C++ content hash `2613fb07`, provenance verified |
| Python layer | `pcb_world/agent/{actions,state,rules,drc_gate,session,tool_api}.py` |
| Harness | `tools/reliability/check_phase.{sh,py}` (+ `--strict`), demo script |
| Phase gate | `bash tools/reliability/check_phase.sh --strict` → exit 0 (unit 159, native 24, no skips) |

## Next phase (resume here)

Phase 2 (**resumable routing agent**) is implemented and back for review:
[phase2/CHECKPOINT.md](phase2/CHECKPOINT.md) and
[phase2/RESULT.md](phase2/RESULT.md). Phase 1 itself stays accepted and unchanged;
the phase-2 work adds `pcb_world/agent/{observations,scheduler,runner}.py`, the
`methods/llm_agent` integration, `tools/reliability/route_agent.py` and the private
V3 pilot. The list below is the phase-1 wishlist that phase 2 partly addressed; it
is kept as written.

No further review loops: phase 1 is accepted. The next planned phase is
**integration**, in this order:

1. **LLM policy integration** — make `connect_targets` a first-class tool in
   `methods/llm_agent` (`pcb_world/agent/tool_api.py` already defines the JSON
   envelope) and measure it on the D3 split.
2. **Candidate ranking** — order probed strategies by DRC delta (fewest added
   violations, then fewest steps) instead of sequential probing.
3. **PNS custom-rule enforcement** — the engine-side change that lets the router
   honour a project rule file on the copper it places, so post-route rejection is
   no longer the only guard.
4. **Mirror coverage** — track type/arc mid-point, lock flag and solder-mask
   margin are not exposed by the wire mirrors; widening them would strengthen
   rollback verification beyond the visible rows.

Open limitations that stay documented (not blockers for phase 1 acceptance) are
listed in [RESULT.md](RESULT.md) §5.

## Reproduce (evidence as accepted)

```bash
cd /Users/leo/Documents/PCBWorld-reliability
bash tools/reliability/check_phase.sh --strict     # exit 0: 159 unit + 24 native, no skips
.venv/bin/python tools/reliability/demo_structured_actions.py
```

## Reproduce

```bash
cd /Users/leo/Documents/PCBWorld-reliability
bash tools/reliability/check_phase.sh --strict          # acceptance evidence
bash tools/reliability/check_phase.sh                   # same, native skip allowed
.venv/bin/python tools/reliability/demo_structured_actions.py
PYTHONPATH=$PWD .venv/bin/python -m pytest tests/agent/test_fault_injection.py -q -o addopts=
git -C engine apply ../patches/engine/0001-routing-rule-context.patch   # engine fix
```

## Fault-injection cycle (what the gate now covers)

`tests/agent/test_fault_injection.py` reproduces every failure root found with
targeted injection: a connectivity rebuild that fails after `start_route`; an
engine that goes unreadable right after a mutation (recoverable and persistent);
a `run_drc` that returns `[]` while reporting a rule-load failure; a rule file
that changes or disappears between the baseline and the verification run; a
caller context pointing at a missing file while the engine holds a real one; the
adapter being offered `token=None`, `waypoints=None`, an infinite layer or
`provisional="false"`; a restore that brings copper back but not the router
session; and a partial snapshot that used to mint a stale token.

## Final-blocker cycle

* **DRC error-code table corrected** against the pinned engine
  (`build_rl/kicad_src/pcbnew/drc/drc_item.h`): `DANGLING_VIA=12`,
  `DANGLING_TRACK=13`, `DRILLED_HOLES_TOO_CLOSE=14`. The previous table treated
  hole spacing as connectivity noise (excluded from acceptance) and 12 as
  gating. `parse_drc_enum` + `tests/agent/test_native_drc_enum.py` bind the
  constants to that header at test time, and native records prove 14 is rejected
  while 12 is not.
* **Unreadable transaction probes** now route through
  `_unreadable_transaction` (invalidate gate → restore → verify → quarantine),
  and `_finish` enforces the invariant that a result may never claim
  `ok`/`accepted`/`committed` while its snapshot is `unverified`.

## Local environment notes

* `.venv/` carries pytest + numpy + pyyaml + gymnasium; the training stack
  (torch/vllm/ray) is absent, so a few unrelated upstream tests cannot run — see
  RESULT.md §6.
* `.local-bin/wx-config` is a Homebrew shim symlink used only to re-configure the
  engine build; it is git-ignored, as are `build_rl/` and `.venv/`.
