"""Native coverage: the reliability contract against the real router.

Skipped (loudly, and reported as skipped by the phase harness) when no
``kicad_rl_router`` build is present. Every board is synthetic and written by
``synthetic_boards.py``; no private artefact is used.
"""

from __future__ import annotations

import pytest

from pcb_world.agent import AgentSession, Outcome, StructuredAction
from pcb_world.agent.state import canonical_rows
from tests.agent import synthetic_boards as sb


def _session(engine, board: str) -> AgentSession:
    """A token-requiring session (the default contract)."""
    return AgentSession(engine, board_path=board)


def _rows(engine) -> tuple:
    """Canonical nanometre rows: coordinates, layer, net, width, via span."""
    return canonical_rows(engine.get_tracks(), engine.get_vias())


def _connect(session, start, target, mode="walkaround", **kwargs):
    """Run a transaction with the token from a fresh snapshot (the default contract)."""
    return session.connect_targets(start, target, mode, token=session.snapshot().token, **kwargs)


def test_direct_connection_commits_and_verifies_connectivity(engine_factory, board_dir):
    board = sb.direct_board(board_dir / "direct.kicad_pcb")
    engine = engine_factory(board)
    session = _session(engine, board)
    snap = session.snapshot()
    assert snap.outcome is Outcome.OK
    assert snap.track_count == 0

    result = _connect(session, (10.0, 10.0, 1), (40.0, 10.0, 1), "walkaround")
    assert result.outcome is Outcome.OK
    assert result.connected is True and result.accepted is True
    assert result.committed is True
    assert engine.get_track_count() > 0
    assert engine.get_unrouted_count() == 0
    assert [10.0, 10.0, 1] in result.evidence["shared_anchors"]
    assert [40.0, 10.0, 1] in result.evidence["shared_anchors"]
    assert result.evidence["drc_delta"]["added_relevant_count"] == 0
    # The route closes, so the engine is idle again and the snapshot says so.
    assert result.snapshot.route_active is False
    assert result.snapshot.head is None


def test_token_is_required_on_the_native_path(engine_factory, board_dir):
    board = sb.direct_board(board_dir / "token.kicad_pcb")
    engine = engine_factory(board)
    session = _session(engine, board)
    # Deliberately token-less: this test is about the refusal.
    result = session.connect_targets((10.0, 10.0, 1), (40.0, 10.0, 1), "walkaround")
    assert result.outcome is Outcome.INVALID_ACTION
    assert result.evidence["reason"] == "token_required"
    assert engine.get_track_count() == 0


def test_second_attempt_is_already_connected_and_mutates_nothing(engine_factory, board_dir):
    board = sb.direct_board(board_dir / "again.kicad_pcb")
    engine = engine_factory(board)
    session = _session(engine, board)
    first = _connect(session, (10.0, 10.0, 1), (40.0, 10.0, 1), "walkaround")
    assert first.accepted

    before = _rows(engine)
    second = _connect(session, (10.0, 10.0, 1), (40.0, 10.0, 1), "walkaround")
    assert second.outcome is Outcome.ALREADY_CONNECTED
    assert second.committed is False and second.steps == ()
    assert _rows(engine) == before


def test_walkaround_detours_around_an_obstacle_and_connects(engine_factory, board_dir):
    board = sb.obstacle_board(board_dir / "obstacle.kicad_pcb")
    engine = engine_factory(board)
    session = _session(engine, board)
    result = _connect(session, (10.0, 10.0, 1), (40.0, 10.0, 1), "walkaround")
    assert result.outcome is Outcome.OK
    assert result.connected is True and result.accepted is True
    # A detour needs more than the single straight segment of a clear corridor.
    assert engine.get_track_count() >= 2
    # The wall is still where it was: walkaround does not move foreign copper.
    assert 2 not in result.evidence["changed_nets"]


def test_alternate_layer_route_with_a_legal_via_is_accepted(engine_factory, board_dir):
    board = sb.write_board(
        board_dir / "layers.kicad_pcb",
        pads=[sb.Pad("PA1", 10.0, 10.0, 1, kind="smd_top"),
              sb.Pad("PB1", 40.0, 20.0, 1, kind="smd_bottom")],
    )
    engine = engine_factory(board)
    # The router places whatever via size it is configured with; this board's
    # project sets a 0.3 mm minimum hole, so a legal via must be asked for.
    engine.set_via_diameter(0.6)
    engine.set_via_drill(0.3)
    session = _session(engine, board)
    result = _connect(session, (10.0, 10.0, 1), (40.0, 20.0, 2), "walkaround")
    assert result.outcome is Outcome.OK
    assert result.connected is True and result.accepted is True
    assert engine.get_via_count() == 1
    kinds = [s.kind for s in result.steps]
    assert kinds[0] == "start" and "via" in kinds
    assert all(s.success for s in result.steps)
    via = engine.get_vias()[0]
    assert round(float(via.top_layer), 4) != round(float(via.bottom_layer), 4)


def test_a_via_that_violates_min_hole_is_rejected_and_rolled_back(
    engine_factory, board_dir
):
    """Native DRC acceptance has teeth: a real rule violation stops the commit.

    The session adopts the project's own netclass via size before routing (see
    ``AgentSession._sync_routing_sizes``), so a violation has to be *asked for*
    here - the engine is told to use a 0.2 mm drill on a board whose minimum is
    0.3 mm. That is the property this test exists for, and it is why the session
    exists: the router will happily place illegal copper, and only the gate
    stands between that and a committed board.
    """
    board = sb.write_board(
        board_dir / "small_via.kicad_pcb",
        pads=[sb.Pad("PA1", 10.0, 10.0, 1, kind="smd_top"),
              sb.Pad("PB1", 40.0, 20.0, 1, kind="smd_bottom")],
    )
    engine = engine_factory(board)
    session = _session(engine, board)
    # After the session's adoption, deliberately go below the board minimum.
    engine.set_via_diameter(0.4)
    engine.set_via_drill(0.2)
    before = _rows(engine)

    result = _connect(session, (10.0, 10.0, 1), (40.0, 20.0, 2), "walkaround")

    assert result.outcome is Outcome.ROUTING_FAILED
    assert result.accepted is False and result.committed is False
    assert result.evidence["reason"] == "drc_regression"
    added = result.evidence["drc_delta"]["added_relevant"]
    assert any("Hole size" in str(v["message"]) for v in added)
    assert result.evidence["rollback"]["copper_digest_matches"] is True
    assert _rows(engine) == before
    assert engine.get_via_count() == 0
    assert engine.get_unrouted_count() == 1


def test_the_session_prevents_the_default_illegal_via(engine_factory, board_dir):
    """The same connection is legal once the router uses the project's via size.

    Measured on the frozen V3 board, the router's size cache was not populated
    from the project: it placed 0.25 mm drills where the board setup requires at
    least 0.3 mm, so *every* via it added was refused. The session adopts the
    project's netclass value, floored by the board's minimum, and the connection
    the illegal via was blocking now commits.
    """
    board = sb.write_board(
        board_dir / "adopted_via.kicad_pcb",
        pads=[sb.Pad("PA1", 10.0, 10.0, 1, kind="smd_top"),
              sb.Pad("PB1", 40.0, 20.0, 1, kind="smd_bottom")],
    )
    engine = engine_factory(board)
    session = _session(engine, board)
    entry = session.routing_sizes["by_net"][0]
    assert entry["applied"] is True, entry
    assert entry["adopted_via_drill_mm"] >= float(
        engine.get_design_rules().min_through_hole_mm
    )

    result = _connect(session, (10.0, 10.0, 1), (40.0, 20.0, 2), "walkaround")
    assert result.outcome is Outcome.OK, result.evidence
    assert result.accepted is True and result.committed is True
    via = engine.get_vias()[0]
    assert float(via.drill_mm) >= float(
        engine.get_design_rules().min_through_hole_mm
    )
    assert engine.get_unrouted_count() == 0


def test_locked_obstacle_is_walked_around_never_shoved(engine_factory, board_dir):
    board = sb.obstacle_board(board_dir / "locked.kicad_pcb", locked=True)
    engine = engine_factory(board)
    before = _rows(engine)
    session = _session(engine, board)
    result = _connect(session, (10.0, 10.0, 1), (40.0, 10.0, 1), "shove")
    assert result.outcome is Outcome.OK
    assert result.connected is True
    locked_rows = [row for row in _rows(engine) if row[-1] == 2]
    original = [row for row in before if row[-1] == 2]
    assert locked_rows == original
    assert 2 not in result.evidence["changed_nets"]


def test_blocked_target_rolls_back_exactly(engine_factory, board_dir):
    """A full-height locked wall makes the target unreachable."""
    board = sb.write_board(
        board_dir / "wall.kicad_pcb",
        pads=[sb.Pad("PA1", 10.0, 10.0, 1), sb.Pad("PB1", 40.0, 10.0, 1)],
        segments=[sb.Segment(25.0, 0.4, 25.0, 29.6, 2, locked=True)],
    )
    engine = engine_factory(board)
    session = _session(engine, board)
    before = _rows(engine)

    result = _connect(session, (10.0, 10.0, 1), (40.0, 10.0, 1), "walkaround")

    assert result.outcome is Outcome.ROUTING_FAILED
    assert result.connected is False and result.committed is False
    assert result.evidence["reason"] == "connection_not_verified"
    assert result.evidence["rollback"]["copper_digest_matches"] is True
    assert _rows(engine) == before
    assert engine.get_unrouted_count() == 1


def test_shove_displaces_foreign_copper_and_probing_restores_it(engine_factory, board_dir):
    """The displaced-net claim is evidence, not an assumption.

    The shove candidate's own evidence records that NET2 (the obstacle bar) changed,
    and the board afterwards is byte-identical to before, so the probe both
    evaluated and undid a real displacement.
    """
    board = sb.shove_board(board_dir / "shove.kicad_pcb")
    engine = engine_factory(board)
    session = _session(engine, board)
    before = _rows(engine)
    snap = session.snapshot()

    results, probe_snap = session.probe_candidates(
        (10.0, 10.0, 1), (40.0, 10.0, 1),
        [
            {"name": "shove", "mode": "shove"},
            {"name": "walkaround", "mode": "walkaround"},
        ],
        token=snap.token,
    )

    assert len(results) == 2
    by_name = {r.name: r.result for r in results}
    assert all(r.result.identical_start is True for r in results)
    shove = by_name["shove"]
    assert shove.connected is True and shove.accepted is True
    assert 2 in shove.evidence["changed_nets"], (
        "the shove candidate did not displace the NET2 bar; the fixture no longer "
        "proves shove behaviour"
    )
    assert 2 not in by_name["walkaround"].evidence["changed_nets"]

    assert _rows(engine) == before
    assert probe_snap.outcome is Outcome.OK
    assert probe_snap.evidence["restored_ok"] is True
    assert session.dirty is False


def test_illegal_via_on_a_thru_pad_is_refused_without_mutation(engine_factory, board_dir):
    """make_via onto a pad that already bridges the layers is a no-op refusal."""
    board = sb.write_board(
        board_dir / "thru.kicad_pcb",
        pads=[sb.Pad("PA1", 10.0, 10.0, 1), sb.Pad("PB1", 25.0, 10.0, 1, kind="thru")],
    )
    engine = engine_factory(board)
    session = _session(engine, board)
    before = _rows(engine)

    snap = session.snapshot()
    started = session.act(StructuredAction.start_route(10.0, 10.0, 1), token=snap.token)
    assert started.outcome is Outcome.OK
    refused = session.act(
        StructuredAction.make_via(25.0, 10.0, "walkaround"), token=started.token
    )

    assert refused.outcome is Outcome.ROUTING_FAILED
    assert refused.evidence["dispatch_info"]["rejected"] == "via_on_thru_pad"
    assert refused.evidence["dispatch_info"]["via_placed"] is False
    assert _rows(engine) == before


def test_cancelled_route_recovers(engine_factory, board_dir):
    board = sb.direct_board(board_dir / "cancel.kicad_pcb")
    engine = engine_factory(board)
    session = _session(engine, board)
    snap = session.snapshot()
    started = session.act(StructuredAction.start_route(10.0, 10.0, 1), token=snap.token)
    assert started.route_active is True

    engine.cancel_route()
    after = session.snapshot()
    assert after.route_active is False
    assert after.allowed_next_actions == ("net_select", "start_route", "connect_targets")

    # A cancelled route leaves a stale token behind on purpose (the session state
    # moved); the fresh snapshot's token works.
    result = _connect(session, (10.0, 10.0, 1), (40.0, 10.0, 1), "walkaround")
    assert result.outcome is Outcome.OK
    assert result.connected is True


@pytest.mark.parametrize("mode", ["walkaround", "shove", "mark_obstacles"])
def test_named_modes_are_dispatched_on_the_native_path(engine_factory, board_dir, mode):
    """Mode-dispatch coverage only: acceptance is asserted elsewhere.

    ``mark_obstacles`` is an obstacle-marking strategy, so this test checks that
    the named mode reaches the engine and produces a *reported* outcome, without
    claiming that every mode closes the connection.
    """
    board = sb.direct_board(board_dir / f"mode_{mode}.kicad_pcb")
    engine = engine_factory(board)
    session = _session(engine, board)
    result = _connect(session, (10.0, 10.0, 1), (40.0, 10.0, 1), mode)
    assert result.mode == mode
    assert result.outcome in (Outcome.OK, Outcome.ROUTING_FAILED, Outcome.UNSUPPORTED)
    assert result.evidence.get("plan")            # the plan reached execution
