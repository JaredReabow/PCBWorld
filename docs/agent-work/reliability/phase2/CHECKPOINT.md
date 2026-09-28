# CHECKPOINT — phase 2: resumable routing agent

Updated: 2026-09-26, ready for review. Phase 1 remains accepted and untouched
([../CHECKPOINT.md](../CHECKPOINT.md)); its accepted source is captured before
this phase as a private tarball
(`/Users/leo/Documents/Helix_Control_V3_pcbworld_improved/_baseline_phase1/`,
sha256 `28847242b44c47bb97fc47ad5296a280a7155212900d0ac8c78ae342a920b040`).

**Superseded for the routing work:** phase 3
([../phase3/CHECKPOINT.md](../phase3/CHECKPOINT.md)) answered this phase's review
and corrected two claims made here (the "engine cannot bridge these" conclusion was
an agent-side threshold, and the +3 artifact findings were engine jitter). This
document is kept as the phase-2 record.

Status: **READY FOR REVIEW** (not accepted). Contract: [PLAN.md](PLAN.md).
Account and evidence: [RESULT.md](RESULT.md).

| Item | State |
|---|---|
| Workspace | `/Users/leo/Documents/PCBWorld-reliability` |
| Branch | `feat/agent-reliability-actions`, base `b3d62f5` |
| Gate | `bash tools/reliability/check_phase.sh --strict` → exit 0 (unit 225, native 30, no skips) |
| New public modules | `pcb_world/agent/{observations,scheduler,runner}.py` |
| Integration | `methods/llm_agent/tools/pcbworld_tools.py`, `methods/llm_agent/policy/structured_agent.py` |
| CLI | `tools/reliability/route_agent.py` |
| Tests | `tests/agent/{test_scheduler_unit,test_runner_scripted,test_planner_client,test_native_runner,test_integration_gaps}.py` |
| Harness | `tools/reliability/check_phase.py` (`EXPECTED_NATIVE_TESTS = 30`) |
| Private pilot | `/Users/leo/Documents/Helix_Control_V3_pcbworld_improved` (`report.md`, `provenance.json`, `run_pilot*`, `baseline/`) |
| Git state | nothing committed, pushed or staged; `git diff --check` clean |

## Reproduce

```bash
cd /Users/leo/Documents/PCBWorld-reliability
bash tools/reliability/check_phase.sh --strict            # acceptance evidence
PYTHONPATH=$PWD .venv/bin/python -m pytest tests/agent -q -o addopts=   # 255 passed

# model-free smoke of the agent loop over a synthetic board
PYTHONPATH=$PWD .venv/bin/python -m pytest tests/agent/test_native_runner.py -q

# CLI shape (network-free):
PYTHONPATH=$PWD .venv/bin/python tools/reliability/route_agent.py \
  --board <b.kicad_pcb> --run-dir /tmp/run --provider scripted --max-attempts 2
```

The private pilot is reproducible with the private scripts in that directory
(`verify_artifact.py`, `probe_drc_repeat.py`) using the same environment variables
as the gate; the model calls need the task-local key file and are not re-run here.

## Open items handed to the reviewer

1. **The V3 pilot closed no connection** (149 → 149 in every run). Cause measured,
   not guessed: the near-field pairs are sub-0.25 mm cross-layer coincident
   bridges the engine cannot bridge, and the deterministic candidates do not yet
   include obstacle-derived multi-waypoint paths. See RESULT §4.5.
2. **Native DRC is not deterministic at this board size** (±0-3 relevant findings
   on an identical file). The gate is one-sided (`added == 0`), the report now
   names a delta on unmodified copper, and the counts are kept in run state.
3. **Planner usage is a lower bound** — request counting was extended during the
   pilot, so persisted totals under-report the session.

## Next phase (resume here)

In measured-impact order:

1. **Scoped/incremental DRC** — a native check limited to the nets a candidate
   touched. This attacks both the ~41 s verification cost and most of the jitter.
2. **Richer deterministic candidates** — obstacle-derived multi-waypoint paths
   (and, where the engine allows it, a cross-layer bridge step), so pairs the
   planner is currently asked about are closed in code.
3. **Richer candidates for congestion** — per-net ordering by congestion rather
   than ratsnest length, so the agent stops reaching the same unreachable pair
   first on every resume.
4. **Planner prompt/limit tuning** — the 8000-token cap produced the only usable
   plan; the prompt and the budget should be tuned against a small fixed set
   before any larger run.
