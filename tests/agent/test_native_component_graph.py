"""Component-graph scheduling: membership, substitution, hints, retries.

The ratsnest is a drawing. When its endpoint lands on no copper the connection
still exists, so the scheduler now expresses the same disconnected state in the
engine's own terms - "component A is not component B" - instead of retiring the
pair because a point was empty. These tests pin each of those claims on a real
engine:

* isolated same-net zones and same-net disconnected terminals become distinct
  components, and the pair between them is offered with its component identities;
* a substitution into a component already joined to the other endpoint is
  reported as ``already_connected`` rather than offered as a route that would
  close nothing;
* a substitution is labelled a substitution, never the offered edge resolved;
* multi-layer components keep their layer, and the layer escape is offered;
* duplicate suppression is bound to the board generation, not to locality alone;
* a violation the native gate already refused a plan for aims the next plan.
"""

from __future__ import annotations

import json
import pathlib

from pcb_world.agent.observations import (
    OFFER_SUBSTITUTION,
    classify_offer,
    component_anchor_candidates,
    component_pairs,
    native_components,
    reanchor_pair,
    reanchor_pair_variants,
)
from pcb_world.agent.observations import NetPair
from pcb_world.agent.runner import RunnerConfig
from pcb_world.agent.scheduler import (
    AttemptHistory,
    AttemptRecord,
    generate_candidates,
)
from tests.agent import synthetic_boards as sb


def _pair(net: int, start, target, *, gap: float = 20.0,
          source: str = "ratsnest") -> NetPair:
    return NetPair(
        net_code=net, net_name=sb.net_name(net), start=start, target=target,
        gap_mm=gap, start_layers=(start[2],), target_layers=(target[2],),
        pad_groups=2, source=source,
    )


def _session(board):
    from pcb_world.agent.session import AgentSession
    from pcb_world.engine import KiCadEngine

    engine = KiCadEngine(board)
    session = AgentSession(engine, board_path=board)
    engine.build_connectivity()
    return engine, session


def test_same_net_disconnected_terminals_are_two_native_components(
    native_engine_checked, board_dir
):
    board = sb.write_board(
        board_dir / "graph_terminals.kicad_pcb",
        pads=[sb.Pad("PA1", 10.0, 10.0, 1), sb.Pad("PB1", 40.0, 10.0, 1)],
        nets=(1,), width=50.0, height=30.0,
    )
    engine, session = _session(board)
    try:
        components = native_components(session)
        assert len(components.get(1, [])) == 2, components
        for component in components[1]:
            assert component.net_code == 1
            assert component.terminal_count == 1
            assert component.component_id
            assert component.anchors

        pairs = component_pairs(session)
        assert pairs.by_net.get(1), "two disconnected components must offer a pair"
        offer = pairs.by_net[1][0]
        assert offer.source == "component"
        assert offer.component_start != offer.component_target
        assert offer.component_start and offer.component_target
        assert offer.substituted is False
        # Both anchors are proved by the engine, not inferred from distance.
        assert session.net_at(*offer.start) == 1
        assert session.net_at(*offer.target) == 1
        assert not (session.cluster(*offer.start) & session.cluster(*offer.target))
    finally:
        engine.close()


def test_isolated_same_net_zones_are_distinct_components(
    native_engine_checked, board_dir
):
    """Two pours of one net with no bridge between them are two components."""
    board = sb.write_board(
        board_dir / "graph_zones.kicad_pcb",
        pads=[sb.Pad("PA1", 4.0, 4.0, 1), sb.Pad("PB1", 40.0, 20.0, 1)],
        nets=(1,), width=50.0, height=30.0,
        zones=[
            sb.Zone(1, 2.0, 2.0, 12.0, 12.0, fill_points=(
                (2.0, 2.0), (12.0, 2.0), (12.0, 12.0), (2.0, 12.0))),
            sb.Zone(1, 34.0, 14.0, 46.0, 26.0, fill_points=(
                (34.0, 14.0), (46.0, 14.0), (46.0, 26.0), (34.0, 26.0))),
        ],
    )
    engine, session = _session(board)
    try:
        components = native_components(session)
        assert len(components.get(1, [])) == 2, components
        ids = {component.component_id for component in components[1]}
        assert len(ids) == 2
        pairs = component_pairs(session)
        assert pairs.by_net.get(1)
        offer = pairs.by_net[1][0]
        assert offer.component_start in ids and offer.component_target in ids
    finally:
        engine.close()


def test_a_substitution_into_an_already_joined_component_is_refused(
    native_engine_checked, board_dir
):
    """The nearest component can be the wrong one; joining it would close nothing."""
    board = sb.write_board(
        board_dir / "graph_already_joined.kicad_pcb",
        pads=[sb.Pad("PA1", 10.0, 10.0, 1), sb.Pad("PB1", 12.0, 10.0, 1),
              sb.Pad("PC1", 44.0, 10.0, 1)],
        segments=[sb.Segment(10.0, 10.0, 12.0, 10.0, 1)],
        nets=(1,), width=50.0, height=30.0,
    )
    engine, session = _session(board)
    try:
        # PA1 and PB1 are one component; PC1 is another. A bare point near the
        # joined pair, asked to substitute for a connection whose *peer* is that
        # joined pair, must be reported as already connected.
        candidates = component_anchor_candidates(
            session, (13.0, 10.0, 1), 1, peer=(12.0, 10.0, 1), limit=4,
        )
        assert candidates
        flags = {candidate.component_id: candidate.already_connected
                 for candidate in candidates}
        joined = [item for item in candidates if item.already_connected]
        assert joined, flags

        pair = _pair(1, (11.5, 13.0, 1), (12.0, 10.0, 1))
        variants = reanchor_pair_variants(
            session, pair, component_radius_mm=None, limit=3,
        )
        # Either no substitution at all (the only in-reach component is the
        # joined one) or a substitution into the *other* component.
        for variant, evidence in variants:
            components = evidence.get("components") or {}
            assert components.get("start") != components.get("target")
            assert session.net_at(*variant.start) == 1
    finally:
        engine.close()


def test_a_substitution_is_labelled_and_carries_its_component(
    native_engine_checked, board_dir
):
    board = sb.write_board(
        board_dir / "graph_substitution.kicad_pcb",
        pads=[sb.Pad("PA1", 10.0, 10.0, 1), sb.Pad("PB1", 40.0, 10.0, 1)],
        segments=[sb.Segment(24.0, 14.0, 26.0, 14.0, 2)],
        nets=(1, 2), width=50.0, height=30.0,
    )
    engine, session = _session(board)
    try:
        offered = _pair(1, (10.0, 11.0, 1), (40.0, 10.0, 1))
        variants = reanchor_pair_variants(
            session, offered, component_radius_mm=None, limit=3,
        )
        assert variants, "a proved same-net component must be offered"
        used, evidence = variants[0]
        assert evidence["offer_relation"] == OFFER_SUBSTITUTION
        assert classify_offer(offered, used) == OFFER_SUBSTITUTION
        assert evidence["moved_mm"] > 0.0
        assert evidence["components"]["start"], evidence
        replaced = evidence["endpoints"]["start"]["replaced"]
        assert replaced["provenance"] == "candidate"
        assert replaced["component_id"]
        assert used.start != offered.start

        # The wrapper keeps the single-pair contract.
        single = reanchor_pair(session, offered, component_radius_mm=None)
        assert single is not None
        assert single[0].key == variants[0][0].key
    finally:
        engine.close()


def test_multi_layer_components_keep_their_layer(native_engine_checked, board_dir):
    board = sb.write_board(
        board_dir / "graph_layers.kicad_pcb",
        pads=[sb.Pad("PA1", 10.0, 10.0, 1, kind="smd_top"),
              sb.Pad("PB1", 40.0, 10.0, 1, kind="smd_bottom")],
        nets=(1,), width=50.0, height=30.0, copper_layers=4,
    )
    engine, session = _session(board)
    try:
        components = native_components(session)
        assert len(components.get(1, [])) == 2
        layers = {item.layers[0] for item in components[1]}
        assert layers == {1, 4}, layers
        pairs = component_pairs(session)
        assert pairs.by_net.get(1)
        offer = pairs.by_net[1][0]
        assert {offer.start[2], offer.target[2]} == {1, 4}

        candidates = generate_candidates(
            offer, copper_layers=4,
            drc_hints=(("clearance", 25.0, 10.0, 1),), max_drc_candidates=1,
        )
        kinds = {candidate.kind for candidate in candidates}
        assert "drc_escape" in kinds
        escape = next(c for c in candidates if c.kind == "drc_escape")
        assert escape.waypoints[0][2] != 1
    finally:
        engine.close()


def test_drc_hints_aim_the_next_plan_at_the_refusal(native_engine_checked):
    """A refused plan's violation steers an avoidance and a layer escape."""
    pair = _pair(1, (10.0, 15.0, 1), (40.0, 15.0, 1), gap=30.0)
    hints = (("clearance", 25.0, 16.2, 1), ("hole_to_hole", 30.0, 15.0, 1))
    candidates = generate_candidates(
        pair, copper_layers=4, drc_hints=hints, max_drc_candidates=2,
    )
    avoid = [c for c in candidates if c.kind == "drc_avoid"]
    escape = [c for c in candidates if c.kind == "drc_escape"]
    # One avoidance per hint; two escapes per hint (walkaround and shove).
    assert len(avoid) == 2 and len(escape) == 4
    # Names carry the geometry (so a key can never move without the copper
    # moving); the refusal class travels in the rationale.
    assert "clearance" in avoid[0].rationale and "clearance" in escape[0].rationale
    assert avoid[0].name.startswith("drc_avoid_") and escape[0].name.startswith("drc_escape_")
    # The avoidance steps to the far side of the obstruction, not further into it.
    waypoint = avoid[0].waypoints[0]
    assert waypoint[1] < 16.2, waypoint
    # The escape leaves the layer at the violation's own position.
    assert escape[0].waypoints[0][:2] == (25.0, 16.2)
    assert escape[0].waypoints[0][2] != 1
    # Bounds are respected: no hints, no candidates of those kinds.
    assert not [c for c in generate_candidates(pair, copper_layers=4)
                if c.kind in ("drc_avoid", "drc_escape")]


def test_duplicate_suppression_is_bound_to_the_generation_and_membership():
    """A plan is only "already tried" against the same copper and components."""
    pair = _pair(1, (10.0, 15.0, 1), (40.0, 15.0, 1), source="component")
    candidate = generate_candidates(pair, copper_layers=2)[0]
    record = AttemptRecord(
        pair_key=pair.key, plan_key=candidate.key(), candidate=candidate.name,
        mode=candidate.mode, kind=candidate.kind, source="deterministic",
        outcome="routing_failed", accepted=False, connected=False,
        board_digest="generation-a", offer_source="component",
        component_start="net:1:cluster:0", component_target="net:1:cluster:1",
    )
    history = AttemptHistory([record])
    assert candidate.key() in history.tried_keys(pair, "generation-a")
    # A different board generation may route the same plan: copper is not equal.
    assert candidate.key() not in history.tried_keys(pair, "generation-b")

    # The record carries the membership it was measured against, and it survives
    # a checkpoint round trip (a resumed run keeps the binding).
    restored = AttemptHistory.from_dicts(json.loads(json.dumps(history.as_dicts())))
    kept = restored.records[0]
    assert kept.offer_source == "component"
    assert kept.component_start == "net:1:cluster:0"
    assert kept.component_target == "net:1:cluster:1"
    assert candidate.key() not in restored.tried_keys(pair, "generation-b")


def test_a_record_carries_the_refusal_class_and_position():
    """The runner's record shape keeps the evidence hints are read from."""
    record = AttemptRecord(
        pair_key=_pair(1, (10.0, 15.0, 1), (40.0, 15.0, 1)).key, plan_key=(),
        candidate="direct_walkaround", mode="walkaround", kind="direct",
        source="deterministic", outcome="routing_failed", accepted=False,
        connected=True, drc_classes=(("clearance", 2),),
        drc_hints=(("clearance", 25.0, 16.2, 1),),
    )
    payload = json.loads(json.dumps(record.to_dict()))
    assert payload["drc_classes"] == [["clearance", 2]]
    assert payload["drc_hints"] == [["clearance", 25.0, 16.2, 1]]


def test_runner_config_exposes_the_hint_budget():
    """The knob exists and defaults to a bounded, on-by-default strategy."""
    config = RunnerConfig(board_path="/tmp/b.kicad_pcb", run_dir="/tmp/r")
    assert config.drc_candidate_limit == 2
    assert config.incremental_drc is False


def test_hint_derived_plans_keep_their_keys_as_the_history_grows():
    """The same copper must yield the same keys, or the sweep never exhausts.

    Measured on the graph campaign: naming the alternatives by the position of a
    hint in the pair's growing history minted a "new" plan key every scan, so
    ``tried_keys`` could not retire it and the same offsets were re-evaluated
    until the budget ran out (1525 distinct keys over 1624 records, one name
    repeating five times). Keys are geometry now.
    """
    pair = _pair(2, (10.0, 15.0, 1), (40.0, 15.0, 1), gap=30.0)
    h1 = ("Hole clearance violation", 25.0, 16.2, 1)
    h2 = ("Clearance violation", 25.0, 16.2, 1)
    h3 = ("Hole size out of range", 30.0, 15.0, -1)

    # The same hint, a longer history in a different order, and a repeat: every
    # key already generated must still be there, unchanged.
    first = {candidate.key(): candidate for candidate in generate_candidates(
        pair, copper_layers=4, drc_hints=(h1,), max_drc_candidates=3,
    )}
    later = {candidate.key(): candidate for candidate in generate_candidates(
        pair, copper_layers=4, drc_hints=(h3, h2, h1), max_drc_candidates=3,
    )}
    repeated = {candidate.key(): candidate for candidate in generate_candidates(
        pair, copper_layers=4, drc_hints=(h1,), max_drc_candidates=3,
    )}
    assert set(first) == set(repeated)
    assert set(first) <= set(later), set(first) - set(later)
    for key, candidate in first.items():
        assert later[key].waypoints == candidate.waypoints
        assert later[key].name == candidate.name
    # The same *geometry* never carries two names, whichever class named it.
    same_spot = [candidate for candidate in generate_candidates(
        pair, copper_layers=4, drc_hints=(h1, h2), max_drc_candidates=2,
    ) if candidate.kind in ("drc_avoid", "drc_escape")]
    names = {candidate.waypoints: candidate.name for candidate in same_spot}
    assert len(names) == len({name for name in names.values()})


def test_a_repeated_hint_never_produces_two_candidates_for_one_waypoint():
    pair = _pair(2, (10.0, 15.0, 1), (40.0, 15.0, 1), gap=30.0)
    hint = ("Clearance violation", 25.0, 16.2, 1)
    candidates = generate_candidates(
        pair, copper_layers=4, drc_hints=(hint, hint, hint),
        max_drc_candidates=3,
    )
    plans = [(candidate.mode, candidate.waypoints) for candidate in candidates
             if candidate.kind in ("drc_avoid", "drc_escape")]
    assert len(plans) == len(set(plans))
    # The same waypoint may appear in two modes - that is two plans - but never
    # twice in the same mode.
    modes = [mode for mode, _ in plans]
    assert len(modes) == len(set(modes)) or len(plans) > 1


def test_component_identity_is_membership_not_row_order(
    native_engine_checked, board_dir
):
    """The same terminals are the same component, whatever row they arrive in."""
    board = sb.write_board(
        board_dir / "graph_identity.kicad_pcb",
        pads=[sb.Pad("PA1", 10.0, 10.0, 1), sb.Pad("PB1", 12.0, 10.0, 1),
              sb.Pad("PC1", 44.0, 10.0, 1)],
        segments=[sb.Segment(10.0, 10.0, 12.0, 10.0, 1)],
        nets=(1,), width=50.0, height=30.0,
    )
    engine, session = _session(board)
    try:
        first = native_components(session)
        ids = sorted(item.component_id for item in first[1])
        assert all(item.startswith("net:1:mem:") for item in ids), ids
        # Re-reading the same board yields the same identities: they are a
        # function of the membership, not of the order the rows came back in.
        again = native_components(session)
        assert sorted(item.component_id for item in again[1]) == ids
    finally:
        engine.close()


def test_component_windows_rotate_and_report_what_they_left_out(
    native_engine_checked, board_dir
):
    """A bounded window must move, and must say what it did not examine."""
    pads = [sb.Pad(f"PA{index}", 8.0 + index * 5.0, 8.0, 1) for index in range(8)]
    board = sb.write_board(
        board_dir / "graph_windows.kicad_pcb", pads=pads,
        nets=(1,), width=60.0, height=30.0,
    )
    engine, session = _session(board)
    try:
        windows = []
        for window in range(4):
            scan = component_pairs(
                session, max_components_per_net=3,
                max_pairs_per_net=100, window=window,
            )
            coverage = scan.coverage
            assert coverage["components_total"] == 8, coverage
            assert coverage["components_in_window"] == 3, coverage
            assert coverage["components_omitted_by_window"] == 5, coverage
            windows.append(frozenset(
                coverage["per_net"]["1"]["component_ids_in_window"]
            ))
        # Successive windows are not the same three components, and the rotation
        # reaches components the first window never saw.
        assert len(set(windows)) > 1, windows
        assert windows[0] != windows[1]
        assert len(set().union(*windows)) > 3
    finally:
        engine.close()


def test_pairs_omitted_by_the_cap_are_counted(native_engine_checked, board_dir):
    pads = [sb.Pad(f"PA{index}", 8.0 + index * 5.0, 8.0, 1) for index in range(6)]
    board = sb.write_board(
        board_dir / "graph_caps.kicad_pcb", pads=pads,
        nets=(1,), width=60.0, height=30.0,
    )
    engine, session = _session(board)
    try:
        scan = component_pairs(
            session, max_components_per_net=6, max_pairs_per_net=1, window=0,
        )
        coverage = scan.coverage
        assert coverage["pairs_offered"] == 1
        assert coverage["pairs_omitted_by_cap"] > 0, coverage
        assert coverage["pairs_considered"] > coverage["pairs_offered"]
    finally:
        engine.close()


def test_the_nearest_neighbour_is_a_neighbour_on_the_whole_net(
    native_engine_checked, board_dir
):
    """A window holding only distant components must not invent a near one.

    The window selects which components are *examined*; it must not change what
    "this component's nearest neighbour" means. Measured on the frozen board, a
    window-relative search proposed two components 85 mm apart as a pair, which
    routed 101 mm of copper before the acceptance DRC failed.
    """
    pads = [sb.Pad(f"PA{index}", 8.0 + index * 3.0, 8.0, 1) for index in range(6)]
    board = sb.write_board(
        board_dir / "graph_neighbours.kicad_pcb", pads=pads,
        nets=(1,), width=60.0, height=30.0,
    )
    engine, session = _session(board)
    try:
        # Pads sit every 3 mm and each component offers its three nearest
        # neighbours, so no legitimate pair can exceed 9 mm. A window-relative
        # search could have offered a pair up to 2x further apart than that
        # (its "neighbour" would be whichever component the window happened to
        # contain), which is the defect this pins.
        for window in range(4):
            scan = component_pairs(
                session, max_components_per_net=2, max_pairs_per_net=10,
                window=window,
            )
            for pair in scan.by_net.get(1, []):
                assert pair.gap_mm <= 9.05, (window, pair.gap_mm,
                                             pair.component_start,
                                             pair.component_target)
    finally:
        engine.close()


def test_an_optional_gap_ceiling_is_counted_when_a_caller_asks_for_one(
    native_engine_checked, board_dir
):
    pads = [sb.Pad(f"PA{index}", 8.0 + index * 12.0, 8.0, 1) for index in range(4)]
    board = sb.write_board(
        board_dir / "graph_gap.kicad_pcb", pads=pads,
        nets=(1,), width=60.0, height=30.0,
    )
    engine, session = _session(board)
    try:
        unbound = component_pairs(session, max_components_per_net=4,
                                  max_pairs_per_net=10)
        assert unbound.by_net.get(1), "by default nothing is dropped for distance"
        bound = component_pairs(session, max_components_per_net=4,
                                max_pairs_per_net=10, max_gap_mm=5.0)
        assert bound.coverage["pairs_omitted_by_gap"] > 0, bound.coverage
        for pair in bound.by_net.get(1, []):
            assert pair.gap_mm <= 5.0
    finally:
        engine.close()


def test_the_scan_reports_component_coverage(native_engine_checked, board_dir):
    """The scan's evidence carries what the windows examined, not just offers."""
    from pcb_world.agent.observations import scan_net_pairs

    pads = [sb.Pad(f"PA{index}", 8.0 + index * 5.0, 8.0, 1) for index in range(6)]
    board = sb.write_board(
        board_dir / "graph_scan_coverage.kicad_pcb", pads=pads,
        nets=(1,), width=60.0, height=30.0,
    )
    engine, session = _session(board)
    try:
        first = scan_net_pairs(session, max_per_net=1, component_window=0)
        evidence = first.to_evidence()
        assert evidence["component_coverage"], evidence
        assert evidence["component_coverage"]["components_total"] == 6
        second = scan_net_pairs(session, max_per_net=1, component_window=2)
        assert second.to_evidence()["component_coverage"]["window"] == 2
    finally:
        engine.close()
