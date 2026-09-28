# Agent reliability layer

`pcb_world/agent/` adds a validated, transactional front door to the routing
engine. The existing interfaces are untouched: the RL index space,
`PCBWorld.step`, and the LLM text projection still work exactly as before.

## What it gives you

* **Structured, validated actions.** Supported schema versions only; strict
  integer layers and net ids; non-finite or malformed coordinates, unknown
  actions/parameters/fields and wrong-phase operations are refused before C++ sees
  them.
* **Named routing modes**: `mark_obstacles` / `shove` / `walkaround`, mapped
  centrally to the legacy `m=0 / p=1 / w=2` encoding.
* **A token that binds to live state.** `session_id.revision.fingerprint` over
  the copper *and* the routing session; a foreign, older or externally invalidated
  token is refused before anything mutates.
* **Transactions that are atomic by default.** `connect_targets` runs a
  deterministic start → advance → via/finish sequence behind engine checkpoints,
  restores any failed step (shove displacement and router session included), and
  reports the *final* board.
* **A run that stops instead of quarantining on a clock accident.** Every native
  call is bounded by the run's remaining time, so the loop refuses to start a scan
  or a pair attempt it cannot also verify and stops with
  `stop_reason="time_limit_headroom"`; an attempt record keeps the failure's own
  exception and rollback detail, so an unverifiable rollback names its cause
  instead of only its category.
* **Rule context, proven and enforced at acceptance.** The applicable context is
  established automatically (fail closed), and no mutation is kept if the
  engine's own DRC reports a new relevant violation under it.
* **A JSON tool surface** (`pcb_world.agent.tool_api`) with the token required,
  plus a model-free demo transcript.

## Use

```python
from pcb_world.agent import AgentSession
from pcb_world.engine import KiCadEngine

engine = KiCadEngine("board.kicad_pcb")
session = AgentSession(engine, board_path="board.kicad_pcb")

snap = session.snapshot()                     # token + authoritative state
print(snap.outcome, snap.allowed_next_actions)

result = session.connect_targets(
    (10.0, 10.0, 1),                          # start: x_mm, y_mm, copper layer
    (40.0, 10.0, 1),                          # target
    "shove",                                  # mark_obstacles | shove | walkaround
    waypoints=[(25.0, 6.0, 1)],
    token=snap.token,                         # required; ties the call to this state
)
print(result.outcome, result.connected, result.accepted, result.committed)
print(result.evidence["drc_delta"])           # what native DRC said about the copper
```

`connect_targets` is atomic: unless the connection verifies **and** the copper
passes DRC acceptance, everything it did is rolled back. `provisional=True` is the
explicit opt-in that keeps verified waypoint progress when a later leg fails — it
never keeps copper that fails DRC acceptance.

Low-level actions keep their interface and also return a snapshot:

```python
a = session.act({"name": "start_route", "schema_version": "1.0",
                 "params": {"x_mm": 10.0, "y_mm": 10.0, "layer": 1}}, token=snap.token)
b = session.act({"name": "make_line", "schema_version": "1.0",
                 "params": {"x_mm": 40.0, "y_mm": 10.0, "mode": "walkaround"}},
                token=a.token)
```

## JSON tools

```python
from pcb_world.agent.tool_api import handle_request, tool_schemas

handle_request(session, {"tool": "snapshot"})
handle_request(session, {
    "tool": "connect_targets", "token": "<from snapshot>",
    "start": [10.0, 10.0, 1], "target": [40.0, 10.0, 1], "mode": "shove",
})
```

Every refusal is `{"ok": false, "reason": ...}`. `tool_schemas()` returns the
machine-readable description, and
`tools/reliability/demo_structured_actions.py` prints a full transcript with no
model and no network.

## Environments

`AgentSession.from_env(env, acknowledge_env_desync=True)` takes the engine out of
an existing environment. Mixing `env.step` with session calls is **not
supported**: a transaction changes the engine without touching the env's step
counter, masks, closed-net set or reward state. Drive one or the other.

## Routing agent

`pcb_world.agent.runner.RoutingRunner` drives the loop on top of the session:
deterministic candidates first, a bounded planner only for pairs the deterministic
set could not close, a heartbeat after every attempt, and a checkpoint of the best
verified board. `tools/reliability/route_agent.py` is the CLI:

```bash
python tools/reliability/route_agent.py --board b.kicad_pcb --run-dir run \
    --provider scripted --max-attempts 20            # no network, no model
python tools/reliability/route_agent.py --board b.kicad_pcb --run-dir run \
    --provider deepseek --model deepseek-flash --max-model-requests 20
python tools/reliability/route_agent.py --board b.kicad_pcb --run-dir run \
    --resume run/run_state.json                      # refuses a changed board
```

A run writes `report.json`, `run_state.json` and
`best_board.{kicad_pcb,kicad_pro,kicad_dru}` (the project and rules are copied
verbatim beside the board), plus `observations.jsonl` with `--record-prompts`. A
resume verifies the board/project/rules hashes and refuses a mismatch with exit 3.
The account of the first real pilot, including its measured cost model and its
stated limits, is in `phase2/RESULT.md`.

## Verify

```bash
bash tools/reliability/check_phase.sh            # unit + native, skip allowed
bash tools/reliability/check_phase.sh --strict   # acceptance: native must execute
```

The strict mode requires a router build, at least `EXPECTED_NATIVE_TESTS`
executed tests, zero skips, and treats load/provenance failures as errors. Both
modes write `docs/agent-work/reliability/evidence/phase_check.json`. No network
and no model calls.
