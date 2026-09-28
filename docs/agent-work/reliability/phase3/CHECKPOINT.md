# CHECKPOINT — phase 3

Updated: 2026-09-26, ready for review. Phase 1 stays accepted; phase 2's
documents stand except where phase 3 corrects a claim (see RESULT §5).

Superseded for recovery acceptance by [phase4-recovery/CHECKPOINT.md](../phase4-recovery/CHECKPOINT.md).
The pilot counts below remain historical; the old mutable artifact workflow did
not establish immutable promotion or detailed reopened DRC identity.

Status: **READY FOR REVIEW** (not accepted). Contract: [PLAN.md](PLAN.md).
Account and evidence: [RESULT.md](RESULT.md).

| Item | State |
|---|---|
| Workspace | `/Users/leo/Documents/PCBWorld-reliability`, branch `feat/agent-reliability-actions`, base `b3d62f5` |
| Gate | `bash tools/reliability/check_phase.sh --strict` → exit 0 (unit 237, native 37, no skips) |
| Agent suite | `pytest tests/agent -q -o addopts=` → 274 passed |
| Private pilot | `/Users/leo/Documents/Helix_Control_V3_pcbworld_improved` (`report_phase3.md`, `provenance_phase3.json`, `run_p3_*`) |
| Git state | nothing committed, pushed or staged; `git diff --check` clean |
| Milestone | Historical report records 6 in-memory accepted closures (149 → 143 unrouted, 335 → 329 pad groups); hardened saved-artifact acceptance pending |

## Reproduce

```bash
cd /Users/leo/Documents/PCBWorld-reliability
bash tools/reliability/check_phase.sh --strict
PYTHONPATH=$PWD .venv/bin/python -m pytest tests/agent -q -o addopts=
```

The V3 pass, its exact commands and its cost model are in the private
`report_phase3.md` §5; nothing private is copied here.

## What is proven, and by what

* **Layer identity** — the engine's own map is the authority; verified on 2- and 4-layer synthetic boards, with overlapping XY across unrelated nets, through-hole pads and an inner-layer anchor (`tests/agent/test_native_layers.py`).
* **Fair queue and honest completion** — round-robin across nets, reasons recorded for everything not offered, completion decided by the engine's unrouted count (`test_phase3_integrity.py`, `test_integration_gaps.py`).
* **Budgets** — totals survive a resume across planner instances; the request ceiling is enforced inside the retry loop; only `content` is an answer; a truncation escalates once (`test_phase3_integrity.py`, `test_planner_client.py`).
* **Artifact integrity** — the former flow staged sidecars and later reopened fixed-name output, but could overwrite the active checkpoint before final DRC and compared aggregate counts only. These checks were not sufficient for promotion; phase 4 supplies the replacement gate.
* **Historical V3 progress** — six in-memory transaction closures and aggregate DRC counts were reported. Detailed saved-artifact revalidation remains pending.

## Open items handed to the reviewer

1. **Zone refill is the next structural blocker.** The frozen rules require more copper-to-zone clearance than the board's fills
provide (`[rule values redacted]`), so the baseline already carries 7488 `source-fill-clearance` violations and any route inside a fill adds more. The honest fix is an engine-side zone refill under the current rules (a standard, non-loosening editor operation), which the pinned engine does not expose. No rule was relaxed and the class was not ignored.
2. **Owned native deadlines** are implemented in phase 4; native strict verification remains pending.
3. **Richer deterministic candidates and a better model plan.** The remaining pairs are refused by the engine's own probes (`connection_not_verified`) or by `endpoint_unknown` (an anchor whose cluster has no pad or via). Obstacle-derived multi-waypoint paths and a legal via-bridge primitive are the next candidate work; the model's executed plans closed nothing, so its prompt/observations need work too.
4. **Scoped/incremental DRC** — `KiCadEngine.run_drc_incremental` exists but its scoped pass skips pad/graphic/zone sub-tests, so it was deliberately not used as an acceptance gate. A differential full-vs-scoped study is the prerequisite before any use.

## Next phase (resume here)

In measured-impact order: engine zone refill under the current rules; multi-waypoint
candidate generation; process-isolated native calls with deadlines; then a
differential full-vs-scoped DRC study.
