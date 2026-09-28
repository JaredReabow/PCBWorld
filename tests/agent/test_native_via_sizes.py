"""The router's via size is resolved per net, from that net's own class.

The router's size cache is process state that is not populated from the project,
so a size has to be *handed* to it. Two things must be true of that handing:

* it uses the class of the net actually being routed - not a hard-coded net, not
  one class applied to every net on a board that has several;
* it sets diameter and drill as a pair, and refuses to continue when it cannot
  (a half-applied pair is router state nothing here can read back).

The declared and adopted numbers are different numbers when board floors bind, so
both travel in the evidence. The adoption is a starting point only: the
transaction's native DRC acceptance and the saved artifact's fresh whole-board
gate are what prove the copper is lawful.
"""

from __future__ import annotations

import json
import math
import pathlib

import pytest

from pcb_world.agent.observations import NetPair
from pcb_world.agent.rules import resolve_via_size
from pcb_world.agent.scheduler import generate_candidates
from pcb_world.agent.session import AgentSession
from pcb_world.engine.wire import DesignRules, NetClassInfo
from tests.agent import synthetic_boards as sb


def _board(directory, stem: str, *, min_through_hole_mm: float = 0.3) -> str:
    board = sb.write_board(
        pathlib.Path(directory) / f"{stem}.kicad_pcb",
        pads=[sb.Pad("PA1", 10.0, 10.0, 1, kind="smd_top"),
              sb.Pad("PB1", 40.0, 10.0, 1, kind="smd_top"),
              sb.Pad("PA2", 10.0, 12.0, 1, kind="smd_top"),
              sb.Pad("PB2", 40.0, 12.0, 1, kind="smd_top"),
              sb.Pad("PC1", 10.0, 20.0, 2, kind="smd_top"),
              sb.Pad("PD1", 40.0, 20.0, 2, kind="smd_top")],
        nets=(1, 2), width=50.0, height=30.0, copper_layers=4,
        min_through_hole_mm=min_through_hole_mm,
    )
    return board


def _two_classes(engine, *, net_a: int, net_b: int, wide=(0.9, 0.4)):
    """Stand in for the engine's class resolution: one class per net.

    The session's contract is "ask the engine for *this* net's class", so the
    classes are supplied at the boundary the session actually uses. How a project
    file maps a net to a class is the engine's business (and its own tests cover
    the file path).
    """
    def resolve(net_code: int) -> NetClassInfo:
        if int(net_code) == int(net_b):
            return NetClassInfo(name="Wide", via_diameter_mm=wide[0],
                                via_drill_mm=wide[1], clearance_mm=0.2,
                                track_width_mm=0.25)
        if int(net_code) == int(net_a):
            return NetClassInfo(name="Default", via_diameter_mm=0.6,
                                via_drill_mm=0.3, clearance_mm=0.2,
                                track_width_mm=0.25)
        return NetClassInfo(name="Default", via_diameter_mm=0.6,
                            via_drill_mm=0.3, clearance_mm=0.2,
                            track_width_mm=0.25)

    engine.get_netclass_for_net = resolve  # type: ignore[method-assign]


def _route(session, start, target, mode: str = "walkaround"):
    return session.connect_targets(
        start, target, mode, token=session.snapshot().token,
    )


def test_each_net_gets_the_size_from_its_own_class(engine_factory, board_dir):
    board = _board(board_dir, "vias_two_classes")
    engine = engine_factory(board)
    _two_classes(engine, net_a=1, net_b=2)
    session = AgentSession(engine, board_path=board)

    first = session._via_size_for_net(1)
    second = session._via_size_for_net(2)
    assert first["netclass"] == "Default", first
    assert first["adopted_via_diameter_mm"] == 0.6
    assert first["adopted_via_drill_mm"] == 0.3
    assert second["netclass"] == "Wide", second
    assert second["adopted_via_diameter_mm"] == 0.9
    assert second["adopted_via_drill_mm"] == 0.4
    # The declared numbers are what the project says; the adopted ones are what
    # was handed to the router. They must not be conflated in the evidence.
    assert first["declared_via_diameter_mm"] == 0.6
    assert first["declared_via_drill_mm"] == 0.3
    assert session.routing_sizes["by_net"][2]["applied"] is True


def test_floors_are_recorded_next_to_the_declared_values(
    engine_factory, board_dir
):
    board = _board(board_dir, "vias_floor", min_through_hole_mm=0.45)
    engine = engine_factory(board)
    session = AgentSession(engine, board_path=board)
    entry = session._via_size_for_net(1)

    assert entry["usable"] is True, entry
    assert entry["floors"]["min_through_hole_mm"] == pytest.approx(0.45)
    # The floor binds the drill; the declared drill is still reported separately.
    assert entry["declared_via_drill_mm"] == 0.3
    assert entry["adopted_via_drill_mm"] >= 0.45
    annular = entry["floors"]["min_via_annular_width_mm"]
    assert entry["adopted_via_diameter_mm"] >= entry["adopted_via_drill_mm"] + 2 * annular
    # Nothing here claims the board minima prove every applicable rule.
    assert entry["source"] == "netclass"


def test_the_code_asked_for_is_the_code_used(engine_factory, board_dir):
    """No probe list: the net being resolved is the net that is asked about."""
    board = _board(board_dir, "vias_codes")
    engine = engine_factory(board)
    asked: list[int] = []
    original = engine.get_netclass_for_net

    def record(net_code: int):
        asked.append(int(net_code))
        return original(int(net_code))

    engine.get_netclass_for_net = record  # type: ignore[method-assign]
    resolve_via_size(engine, 317)
    resolve_via_size(engine, 42)
    assert asked == [317, 42], asked


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -1.0, 0.0, "0.3", None])
def test_a_malformed_declared_value_is_unusable_and_unchanged(
    engine_factory, board_dir, bad
):
    board = _board(board_dir, "vias_malformed")
    engine = engine_factory(board)
    engine.get_netclass_for_net = lambda net_code: NetClassInfo(  # type: ignore[method-assign]
        name="Default", via_diameter_mm=bad, via_drill_mm=0.3,
        clearance_mm=0.2, track_width_mm=0.25,
    )
    resolution = resolve_via_size(engine, 1)
    assert resolution.usable is False
    assert "finite" in resolution.reason
    assert resolution.adopted_diameter_mm is None
    assert resolution.declared_diameter_mm is None


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -1.0, -0.001, "0.3", None])
def test_an_invalid_floor_makes_the_size_unusable(
    engine_factory, board_dir, bad
):
    """An unreadable constraint is not "no constraint"."""
    board = _board(board_dir, "vias_bad_floor")
    engine = engine_factory(board)
    engine.get_design_rules = lambda: DesignRules(  # type: ignore[method-assign]
        min_through_hole_mm=bad, min_via_diameter_mm=0.5,
        min_via_annular_width_mm=0.1, min_hole_to_hole_mm=0.2,
    )
    resolution = resolve_via_size(engine, 1)
    assert resolution.usable is False, resolution.to_evidence()
    assert resolution.invalid_constraints, resolution.to_evidence()
    assert "min_through_hole_mm" in resolution.invalid_constraints[0]
    assert resolution.adopted_drill_mm is None


def test_a_zero_floor_is_allowed_and_not_treated_as_invalid(
    engine_factory, board_dir
):
    """KiCad writes 0.0 for "no constraint" - that is an answer, not a fault."""
    board = _board(board_dir, "vias_zero_floor")
    engine = engine_factory(board)
    engine.get_design_rules = lambda: DesignRules(  # type: ignore[method-assign]
        min_through_hole_mm=0.0, min_via_diameter_mm=0.0,
        min_via_annular_width_mm=0.0, min_hole_to_hole_mm=0.0,
    )
    resolution = resolve_via_size(engine, 1)
    assert resolution.usable is True, resolution.to_evidence()
    assert resolution.invalid_constraints == ()
    # With no floors at all the declared values are adopted unchanged.
    assert resolution.adopted_drill_mm == resolution.declared_drill_mm
    assert resolution.adopted_diameter_mm == resolution.declared_diameter_mm


@pytest.mark.parametrize("which", ["diameter", "drill"])
def test_any_setter_failure_quarantines_because_the_outcome_is_unknown(
    engine_factory, board_dir, which
):
    """No readback exists, so a failed setter cannot be called a no-op.

    The engine exposes no getter for the sizes it is holding. A raise from either
    setter therefore leaves a state this session cannot prove - whether or not the
    call "should" have been a no-op - and the fail-closed answer is to quarantine
    rather than route on it.
    """
    board = _board(board_dir, f"vias_{which}_fails")
    engine = engine_factory(board)

    def explode(value_mm: float) -> None:
        raise RuntimeError(f"{which} setter refused")

    monkeypatch_target = "set_via_diameter" if which == "diameter" else "set_via_drill"
    setattr(engine, monkeypatch_target, explode)
    session = AgentSession(engine, board_path=board)
    assert session.dirty is True
    assert "cannot be proven" in session.routing_sizes["failure"]
    result = _route(session, (10.0, 10.0, 1), (40.0, 10.0, 1))
    assert result.accepted is False
    assert result.evidence.get("reason") in (
        "session_quarantined", "unverified_state",
    ), result.evidence


def test_a_setter_that_mutates_then_raises_still_quarantines(
    engine_factory, board_dir
):
    """The dangerous case: copper settings did change, then the call raised."""
    board = _board(board_dir, "vias_mutate_then_raise")
    engine = engine_factory(board)
    applied: list[float] = []
    original = engine.set_via_diameter

    def mutate_then_raise(diameter_mm: float) -> None:
        original(diameter_mm)          # the setting really does change
        applied.append(float(diameter_mm))
        raise RuntimeError("reported failure after mutating")

    engine.set_via_diameter = mutate_then_raise  # type: ignore[method-assign]
    session = AgentSession(engine, board_path=board)
    assert applied, "the setter really mutated before raising"
    assert session.dirty is True
    failure = session.routing_sizes["failure"]
    assert "cannot be proven" in failure
    assert str(applied[-1]) in failure, failure


def test_alternating_nets_apply_each_nets_size(engine_factory, board_dir):
    board = _board(board_dir, "vias_alternate")
    engine = engine_factory(board)
    _two_classes(engine, net_a=1, net_b=2)
    applied: list[str] = []
    original = engine.set_via_diameter

    def record(diameter_mm: float) -> None:
        applied.append(float(diameter_mm))
        return original(diameter_mm)

    # Patch before the session exists so the startup adoption is recorded too.
    engine.set_via_diameter = record  # type: ignore[method-assign]
    session = AgentSession(engine, board_path=board)
    first = _route(session, (10.0, 10.0, 1), (40.0, 10.0, 1))
    second = _route(session, (10.0, 20.0, 1), (40.0, 20.0, 1))
    assert first.evidence["via_size"]["netclass"] == "Default"
    assert second.evidence["via_size"]["netclass"] == "Wide"
    # The alternation really reached the engine: 0.6 then 0.9.
    assert applied == [0.6, 0.9], applied


def test_a_b_a_route_ends_with_a_in_force(engine_factory, board_dir):
    """Returning to the first net must restore *its* size, not keep B's.

    This is the defect a per-net cache that answers from memory cannot see: the
    entry for A still says "applied", while the router is holding B's size. The
    engine's active sizes are read back through the setter calls it receives, so
    the assertion is about the engine's state, not about the session's bookkeeping.
    """
    board = _board(board_dir, "vias_aba")
    engine = engine_factory(board)
    _two_classes(engine, net_a=1, net_b=2)
    calls: list[tuple[str, float]] = []
    original_diameter = engine.set_via_diameter
    original_drill = engine.set_via_drill

    def record_diameter(value: float) -> None:
        calls.append(("diameter", float(value)))
        return original_diameter(value)

    def record_drill(value: float) -> None:
        calls.append(("drill", float(value)))
        return original_drill(value)

    engine.set_via_diameter = record_diameter  # type: ignore[method-assign]
    engine.set_via_drill = record_drill  # type: ignore[method-assign]
    session = AgentSession(engine, board_path=board)
    first = _route(session, (10.0, 10.0, 1), (40.0, 10.0, 1))     # A
    second = _route(session, (10.0, 20.0, 1), (40.0, 20.0, 1))    # B
    third = _route(session, (10.0, 12.0, 1), (40.0, 12.0, 1))     # A again, new pair
    assert first.evidence["via_size"]["netclass"] == "Default"
    assert second.evidence["via_size"]["netclass"] == "Wide"
    assert third.evidence["via_size"]["netclass"] == "Default"
    # The engine was told A's size *again* after B's - the exact final state is
    # A's pair, in that order.
    diameters = [value for name, value in calls if name == "diameter"]
    drills = [value for name, value in calls if name == "drill"]
    assert diameters[-1] == 0.6, calls
    assert drills[-1] == 0.3, calls
    assert diameters == [0.6, 0.9, 0.6], calls
    assert session.routing_sizes["applied"]["netclass"] == "Default"


def test_a_class_change_on_the_same_net_is_reapplied(engine_factory, board_dir):
    """The cached answer must not outlive the class it came from."""
    board = _board(board_dir, "vias_class_change")
    engine = engine_factory(board)
    current = {"diameter": 0.6, "drill": 0.3, "name": "Default"}

    def resolve(net_code: int) -> NetClassInfo:
        return NetClassInfo(name=current["name"], via_diameter_mm=current["diameter"],
                            via_drill_mm=current["drill"], clearance_mm=0.2,
                            track_width_mm=0.25)

    engine.get_netclass_for_net = resolve  # type: ignore[method-assign]
    applied: list[float] = []
    original = engine.set_via_diameter

    def record(value: float) -> None:
        applied.append(float(value))
        return original(value)

    engine.set_via_diameter = record  # type: ignore[method-assign]
    session = AgentSession(engine, board_path=board)
    _route(session, (10.0, 10.0, 1), (40.0, 10.0, 1))
    assert applied == [0.6]
    # The same net now resolves to a different class: it must be re-applied.
    current.update(diameter=0.8, drill=0.4, name="Wide")
    result = _route(session, (10.0, 12.0, 1), (40.0, 12.0, 1))
    assert applied == [0.6, 0.8], applied
    assert result.evidence["via_size"]["netclass"] == "Wide"


def test_adopted_sizes_are_not_rounded_below_a_declared_minimum(
    engine_factory, board_dir
):
    """Boundary proof: the value handed to the engine is the exact one computed."""
    board = _board(board_dir, "vias_precision")
    engine = engine_factory(board)
    floor = 0.30000000004
    engine.get_design_rules = lambda: DesignRules(  # type: ignore[method-assign]
        min_through_hole_mm=floor, min_via_diameter_mm=0.5,
        min_via_annular_width_mm=0.1, min_hole_to_hole_mm=0.2,
    )
    resolution = resolve_via_size(engine, 1)
    assert resolution.usable is True, resolution.to_evidence()
    # Not rounded to 0.3: that would be below the floor the board declares.
    assert resolution.adopted_drill_mm == floor
    assert resolution.adopted_drill_mm >= floor
    assert resolution.adopted_diameter_mm >= (
        resolution.adopted_drill_mm + 2.0 * 0.1
    )


def test_an_unusable_size_refuses_a_plan_that_would_place_a_via(
    engine_factory, board_dir
):
    """No lawful size means no via, not a via at whatever was left in force."""
    # A pair with a terminal on each side: both endpoints resolve, so the plan
    # really does contain a via step and the refusal is about the via size.
    board = sb.write_board(
        pathlib.Path(board_dir) / "vias_unusable_refusal.kicad_pcb",
        pads=[sb.Pad("PA1", 10.0, 10.0, 1, kind="smd_top"),
              sb.Pad("PB1", 40.0, 20.0, 1, kind="smd_bottom")],
        nets=(1,), width=50.0, height=30.0, copper_layers=4,
    )
    engine = engine_factory(board)
    engine.get_design_rules = lambda: DesignRules(  # type: ignore[method-assign]
        min_through_hole_mm=float("nan"), min_via_diameter_mm=0.5,
        min_via_annular_width_mm=0.1, min_hole_to_hole_mm=0.2,
    )
    session = AgentSession(engine, board_path=board)
    before = engine.get_track_count()
    result = _route(session, (10.0, 10.0, 1), (40.0, 20.0, 4))
    assert result.accepted is False
    assert result.evidence.get("reason") == "via_size_unusable", result.evidence
    assert result.evidence["via_size"]["invalid_constraints"]
    assert engine.get_track_count() == before


def test_adoption_does_not_touch_the_design_rules(engine_factory, board_dir):
    board = _board(board_dir, "vias_rules_untouched")
    engine = engine_factory(board)
    before = engine.get_design_rules()
    netclass_before = engine.get_netclass_for_net(1)
    AgentSession(engine, board_path=board)
    after = engine.get_design_rules()
    netclass_after = engine.get_netclass_for_net(1)
    for field in ("min_clearance_mm", "min_track_width_mm", "min_via_diameter_mm",
                  "min_through_hole_mm", "min_via_annular_width_mm",
                  "min_hole_to_hole_mm", "copper_edge_clearance_mm"):
        assert getattr(before, field) == getattr(after, field), field
    assert netclass_before.clearance_mm == netclass_after.clearance_mm
    assert netclass_before.track_width_mm == netclass_after.track_width_mm
    assert netclass_before.via_diameter_mm == netclass_after.via_diameter_mm
    assert netclass_before.via_drill_mm == netclass_after.via_drill_mm


def test_via_jogs_are_offered_for_a_cross_layer_bridge(native_engine_checked):
    pair = NetPair(
        net_code=2, net_name="SCL1", start=(147.169, 112.603, 3),
        target=(147.169, 112.603, 4), gap_mm=0.0,
        pair_kind="coincident_cross_layer",
    )
    candidates = generate_candidates(pair, copper_layers=4, max_via_jogs=4)
    jogs = [candidate for candidate in candidates if candidate.kind == "via_jog"]
    assert len(jogs) == 4
    for candidate in jogs:
        waypoint = candidate.waypoints[0]
        assert waypoint[2] == 4, "the jog must place the via on the target side"
        offset = ((waypoint[0] - 147.169) ** 2
                  + (waypoint[1] - 112.603) ** 2) ** 0.5
        assert 0.0 < offset <= 1.0


def test_a_hole_class_refusal_adds_alternatives_and_no_rule_change(
    native_engine_checked,
):
    pair = NetPair(
        net_code=2, net_name="SCL1", start=(10.0, 15.0, 1),
        target=(40.0, 15.0, 1), gap_mm=30.0,
    )
    hints = (("Hole size out of range", 25.0, 15.0, -1),)
    candidates = generate_candidates(
        pair, copper_layers=4, drc_hints=hints, max_via_jogs=2,
    )
    kinds = {candidate.kind for candidate in candidates}
    assert {"drc_escape", "via_jog"} <= kinds
    # A layer escape is offered in both modes: walkaround and shove.
    modes = {candidate.mode for candidate in candidates
             if candidate.kind == "drc_escape"}
    assert {"walkaround", "shove"} <= modes, modes
    for candidate in candidates:
        assert not hasattr(candidate, "clearance_mm")
