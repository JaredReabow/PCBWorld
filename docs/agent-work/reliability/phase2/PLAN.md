# PLAN — phase 2: resumable end-to-end routing agent (with a V3 pilot)

Builds directly on the accepted phase 1
([../PLAN.md](../PLAN.md), [../RESULT.md](../RESULT.md)). Phase 1 is **not** redone:
its accepted source is captured as a private tarball before this phase started
(`/Users/leo/Documents/Helix_Control_V3_pcbworld_improved/_baseline_phase1/`,
sha256 `28847242b44c47bb97fc47ad5296a280a7155212900d0ac8c78ae342a920b040`).

Milestone, not the end of the user's whole task: the deliverable is a *usable,
resumable* routing agent demonstrated on a real V3 pilot, with honest bottlenecks
for the next phase to attack.

## Non-goals

Whole-board completion promise; PNS-side rule enforcement; power/current design
decisions; relaxing clearances, drills or protected copper; moving components or
placements; cloud autorouters; unrelated EasyEDA actions; publishing private V3
artefacts.

## Phase order (dependency-driven)

| Phase | Deliverable | Depends on |
|---|---|---|
| P0 (done) | Private baseline capture of accepted phase-1 source; read-only V3 reference mapping | — |
| P1 | `pcb_world/agent/observations.py` — compact, planner-sized board observation (net pair, nearest obstacles, legal choices, progress/DRC delta, prior attempts) | phase 1 API |
| P2 | `pcb_world/agent/scheduler.py` — net-pair enumeration from the ratsnest, deterministic candidate generation (direct/shove/detours/layer changes), probing + ranking, attempt history, per-net budgets, stall detection, run state + provenance-verified resume | P1 |
| P3 | `pcb_world/agent/runner.py` — the resumable agent loop: deterministic-first, bounded planner assistance for blocked pairs, provider error taxonomy, heartbeat, limits, best-verified-board saving, structured report | P2 |
| P4 | `methods/llm_agent/tools/pcbworld_tools.py` + `policy/structured_agent.py` — the supported agent integration (tool schema + bounded tool-calling loop over the same JSON surface), no provider rewrites | P3 |
| P5 | `tools/reliability/route_agent.py` — documented CLI (board/project/rules, run dir, provider/model or scripted, limits, resume) and the harness wiring | P3, P4 |
| P6 | Tests: scheduler/observation units, scripted-provider runner tests (initial/resume equivalence, provider failures, bounded bad JSON, no false success), native runner tests (deterministic closure, shove/walkaround use, save + reopen equivalence, blocked board refuses) | P2-P5 |
| P7 | Private V3 pilot: baseline copy + provenance, bounded deterministic + (≤20) model-planning attempts, checkpoints, before/after connectivity and native DRC, best board, failure taxonomy | P5, P6 |
| P8 | Evidence and handover docs (`phase2/RESULT.md`, `CHECKPOINT.md`), HISTORY/CHANGELOG, bottlenecks and next-phase work | P7 |

## Contracts carried from the brief

* Planner input is **compact**: a net pair, its connected components, the nearest
  few obstacles, the legal layer/width/via choices, progress and the DRC delta —
  never a whole-board dump. Target ≤8k tokens per request, enforced by a character
  budget in the renderer.
* The model may choose endpoint groups, mode and waypoints; **only** deterministic
  code performs mechanics, and every request is validated by the phase-1 layer
  (token, schema, endpoint identity, rule context, DRC acceptance, rollback).
* Refusals are stable categories, so a planner can react without parsing prose.
* Deterministic candidates come first; the model is asked only for pairs the
  deterministic set could not close. Per-pilot budget: ≤20 planning requests.
* A run is resumable: run state records provenance (board/project/rules hashes,
  engine build hash) and attempt history; resume refuses on a provenance mismatch
  and never loses the best verified checkpoint.
* No silent weakening: no default that relaxes rules, shrinks drills or picks a
  via size on the caller's behalf.
* Private artefacts (V3 boards, prompts, logs, renderings) stay under
  `/Users/leo/Documents/Helix_Control_V3_pcbworld_improved`; only sanitized
  aggregate numbers may appear in the public tree.

## Status

P0-P8 are implemented and the phase is ready for review: account and evidence in
[RESULT.md](RESULT.md), handover state in [CHECKPOINT.md](CHECKPOINT.md). The
gate is `bash tools/reliability/check_phase.sh --strict` (exit 0, unit 225 +
native 30, no skips). The headline limit is stated in RESULT §4.5 and §5: the
private V3 pilot closed no connection, and the measured cause is written down
rather than worked around.
