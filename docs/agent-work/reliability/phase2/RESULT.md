# RESULT — phase 2: resumable end-to-end routing agent (with a V3 pilot)

Phase 1 is unchanged and still accepted. This phase adds the loop that turns the
phase-1 safety API into an agent that can be pointed at a real board, stopped,
and resumed, and it measures that loop on the private Helix Control V3 board.

Contract: [PLAN.md](PLAN.md). Handover state: [CHECKPOINT.md](CHECKPOINT.md).
Phase-1 account: [../RESULT.md](../RESULT.md).

| | |
|---|---|
| Workspace | `/Users/leo/Documents/PCBWorld-reliability` |
| Branch | `feat/agent-reliability-actions` (base `b3d62f5`, v1.0.1) |
| Public gate | `bash tools/reliability/check_phase.sh --strict` → exit 0 (unit 225, native 30, no skips) |
| Private pilot | `/Users/leo/Documents/Helix_Control_V3_pcbworld_improved` (boards, prompts, logs, reports) |
| Git state | nothing committed, pushed or staged |

The honest headline: the agent is implemented, tested and resumable, and on the
V3 pilot it **closed no connection**. It did not fail to run — it ran, spent its
bounded budget on the right pairs, and the pairs it reached need either a richer
deterministic candidate set or an engine capability it does not have. That
outcome, the evidence for it, and the cost model are the deliverable (see §4-§5).

## 1. What the agent now does

`RoutingRunner` (`pcb_world/agent/runner.py`) drives one loop:

1. load the board, build the layer mapping **from board evidence**
   (`LayerResolver`), take a baseline DRC under the proven rule context;
2. enumerate outstanding net pairs from the ratsnest
   (`observations.enumerate_net_pairs`), routable-first and capped per net;
3. generate deterministic candidates in code — direct walkaround, shove,
   bounded detours, layer change (`scheduler.generate_candidates`) — and rank
   them by acceptance, connectivity, fewest added relevant findings, fewest vias,
   least added length, least foreign-net disturbance, fewest steps;
4. apply the best candidate through the phase-1 transactional session (token,
   schema, endpoint identity, rule context, native DRC acceptance, verified
   rollback). Nothing is applied outside that API;
5. only when the deterministic set cannot close a pair, ask the planner, once per
   pair, for that pair alone;
6. checkpoint the best verified board, save run state atomically, and stop on a
   limit or on a bounded stall.

Resume is provenance-checked: run state records board/project/rules hashes and the
engine build hash, and `RunState.load` refuses a mismatch
(`ProvenanceMismatchError`) rather than continuing against a different board.

Planner input is compact by construction. `observations.compact_observation`
renders one pair (endpoints, components, nearest obstacles, legal
layers/widths/vias, progress, DRC delta, prior attempts) under a character budget;
on V3 it renders to ~4.7k characters. The runner never sends a whole-board dump.

## 2. Verification

```bash
cd /Users/leo/Documents/PCBWorld-reliability
bash tools/reliability/check_phase.sh --strict     # exit 0: 225 unit + 30 native, no skips
PYTHONPATH=$PWD .venv/bin/python -m pytest tests/agent -q -o addopts=     # 255 passed
git diff --check                                   # clean
```

`--strict` is the acceptance mode: the native group must execute with no skips and
a load/provenance failure is an error. The run writes
`docs/agent-work/reliability/evidence/phase_check.json` (command, exit status,
counts, strict verdict). Last run at the time of writing:
unit `225 passed in 3.73s`, native `30 passed in 11.03s`, `exit_status 0`.

The gate is deliberately not the whole story: the unit group proves the contract
against a test double, and the native group proves it against a real
`kicad_rl_router` build over synthetic boards. Neither is evidence about V3 — §4
is.

## 3. Behaviour fixed in this phase, with the reason

Each of these was a defect found by running the loop on the real board, not by
inspection:

| Finding | Fix |
|---|---|
| Per-candidate cost was dominated by re-running a full ~41 s DRC after every rollback | `DrcGate.seed` re-seeds the DRC cache after a **verified** rollback, so an unchanged board is not re-costed |
| A candidate emitted a zero-length `line` step that the engine refused, discarding an otherwise valid plan | The redundant step is dropped before dispatch (`_head_matches`) |
| A stale `routing_target` from the engine could fail an otherwise-valid commit | `routing_target` is now **advisory**: compared and reported, listed in `unverifiable_properties()`, not fatal |
| Sub-0.25 mm cross-layer pairs were attempted forever | Classified `coincident_cross_layer`; the runner records `unsupported_pair` and moves on |
| A planner reply that failed to parse lost its token accounting | Usage is recorded on failed replies too |
| The saved artifact carried the engine's re-serialized project (5121 B → 13300 B), a different set of settings from the one the run was validated against | `_save_best` copies the **input** project and rules next to the board verbatim |
| A resume with a different `--run-dir` kept writing the state file to the checkpoint's directory while the board artefacts would have gone to the new one | A resume whose run directory does not match the checkpoint's is refused before the engine is opened: `ValueError` in the runner, exit 3 `resume_run_dir_mismatch` at the CLI |
| A reused engine server shifted the UUID stream between runs | `KICAD_ENGINE_REUSE=0` is set by default for a run and recorded in `state.metrics["determinism"]` |
| A DRC delta on a board nobody changed read like a result | The report names it: "drc delta on an unmodified board: N added / M resolved finding(s) with no committed copper change", with the counts in `state.metrics["drc_jitter_suspected"]` |

The last one comes from a measurement, not a guess — see §4.4.

## 4. Private V3 pilot

All boards, prompts, logs and detailed reports live in
`/Users/leo/Documents/Helix_Control_V3_pcbworld_improved`. Only aggregates appear
here.

### 4.1 Input provenance

The pilot ran against a frozen copy of the board, its project and its rules;
the source `.eprj2` was not modified. Board, project and rules hashes are pinned
in `baseline/SHA256SUMS.txt` and re-verified by the private check scripts, and the
engine build hash is recorded per run.

### 4.2 Baseline state

| Metric | Value |
|---|---|
| Unrouted edges | 149 |
| Net/pad groups | 335 |
| Tracks | 6175 |
| Vias | 209 |
| Native DRC | 8149 total (7930 relevant + 219 connectivity) |
| Cost of one native DRC | ~41 s |
| Cost of one engine load + session/resolver build | ~1 s + ~12 s |

### 4.3 Runs

| Run | Provider | Attempts | Recorded planner requests | Unrouted before → after | Stop reason |
|---|---|---|---|---|---|
| `run_pilot` | scripted, then DeepSeek resumes | 5 | 3 (categories: `bad_response` ×4) | 149 → 149 | `pairs_exhausted` |
| `run_pilot2` | DeepSeek | 3 | 1 (one usable plan: `mark_obstacles`) | 149 → 149 | `no_progress` |
| `run_pilot3` | scripted | 0 | 0 | 149 → 149 | `attempt_limit` (artifact round-trip) |
| `run_pilot4` | scripted | 0 | 0 | 149 → 149 | `attempt_limit` (artifact round-trip, post-jitter-note) |

No run improved connectivity, and no run claimed one: `accepted = 0` in every
report, and every run's `progress_after` equals its `progress_before`.

Resume was exercised against the real board, not only the fake engine. Loading
`run_pilot4`'s checkpoint and continuing in place returned exit 0, kept the
checkpoint's board as the start board, and preserved its progress. Resuming that
checkpoint against a different board file was refused with exit 3
(`provenance_mismatch`, naming the exact `board_sha256` that differed), and
resuming into a different `--run-dir` was refused with exit 3
(`resume_run_dir_mismatch`) — the second of those was found by doing it, and is in
§3.

Planner prompts recorded on disk: 5 (4 in `run_pilot`, 1 in `run_pilot2`).
Recorded token usage: 3541 prompt, 2808 completion (2751 of that reasoning),
2048 cached. Request counting was extended part-way through the pilot, so the
recorded totals are a **lower bound**; the pilot's own ceiling was 20 planning
requests and no run exceeded its configured budget.

The brief capped the pilot at 20 planning requests; the recorded runs are well
inside that, and each run stopped on its own limit or stall rather than on the
provider.

The first DeepSeek configuration lost whole replies to the reasoning budget
(`bad_response` with an empty answer at `--max-new-tokens 1600`); raising the cap
to 8000 produced the one usable plan. That is a prompt/limit tuning result, not a
model-capability claim.

### 4.4 Artifact round-trip, and a measurement that changed a claim

The best-board artifact was re-opened in fresh engines, three passes per board,
against the frozen baseline:

| Board | Pass 1 | Pass 2 | Pass 3 |
|---|---|---|---|
| Baseline (identical file, no change) | 8149 (7930 relevant) | 8149 (7930) | **8152 (7933)** |
| Saved artifact | 8152 (7933) | 8152 (7933) | 8152 (7933) |

The baseline reproduced the artifact's own number on its third pass, so the
"+3" is **engine jitter on this board, not something the artifact introduced**.
Stable-key analysis agrees: 7310 findings are stable on the baseline, 7313 on the
artifact, 7309 shared, and every finding stable on one board appeared in at least
one pass of the other. Nothing is stably added by the artifact.

Two consequences were acted on. The artifact now carries its project and rules
verbatim (verified byte-identical), so a reader gets the settings the run was
validated against; and the runner names a delta-on-unchanged-copper instead of
presenting it as a result (§3, last row). The acceptance gate stays
one-sided — it requires *zero added* relevant findings — so jitter can only make
it stricter, never looser.

The saved board is the engine's own re-serialization of the input (copper
identical; different file bytes), and two zero-attempt saves of the same input
were not byte-identical to each other either — they differ by UUID reassignment
with the same counts. An artifact hash therefore identifies a run, not a canonical
board; the sidecar hashes do identify the settings.

### 4.5 Why nothing was closed

Measured, not inferred: the V3 pairs the runner reaches first are **sub-0.25 mm
cross-layer coincident bridges**. The engine's `fix_route` places tracks to the
target position but no via, so the two sides never join; the runner classifies
these `coincident_cross_layer` → `unsupported_pair` and stops spending model
tokens on them. Adjacent causes, in order of measured impact:

1. **Verification cost.** ~41 s of native DRC per candidate evaluation, plus
   ~12 s of session/resolver build per engine, makes one attempt cost 1-4 minutes.
   A 149-pair pass is hours at this rate.
2. **Candidate reach.** The deterministic set (direct/walkaround/shove/bounded
   detour/layer) does not include obstacle-derived multi-waypoint paths, which is
   what the near-field congestion on this board appears to need.
3. **No "why blocked" signal.** The engine reports failures as geometry
   outcomes, so the agent cannot distinguish "my path is wrong" from "this pair
   is not bridgeable" without spending a full DRC.

## 5. Remaining limits (stated, not hidden)

* **No V3 connection was closed.** The pilot demonstrates the loop, its safety
  properties, its resume behaviour and its cost model on a real board. It does
  not demonstrate board-level routing progress on V3.
* **The model contributed one usable plan.** One `mark_obstacles` plan out of the
  recorded requests; the rest were either deterministic-only iterations or
  replies lost to the token cap. No claim is made about planner quality at scale.
* **DRC is not deterministic at this board size** (±0-3 relevant findings between
  passes of an identical file). Any future acceptance rule that compares counts
  must stay one-sided, and scoped/incremental DRC would remove both the cost and
  most of the jitter.
* **Only one board and one rule file** were exercised; the native gate's own
  coverage is synthetic.
* **Planner usage accounting is a lower bound.** Request counting changed during
  the pilot, so the persisted totals under-report the session.
* **No rule, via or clearance default was relaxed** to make anything pass, and
  none was changed at all in this phase.

## 6. Environment limitations (not caused by this change)

* `.venv/` carries pytest, numpy, pyyaml and gymnasium; the training stack
  (torch/vllm/ray) is absent, so unrelated upstream tests cannot run here. The
  project's `addopts` require `pytest-xdist`, which is not installed in this
  environment; the phase gate runs pytest with `-o addopts=` for that reason.
* `build_rl/` and `.venv/` are git-ignored, as is the engine patch's build copy.

## 7. Not claimed

* No version tag, release, push or deployment. Nothing is committed in this
  workspace.
* No whole-board completion, no routing-quality claim, no claim that the plan is
  electrically complete.
* No private V3 board, rule file, prompt, log or key entered this repository; only
  the aggregate numbers above.
