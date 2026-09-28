"""The scoped native DRC re-check, against a real engine.

The acceptance gate may ask the engine for its incremental pass only when that
pass returns what a whole-board DRC returns. These tests pin the four claims the
opt-in rests on:

* the relevant violation multiset (what acceptance reads) is identical, and the
  connectivity count matches, for a real copper change;
* a rollback restores the engine's DRC state, so the restored copper is judged
  against the baseline, not against the copper that was undone;
* the pass is opt-in - a default session never calls it;
* a rule file whose *contents* changed behind the same path refuses the stale
  baseline instead of diffing across rule regimes.

The full differential against the frozen V3 board (which carries real pours) is
`tools/reliability/drc_incremental_differential.py`; these are the fast cases.
"""

from __future__ import annotations

import pathlib

import pytest

from pcb_world.agent import AgentSession
from pcb_world.agent.drc_gate import (
    DrcContextError,
    is_connectivity_finding,
    take_violations,
)
from pcb_world.engine import KiCadEngine
from tests.agent import synthetic_boards as sb

START = (10.0, 15.0, 1)
TARGET = (40.0, 15.0, 1)


def _board(directory, stem: str = "incremental") -> str:
    """A pair with room to route, plus a net-2 obstacle worth clearing."""
    board = sb.write_board(
        pathlib.Path(directory) / f"{stem}.kicad_pcb",
        pads=[sb.Pad("PA1", *START[:2], 1), sb.Pad("PB1", *TARGET[:2], 1)],
        segments=[sb.Segment(25.0, 10.0, 25.0, 20.0, 2)],
    )
    sb.write_rules(pathlib.Path(board).with_suffix(".kicad_dru"), 0.2)
    return board


def _relevant_multiset(violation_set) -> dict:
    counts: dict = {}
    for violation in violation_set.violations:
        if is_connectivity_finding(violation):
            continue
        key = (
            int(violation.error_code), str(violation.error_type),
            round(float(violation.x_mm), 4), round(float(violation.y_mm), 4),
            int(violation.layer),
            tuple(sorted(str(n) for n in (violation.net_names or ()))),
            str(violation.message),
        )
        counts[key] = counts.get(key, 0) + 1
    return counts


def _connect(session, *, mode: str = "walkaround"):
    return session.connect_targets(
        START, TARGET, mode, token=session.snapshot().token,
    )


def test_scoped_and_full_passes_agree_on_identical_copper(
    engine_factory, board_dir
):
    board = _board(board_dir)
    engine = engine_factory(board)
    session = AgentSession(engine, board_path=board, incremental_drc=True)
    gate = session._gate

    baseline = gate.baseline(session.rule_context.rules_path, session.board_digest())
    checkpoint = engine.checkpoint()
    result = _connect(session)
    assert result.connected or result.accepted or result.steps
    scoped = take_violations(
        engine, session.rule_context.rules_path, incremental=True,
    )
    engine.restore(checkpoint)
    _connect(session)
    full = take_violations(engine, session.rule_context.rules_path)
    engine.restore(checkpoint)

    assert _relevant_multiset(scoped) == _relevant_multiset(full)
    assert scoped.connectivity == full.connectivity
    assert scoped.incremental is True
    assert full.incremental is False
    assert scoped.context == full.context
    # The baseline is still the same board, so the comparison the gate makes is
    # over the same three states.
    assert baseline.context == scoped.context


def test_a_rollback_restores_the_drc_state_the_scoped_pass_diffs_against(
    engine_factory, board_dir
):
    board = _board(board_dir, "incremental_rollback")
    engine = engine_factory(board)
    session = AgentSession(engine, board_path=board, incremental_drc=True)
    gate = session._gate
    rules = session.rule_context.rules_path

    baseline = gate.baseline(rules, session.board_digest())
    checkpoint = engine.checkpoint()
    _connect(session)
    engine.restore(checkpoint)

    restored = take_violations(engine, rules, incremental=True)
    assert _relevant_multiset(restored) == _relevant_multiset(baseline)
    assert restored.connectivity == baseline.connectivity


def test_the_scoped_pass_is_opt_in(engine_factory, board_dir, monkeypatch):
    """A default session never asks for the incremental pass."""
    board = _board(board_dir, "incremental_optin")
    engine = engine_factory(board)
    calls = {"incremental": 0}
    original = engine.run_drc_incremental

    def counted(*args, **kwargs):
        calls["incremental"] += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(engine, "run_drc_incremental", counted, raising=False)
    session = AgentSession(engine, board_path=board)
    try:
        result = _connect(session)
    except Exception:  # noqa: BLE001 - the engine may refuse the geometry
        result = None
    assert calls["incremental"] == 0
    assert result is not None


def test_a_changed_rule_file_refuses_the_stale_baseline(
    engine_factory, board_dir
):
    board = _board(board_dir, "incremental_rules")
    rules_path = pathlib.Path(board).with_suffix(".kicad_dru")
    engine = engine_factory(board)
    session = AgentSession(engine, board_path=board, incremental_drc=True)
    gate = session._gate
    rules = session.rule_context.rules_path

    digest = session.board_digest()
    baseline = gate.baseline(rules, digest)
    original = pathlib.Path(rules).read_bytes()
    try:
        pathlib.Path(rules).write_bytes(
            original + b'\n(rule "tight" (constraint clearance (min 1.5mm)))\n'
        )
        with pytest.raises(DrcContextError):
            gate.verify(baseline, rules, digest)
    finally:
        pathlib.Path(rules).write_bytes(original)


def test_the_scoped_pass_falls_back_when_the_engine_has_no_baseline(
    engine_factory, board_dir
):
    """A cold engine has no signature to diff against; it must run the full pass."""
    board = _board(board_dir, "incremental_cold")
    engine = engine_factory(board)
    rules = str(pathlib.Path(board).with_suffix(".kicad_dru"))
    sb.write_rules(rules, 0.2)

    scoped = take_violations(engine, rules, incremental=True)
    full = take_violations(engine, rules)
    assert scoped.total == full.total
    assert _relevant_multiset(scoped) == _relevant_multiset(full)
    assert scoped.connectivity == full.connectivity


# ---------------------------------------------------------------------------
# The shapes the multi-layer / via-escape strategies produce
#
# The first differential covered a direct connect and a shove. A via is a
# different item class with a span, and a layer transition changes which copper
# the track-vs-zone and hole checks run against, so the scoped pass has to be
# measured on those shapes before it is used for them.
# ---------------------------------------------------------------------------


def _cross_layer_board(directory, stem: str) -> str:
    """One net with a terminal on each side: any closure needs a via."""
    board = sb.write_board(
        pathlib.Path(directory) / f"{stem}.kicad_pcb",
        pads=[sb.Pad("PA1", 10.0, 10.0, 1, kind="smd_top"),
              sb.Pad("PB1", 40.0, 20.0, 1, kind="smd_bottom")],
        nets=(1,), width=50.0, height=30.0, copper_layers=4,
    )
    sb.write_rules(pathlib.Path(board).with_suffix(".kicad_dru"), 0.2)
    return board


def _scoped_vs_full_after(engine, rules: str, operate):
    """Run ``operate`` twice from one checkpoint; compare the two DRC passes."""
    checkpoint = engine.checkpoint()
    result_a = operate()
    scoped = take_violations(engine, rules, incremental=True)
    engine.restore(checkpoint)
    result_b = operate()
    full = take_violations(engine, rules)
    engine.restore(checkpoint)
    return scoped, full, result_a, result_b


def test_scoped_and_full_agree_when_the_change_is_a_via_and_a_span(
    engine_factory, board_dir
):
    board = _cross_layer_board(board_dir, "incremental_via")
    engine = engine_factory(board)
    session = AgentSession(engine, board_path=board, incremental_drc=True)
    rules = session.rule_context.rules_path
    session._gate.baseline(rules, session.board_digest())
    before = engine.get_via_count()

    scoped, full, result_a, result_b = _scoped_vs_full_after(
        engine, rules, lambda: _connect(session),
    )
    assert engine.get_via_count() == before, "the probe must leave the board clean"
    assert result_a.connected == result_b.connected
    assert _relevant_multiset(scoped) == _relevant_multiset(full)
    assert scoped.connectivity == full.connectivity
    assert scoped.incremental is True and full.incremental is False


def test_scoped_and_full_agree_when_the_change_crosses_layers(
    engine_factory, board_dir
):
    """A track on one layer, a via, and a track on another is one change set."""
    from pcb_world.core import action as core_action

    board = _cross_layer_board(board_dir, "incremental_layers")
    engine = engine_factory(board)
    session = AgentSession(engine, board_path=board, incremental_drc=True)
    rules = session.rule_context.rules_path
    session._gate.baseline(rules, session.board_digest())

    def operate():
        engine.set_routing_mode(1)
        # A via *ends* the router session on this API, so the far-layer copper is
        # a second session started at the via: track -> via -> track, one change
        # set for the DRC diff either way.
        core_action.start_route(engine, 10.0, 10.0, 1)
        placed, _detail = core_action.make_via(engine, 25.0, 10.0, 1)
        if placed:
            core_action.start_route(engine, 25.0, 10.0, 2)
            core_action.make_line(engine, 25.0, 14.0, 1)
        return placed

    checkpoint = engine.checkpoint()
    placed_a = operate()
    tracks_a, vias_a = engine.get_track_count(), engine.get_via_count()
    scoped = take_violations(engine, rules, incremental=True)
    engine.restore(checkpoint)
    placed_b = operate()
    full = take_violations(engine, rules)
    engine.restore(checkpoint)

    assert placed_a is True and placed_b is True, "the case must place a via"
    assert vias_a >= 1 and tracks_a >= 1
    assert _relevant_multiset(scoped) == _relevant_multiset(full)
    assert scoped.connectivity == full.connectivity


def test_scoped_and_full_agree_after_a_discarded_via_attempt(
    engine_factory, board_dir
):
    """A via the gate discards leaves no trace in either pass's state."""
    board = _cross_layer_board(board_dir, "incremental_via_discard")
    engine = engine_factory(board)
    session = AgentSession(engine, board_path=board, incremental_drc=True)
    rules = session.rule_context.rules_path
    baseline = session._gate.baseline(rules, session.board_digest())

    checkpoint = engine.checkpoint()
    _connect(session)
    scoped = take_violations(engine, rules, incremental=True)
    engine.restore(checkpoint)
    _connect(session)
    full = take_violations(engine, rules)
    engine.restore(checkpoint)

    assert _relevant_multiset(scoped) == _relevant_multiset(full)
    assert scoped.connectivity == full.connectivity
    assert _relevant_multiset(scoped) == _relevant_multiset(baseline)


def test_scoped_and_full_report_the_same_added_violation(
    engine_factory, board_dir
):
    """A regression must be *added* identically by both passes, not just counted."""
    from pcb_world.agent.drc_gate import diff_sets
    from pcb_world.core import action as core_action

    board = _cross_layer_board(board_dir, "incremental_regression")
    engine = engine_factory(board)
    session = AgentSession(engine, board_path=board, incremental_drc=True)
    rules = session.rule_context.rules_path
    baseline = session._gate.baseline(rules, session.board_digest())
    # Deliberately ask for a drill the board's own setup forbids: the router will
    # place it (that is what the gate is for), and both passes must report the
    # same added finding for the copper it lands in.
    engine.set_via_diameter(0.4)
    engine.set_via_drill(0.2)

    def operate():
        engine.set_routing_mode(1)
        core_action.start_route(engine, 10.0, 10.0, 1)
        placed, _detail = core_action.make_via(engine, 25.0, 10.0, 1)
        if placed:
            core_action.start_route(engine, 25.0, 10.0, 2)
            core_action.make_line(engine, 25.0, 14.0, 1)
        return placed

    checkpoint = engine.checkpoint()
    assert operate() is True, "the violating via has to be placed for the case to run"
    scoped = take_violations(engine, rules, incremental=True)
    engine.restore(checkpoint)
    assert operate() is True
    full = take_violations(engine, rules)
    engine.restore(checkpoint)

    delta_scoped = diff_sets(baseline, scoped)
    delta_full = diff_sets(baseline, full)
    assert _relevant_multiset(scoped) == _relevant_multiset(full)
    assert len(delta_scoped.added_relevant) == len(delta_full.added_relevant)
    assert delta_scoped.acceptable == delta_full.acceptable
    assert len(delta_full.added_relevant) >= 1, "the case must regress"
