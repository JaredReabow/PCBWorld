"""Native end-to-end runner coverage over synthetic boards.

These prove the agent loop on the real router: it closes connections, uses the
candidate machinery (walkaround/shove), refuses to claim success on a blocked
board, survives a stop/resume cycle, and leaves a best-board artifact that
reopens with the same connectivity.
"""

from __future__ import annotations

import json
import os

from pcb_world.agent.runner import RoutingRunner, RunnerConfig, ScriptedPlanner
from pcb_world.agent.session import AgentSession
from tests.agent import synthetic_boards as sb
from tests.agent.conftest import BUILD_DIR


def _config(board: str, run_dir: str, **overrides) -> RunnerConfig:
    defaults = dict(
        board_path=board, run_dir=run_dir, max_attempts=8, time_limit_s=180.0,
        planner=None, stall_patience=6,
    )
    defaults.update(overrides)
    return RunnerConfig(**defaults)


def _reopen(board_path: str) -> tuple[int, int, int]:
    """Reopen a saved board and report (unrouted, tracks, vias)."""
    from pcb_world.engine import KiCadEngine

    engine = KiCadEngine(board_path)
    try:
        return engine.get_unrouted_count(), engine.get_track_count(), engine.get_via_count()
    finally:
        engine.close()


def test_native_runner_closes_a_pair_and_leaves_a_reopenable_board(
    native_engine_checked, board_dir, tmp_path
):
    board = sb.direct_board(board_dir / "runner_direct.kicad_pcb")
    before = _reopen(board)
    report = RoutingRunner(_config(board, str(tmp_path / "run"))).run()

    assert report.status == "completed"
    assert report.accepted >= 1
    assert report.progress_after["unrouted_edges"] < report.progress_before["unrouted_edges"]
    assert report.drc["added_relevant_count"] == 0
    assert report.best_board_path and os.path.isfile(report.best_board_path)
    assert report.best_board_sha256
    assert report.mode_usage

    # The artifact reopens in a fresh engine with the improved connectivity.
    unrouted, tracks, _vias = _reopen(report.best_board_path)
    assert unrouted == 0
    assert tracks > before[1]
    assert (tmp_path / "run" / "run_state.json").is_file()


def test_native_runner_uses_alternative_plans_and_improves(
    native_engine_checked, board_dir, tmp_path
):
    """A blocking wall: the runner closes it and stops at the first accepted plan.

    Candidates are applied as real atomic transactions in order, stopping at the
    first one that closes the connection and passes native acceptance, so the
    rest of the set is never evaluated and the winning plan is applied once
    rather than probed and then applied.
    """
    board = sb.obstacle_board(board_dir / "runner_obstacle.kicad_pcb")
    report = RoutingRunner(_config(board, str(tmp_path / "run"), max_attempts=4)).run()

    assert report.accepted >= 1
    assert report.progress_after["unrouted_edges"] < report.progress_before["unrouted_edges"]
    assert set(report.mode_usage) & {"walkaround", "shove"}
    state = json.loads((tmp_path / "run" / "run_state.json").read_text())
    # Nothing was probed and thrown away; every evaluated loser is a swept plan,
    # so it does not spend the pair's attempt budget.
    assert not [record for record in state["attempts"] if record["probe_only"]]
    accepted = [record for record in state["attempts"] if record["accepted"]]
    assert accepted and accepted[0]["committed"] is True
    assert accepted[0]["sweep_member"] is True
    assert not [record for record in state["attempts"]
                if record["sweep_member"] and record["accepted"]
                and record["committed"] is False]
    swept_losers = [record for record in state["attempts"]
                    if record["sweep_member"] and not record["accepted"]]
    for record in swept_losers:
        # A refused swept plan kept no copper: the board was restored.
        assert record["committed"] is False
        assert record["copper_state"] in ("restored", "unchanged", None)
    assert [record for record in state["attempts"] if record["accepted"]]
    assert state["metrics"]["iterations"] <= 4


def test_native_runner_reports_no_success_on_a_blocked_board(
    native_engine_checked, board_dir, tmp_path
):
    """A full-height locked wall: nothing may be claimed as routed."""
    board = sb.write_board(
        board_dir / "runner_blocked.kicad_pcb",
        pads=[sb.Pad("PA1", 10.0, 10.0, 1), sb.Pad("PB1", 40.0, 10.0, 1)],
        segments=[sb.Segment(25.0, 0.4, 25.0, 29.6, 2, locked=True)],
    )
    before = _reopen(board)
    report = RoutingRunner(_config(
        board, str(tmp_path / "run"), max_attempts=6, stall_patience=2,
    )).run()

    assert report.accepted == 0
    assert report.improved is False
    assert report.progress_after == report.progress_before
    assert report.stop_reason in ("no_progress", "attempt_limit", "pairs_exhausted")
    assert report.best_board_path and os.path.isfile(report.best_board_path)
    after = _reopen(report.best_board_path)
    assert after[0] == before[0]          # still the same outstanding connection
    assert after[1] == before[1]          # and the same copper (everything rolled back)


def test_native_runner_resume_matches_a_straight_run(
    native_engine_checked, board_dir, tmp_path
):
    """Stop after one attempt, resume, and land where an uninterrupted run lands."""
    resumed_board = sb.write_board(
        board_dir / "resume_a.kicad_pcb",
        pads=[sb.Pad("PA1", 10.0, 10.0, 1), sb.Pad("PB1", 40.0, 10.0, 1),
              sb.Pad("PC1", 10.0, 20.0, 2), sb.Pad("PD1", 40.0, 20.0, 2)],
        segments=[sb.Segment(25.0, 5.0, 25.0, 15.0, 3)],
        nets=(1, 2, 3),
    )
    straight_board = sb.write_board(
        board_dir / "resume_b.kicad_pcb",
        pads=[sb.Pad("PA1", 10.0, 10.0, 1), sb.Pad("PB1", 40.0, 10.0, 1),
              sb.Pad("PC1", 10.0, 20.0, 2), sb.Pad("PD1", 40.0, 20.0, 2)],
        segments=[sb.Segment(25.0, 5.0, 25.0, 15.0, 3)],
        nets=(1, 2, 3),
    )
    run_dir = tmp_path / "resumed"
    first = RoutingRunner(_config(
        resumed_board, str(run_dir), max_attempts=1,
    )).run()
    assert first.attempts == 1

    from pcb_world.agent.runner import resolve_inputs
    from pcb_world.agent.scheduler import RunState, build_provenance

    # Provenance covers the board *and* the sidecars the run resolved, so the
    # resume check is built from the same resolution the runner uses.
    inputs = resolve_inputs(resumed_board)
    state = RunState.load(
        str(run_dir / "run_state.json"),
        expected_provenance=build_provenance(
            board_path=inputs.board_path, project_path=inputs.project_path,
            rules_path=inputs.rules_path,
        ),
    )
    resumed = RoutingRunner(
        _config(resumed_board, str(run_dir), max_attempts=4), state=state,
    ).run()
    straight = RoutingRunner(_config(
        straight_board, str(tmp_path / "straight"), max_attempts=4,
    )).run()

    assert resumed.progress_after == straight.progress_after
    assert resumed.accepted == straight.accepted
    assert resumed.attempts == straight.attempts


def test_native_resume_can_still_mutate_after_the_rules_are_reproven(
    native_engine_checked, board_dir, tmp_path
):
    """A resumed run keeps working: the artifact's rules are proven by content.

    On a resume the engine opens the checkpoint's board, so the rule file it
    loads is that board's own sibling - a byte copy of the run's resolved rules,
    under a different path. Comparing the two by *path* made every resumed
    mutation fail closed with `rules_unavailable`; comparing by content keeps the
    proof and lets the run continue.
    """
    board = sb.write_board(
        board_dir / "resume_rules.kicad_pcb",
        pads=[sb.Pad("PA1", 10.0, 10.0, 1), sb.Pad("PB1", 40.0, 10.0, 1),
              sb.Pad("PC1", 10.0, 20.0, 2), sb.Pad("PD1", 40.0, 20.0, 2)],
        nets=(1, 2),
    )
    run_dir = tmp_path / "run"
    first = RoutingRunner(_config(board, str(run_dir), max_attempts=1)).run()
    assert first.accepted == 1

    from pcb_world.agent.runner import resolve_inputs
    from pcb_world.agent.scheduler import RunState, build_provenance

    inputs = resolve_inputs(board)
    state = RunState.load(
        str(run_dir / "run_state.json"),
        expected_provenance=build_provenance(
            board_path=inputs.board_path, project_path=inputs.project_path,
            rules_path=inputs.rules_path,
        ),
    )
    second = RoutingRunner(
        _config(board, str(run_dir), max_attempts=6), state=state
    ).run()

    # The resumed run really mutated: the second pair closed too. (`accepted`
    # alone is not evidence here - it counts the checkpoint's own records.)
    assert first.progress_after["unrouted_edges"] == 1
    assert second.progress_after["unrouted_edges"] == 0
    state_after = json.loads((run_dir / "run_state.json").read_text())
    assert not [r for r in state_after["attempts"] if r["reason"] == "rules_unavailable"]


def test_native_runner_alternate_layer_uses_a_via(
    native_engine_checked, board_dir, tmp_path
):
    """A cross-layer pair is closed with a via; the board's rules allow the size."""
    board = sb.write_board(
        board_dir / "runner_layer.kicad_pcb",
        pads=[sb.Pad("PA1", 10.0, 10.0, 1, kind="smd_top"),
              sb.Pad("PB1", 40.0, 20.0, 1, kind="smd_bottom")],
        min_through_hole_mm=0.2,     # the engine's configured via drill is legal here
    )
    report = RoutingRunner(_config(board, str(tmp_path / "run"))).run()

    assert report.accepted >= 1
    assert report.progress_after["unrouted_edges"] == 0
    assert report.progress_after["via_count"] == 1
    assert report.drc["added_relevant_count"] == 0
    state = json.loads((tmp_path / "run" / "run_state.json").read_text())
    assert any(record["vias_added"] == 1 for record in state["attempts"])


def test_native_runner_scripted_planner_is_not_called_on_routine_pairs(
    native_engine_checked, board_dir, tmp_path
):
    board = sb.direct_board(board_dir / "runner_scripted.kicad_pcb")
    planner = ScriptedPlanner()
    report = RoutingRunner(_config(
        board, str(tmp_path / "run"), planner=planner, max_model_requests=3,
    )).run()
    assert report.accepted >= 1
    assert planner.calls == []                 # deterministic plans were enough
    assert report.model_usage["requests"] == 0
