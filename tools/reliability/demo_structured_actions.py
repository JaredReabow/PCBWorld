#!/usr/bin/env python3
"""Model-free demo of the structured JSON tool surface.

Writes a synthetic two-pad board, drives the whole workflow through the same
JSON envelopes a model would send (snapshot -> token -> validated action ->
transaction), and prints the transcript. No network, no model, no private board.

    .venv/bin/python tools/reliability/demo_structured_actions.py
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pcb_world.agent import AgentSession                      # noqa: E402
from pcb_world.agent.tool_api import handle_request            # noqa: E402
from tests.agent import synthetic_boards as sb                 # noqa: E402


def show(label: str, response: dict) -> None:
    print(f"\n=== {label}")
    print(json.dumps(response, indent=2, sort_keys=True))


def main() -> int:
    from pcb_world.engine import KiCadEngine

    workdir = Path(tempfile.mkdtemp(prefix="pcbworld_demo_"))
    board = sb.direct_board(workdir / "demo.kicad_pcb")
    print(f"board: {board}")

    engine = KiCadEngine(board)
    try:
        session = AgentSession(engine, board_path=board)

        first = handle_request(session, {"tool": "snapshot"})
        show("snapshot", first)

        # A malformed request is refused before anything is dispatched.
        show("refused action (missing token)", handle_request(session, {
            "tool": "act",
            "action": {"name": "start_route", "schema_version": "1.0",
                       "params": {"x_mm": 10.0, "y_mm": 10.0, "layer": 1}},
        }))

        token = first["snapshot"]["token"]
        show("start_route", handle_request(session, {
            "tool": "act", "token": token,
            "action": {"name": "start_route", "schema_version": "1.0",
                       "params": {"x_mm": 10.0, "y_mm": 10.0, "layer": 1}},
        }))

        # The token above is now stale: the session moved on.
        show("stale token refused", handle_request(session, {
            "tool": "act", "token": token,
            "action": {"name": "make_line", "schema_version": "1.0",
                       "params": {"x_mm": 40.0, "y_mm": 10.0, "mode": "walkaround"}},
        }))

        engine.cancel_route()
        fresh = handle_request(session, {"tool": "snapshot"})["snapshot"]["token"]
        result = handle_request(session, {
            "tool": "connect_targets", "token": fresh,
            "start": [10.0, 10.0, 1], "target": [40.0, 10.0, 1],
            "mode": "walkaround",
        })
        show("connect_targets", {
            "ok": result["ok"],
            "outcome": result["result"]["outcome"],
            "connected": result["result"]["connected"],
            "accepted": result["result"]["accepted"],
            "committed": result["result"]["committed"],
            "steps": [s["kind"] for s in result["result"]["steps"]],
            "drc_delta": {
                "added_relevant_count": result["result"]["evidence"]["drc_delta"]["added_relevant_count"],
            },
            "track_count": handle_request(session, {"tool": "snapshot"})["snapshot"]["track_count"],
        })
        return 0 if result["ok"] else 1
    finally:
        engine.close()


if __name__ == "__main__":
    raise SystemExit(main())
