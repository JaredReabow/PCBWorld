"""The two phase-9 strategies: violation-centred clearance search, and coverage.

Both are *generic* changes - neither names a net, a coordinate or a board - so
they are pinned here on synthetic geometry with a fake engine:

* the clearance search must never lose the single fixed step it replaced, must
  stay bounded, and must name every probe by its own geometry so a re-scan cannot
  mint a new plan key for the same copper;
* coverage must be exactly a widening: with no per-net cap hit, or with a fresh
  pair already offered, it changes nothing; it never drops a pair and never lets
  one net contribute more than one extra pair.
"""

from __future__ import annotations

import math

from pcb_world.agent.observations import (
    LayerResolver,
    NetPair,
    scan_net_pairs,
)
from pcb_world.agent.runner import RunnerConfig, RoutingRunner
from pcb_world.agent.scheduler import (
    AttemptHistory,
    AttemptRecord,
    clearance_search_probes,
    generate_candidates,
)
from pcb_world.agent.session import AgentSession
from tests.agent.conftest import fake_artifact_verifier
from tests.agent.fake_engine import FakeEngine
from pcb_world.engine.wire import RatsnestEdge


def _pair(net: int = 1, start=(10.0, 15.0, 1), target=(40.0, 15.0, 1)) -> NetPair:
    return NetPair(
        net_code=net, net_name=f"NET{net}", start=start, target=target,
        gap_mm=30.0, start_layers=(start[2],), target_layers=(target[2],),
        pad_groups=2, source="ratsnest",
    )


# ---------------------------------------------------------------------------
# Strategy A - violation-centred clearance search
# ---------------------------------------------------------------------------


def test_clearance_search_probes_are_nearest_first_and_bounded():
    probes = clearance_search_probes(
        base_x=25.0, base_y=15.0, perpendicular=(0.0, 1.0), direction=(1.0, 0.0),
        side_sign=+1.0, base_offset=1.2, clearance_mm=0.4, layer=1,
        steps=(1.0, 2.0, 4.0),
    )
    assert len(probes) == 4                     # three outward + one along-line
    outward = [probe for probe in probes[:3]]
    distances = [round(probe[1] - 15.0, 4) for probe in outward]
    assert distances == [1.6, 2.0, 2.8]         # 1.2 + 0.4 * (1, 2, 4)
    assert probes[-1][2] == 1
    assert all(probe[2] == 1 for probe in probes)


def test_clearance_search_mirrors_the_side_the_plan_was_refused_on():
    plus = clearance_search_probes(
        base_x=0.0, base_y=0.0, perpendicular=(0.0, 1.0), direction=(1.0, 0.0),
        side_sign=+1.0, base_offset=1.0, clearance_mm=0.5, layer=1,
        steps=(2.0,),
    )
    minus = clearance_search_probes(
        base_x=0.0, base_y=0.0, perpendicular=(0.0, 1.0), direction=(1.0, 0.0),
        side_sign=-1.0, base_offset=1.0, clearance_mm=0.5, layer=1,
        steps=(2.0,),
    )
    assert plus[0][1] > 0.0 > minus[0][1]
    assert plus[0][0] == minus[0][0] == 0.0


def test_clearance_search_respects_the_board_bounds_and_deduplicates():
    probes = clearance_search_probes(
        base_x=99.0, base_y=99.0, perpendicular=(1.0, 0.0), direction=(1.0, 0.0),
        side_sign=+1.0, base_offset=1.0, clearance_mm=0.5, layer=1,
        steps=(1.0, 1.0), bounds=(0.0, 0.0, 100.0, 100.0), margin_mm=0.5,
    )
    assert len(probes) == len(set(probes))
    assert all(probe[0] <= 99.5 and probe[1] <= 99.5 for probe in probes)


def test_generate_candidates_keeps_the_fixed_step_and_adds_the_search():
    pair = _pair()
    hints = (("Clearance violation", 25.0, 16.2, 1),)
    base = generate_candidates(
        pair, copper_layers=4, drc_hints=hints, max_drc_candidates=1,
        max_clearance_probes=0,
    )
    searched = generate_candidates(
        pair, copper_layers=4, drc_hints=hints, max_drc_candidates=1,
        max_clearance_probes=3,
    )
    assert [c.kind for c in base if c.kind == "drc_avoid"] == ["drc_avoid"]
    assert not [c for c in base if c.kind == "drc_clear"]
    avoid = [c for c in searched if c.kind == "drc_avoid"]
    assert len(avoid) == 1 and avoid[0] == [c for c in base if c.kind == "drc_avoid"][0]
    clear = [c for c in searched if c.kind == "drc_clear"]
    assert len(clear) == 3
    assert all(c.name.startswith("drc_clear_") for c in clear)
    assert all(len(c.waypoints) == 1 for c in clear)
    # Names are canonical geometry, so the same copper cannot mint a second key.
    assert clear[0].name == f"drc_clear_{clear[0].waypoints[0][0]:.2f}_" \
                            f"{clear[0].waypoints[0][1]:.2f}"


def test_clearance_search_budget_is_shared_across_hints():
    pair = _pair()
    hints = (("Clearance violation", 25.0, 16.2, 1),
             ("Hole clearance violation", 30.0, 14.2, 1))
    candidates = generate_candidates(
        pair, copper_layers=4, drc_hints=hints, max_drc_candidates=2,
        max_clearance_probes=2,
    )
    assert len([c for c in candidates if c.kind == "drc_clear"]) == 2
    assert len([c for c in candidates if c.kind == "drc_avoid"]) == 2


def test_clearance_search_does_not_change_the_plan_keys_of_other_families():
    pair = _pair()
    hints = (("Clearance violation", 25.0, 16.2, 1),)
    without = generate_candidates(
        pair, copper_layers=4, drc_hints=hints, max_drc_candidates=1,
        max_clearance_probes=0, max_via_jogs=2,
    )
    with_search = generate_candidates(
        pair, copper_layers=4, drc_hints=hints, max_drc_candidates=1,
        max_clearance_probes=4, max_via_jogs=2,
    )
    before = [c.key() for c in without if c.kind != "drc_clear"]
    after = [c.key() for c in with_search if c.kind != "drc_clear"]
    assert before == after


def test_refusal_derived_candidates_survive_the_candidate_limit():
    """A recorded refusal outranks a proximity heuristic.

    The runner truncates to its candidate limit. With the contextual families
    first, five or six pour/obstacle plans stood in front of every plan derived
    from a measured violation - and on the V3 campaign's first 1 318 s segment
    that meant 36 pairs with recorded violations produced zero refusal-derived
    evaluations. The evidence families have to come first.
    """
    pair = _pair()
    hints = (("Clearance violation", 25.0, 16.2, 1),)
    pour = ({"x_mm": 20.0, "y_mm": 15.0, "layer": 1, "source": "component"},)
    obstacles = ({"kind": "track", "x1_mm": 22.0, "y1_mm": 16.0,
                  "x2_mm": 28.0, "y2_mm": 16.0, "distance_mm": 1.0,
                  "layer": 1},)
    candidates = generate_candidates(
        pair, copper_layers=4, drc_hints=hints, max_drc_candidates=1,
        max_clearance_probes=3, pour_waypoints=pour, obstacles=obstacles,
        max_obstacle_detours=3,
    )
    kinds = [candidate.kind for candidate in candidates]
    evidence = {"drc_avoid", "drc_clear", "drc_escape"}
    first_evidence = min(kinds.index(kind) for kind in evidence if kind in kinds)
    contextual = [index for index, kind in enumerate(kinds)
                  if kind in {"pour", "obstacle"}]
    assert contextual, kinds
    assert first_evidence < min(contextual), kinds
    # The families themselves are all still present.
    assert evidence <= set(kinds)


def test_extent_family_clears_the_observed_obstacle_by_its_own_extent():
    """The offset is measured from where the copper actually is.

    ``drc_clear`` steps by multiples of clearance from the refusal; this family
    projects the *observed* obstacle onto the connection's perpendicular axis at
    the refusal and places the waypoint past its far edge. A 0.5 mm track whose
    far edge sits 1.25 mm off the line therefore gets a waypoint at 1.25 + the
    applicable clearance, not at a guessed multiple of it.
    """
    pair = _pair()
    hints = (("Clearance violation", 25.0, 16.2, 1),)
    # A track on the pair's own layer, 1.0 mm off the line and 0.5 mm wide.
    obstacle = {"kind": "track", "layer": 1, "distance_mm": 1.0,
                "x1_mm": 22.0, "y1_mm": 16.0, "x2_mm": 28.0, "y2_mm": 16.0,
                "width_mm": 0.5}
    candidates = generate_candidates(
        pair, copper_layers=4, drc_hints=hints, max_drc_candidates=1,
        max_extent_probes=2, rule_clearance_mm=0.2, obstacles=(obstacle,),
    )
    extent = [c for c in candidates if c.kind == "drc_extent"]
    assert len(extent) == 1
    assert extent[0].name == f"drc_extent_{extent[0].waypoints[0][0]:.2f}_" \
                             f"{extent[0].waypoints[0][1]:.2f}"
    # The waypoint is past the track's far edge (16.25) plus the 0.2 mm clearance
    # and the family's own 0.2 mm margin.
    assert abs(extent[0].waypoints[0][1] - 16.65) < 1e-6
    assert extent[0].waypoints[0][2] == 1
    # A hole-class refusal is the via family's business, not a wider step's.
    hole_only = generate_candidates(
        pair, copper_layers=4, drc_hints=(("Hole clearance violation", 25.0, 16.2, 1),),
        max_drc_candidates=1, max_extent_probes=2, rule_clearance_mm=0.2,
        obstacles=(obstacle,),
    )
    assert not [c for c in hole_only if c.kind == "drc_extent"]


def test_extent_family_is_bounded_and_ignores_other_layers():
    pair = _pair()
    hints = (("Clearance violation", 25.0, 16.2, 1),)
    obstacles = (
        {"kind": "track", "layer": 2, "x1_mm": 22.0, "y1_mm": 16.0,
         "x2_mm": 28.0, "y2_mm": 16.0, "width_mm": 0.5},
        {"kind": "track", "layer": 1, "x1_mm": 22.0, "y1_mm": 16.0,
         "x2_mm": 28.0, "y2_mm": 16.0, "width_mm": 0.5},
        {"kind": "track", "layer": 1, "x1_mm": 22.0, "y1_mm": 17.0,
         "x2_mm": 28.0, "y2_mm": 17.0, "width_mm": 0.5},
    )
    candidates = generate_candidates(
        pair, copper_layers=4, drc_hints=hints, max_drc_candidates=1,
        max_extent_probes=1, rule_clearance_mm=0.2, obstacles=obstacles,
    )
    extent = [c for c in candidates if c.kind == "drc_extent"]
    assert len(extent) == 1                      # one shared budget, respected
    # The nearest far edge wins; the layer-2 track is not a same-layer wall.
    assert abs(extent[0].waypoints[0][1] - 16.65) < 1e-6
    # Disabling the family restores the previous candidate set exactly.
    without = generate_candidates(
        pair, copper_layers=4, drc_hints=hints, max_drc_candidates=1,
        max_extent_probes=0, rule_clearance_mm=0.2, obstacles=obstacles,
    )
    assert [c.key() for c in without] == [
        c.key() for c in candidates if c.kind != "drc_extent"
    ]


# ---------------------------------------------------------------------------
# Offered-edge identity
# ---------------------------------------------------------------------------


def test_a_substitution_offer_carries_the_offered_edge_key(monkeypatch):
    """A substituted offer names the edge the ratsnest actually drew.

    ``offer.key`` is the substituted geometry; without ``offered_key`` an attempt
    on it cannot be attributed to any offered connection and stays out of the
    per-edge record. The re-anchor search is stubbed here - it is covered by the
    native component-graph tests - so this pins the *scan's* wiring.
    """
    from dataclasses import replace as _replace
    import pcb_world.agent.observations as obs

    engine = _two_pair_engine()
    session = AgentSession(engine, board_path="/tmp/fake.kicad_pcb")
    engine.build_connectivity()
    base = scan_net_pairs(
        session, resolver=LayerResolver.build(session), max_per_net=1,
        include_components=False, include_substitutions=False,
    )
    offered = base.pairs[0]
    moved = _replace(offered, start=(offered.start[0] + 3.0, offered.start[1] + 3.0,
                                     offered.start[2]))

    def fake_variants(session_, pair, **kwargs):
        return [(moved, {"offer_relation": "substitution",
                         "components": {"start": "net:1:mem:aaaa",
                                        "target": "net:1:mem:bbbb"}})]

    monkeypatch.setattr(obs, "reanchor_pair_variants", fake_variants)
    scan = scan_net_pairs(
        session, resolver=LayerResolver.build(session), max_per_net=1,
        include_components=False, include_substitutions=True,
        component_window=1,
    )
    substitutions = [pair for pair in scan.pairs if pair.substituted]
    assert substitutions, [pair.to_dict() for pair in scan.pairs]
    offer = substitutions[0]
    assert offer.source == "substitution"
    assert tuple(offer.offered_key) == tuple(offered.key)
    assert offer.key != tuple(offer.offered_key)


def test_a_record_keeps_the_attempted_geometry_and_the_offered_edge():
    """``pair_key`` is what was attempted; ``offered_pair_key`` is what was offered.

    A substitution replaces the anchors, so the two are different statements and
    both are kept. The taxonomy attributes the record by the offered key, which
    is the exact link back to the connection the scan drew.
    """
    from pcb_world.agent.scheduler import offered_edge_key

    offered = _pair(net=1, start=(10.0, 15.0, 1), target=(40.0, 15.0, 1))
    substituted = _pair(net=1, start=(13.0, 18.0, 1), target=(40.0, 15.0, 1))
    substituted = type(offered)(
        net_code=1, net_name="NET1", start=substituted.start,
        target=substituted.target, gap_mm=substituted.gap_mm,
        start_layers=(1,), target_layers=(1,), pad_groups=2,
        source="substitution", substituted=True,
        component_start="net:1:mem:aaaa", component_target="net:1:mem:bbbb",
        offered_key=tuple(offered.key),
    )
    assert tuple(offered_edge_key(substituted)) == tuple(offered.key)
    assert tuple(offered_edge_key(offered)) == tuple(offered.key)

    history = AttemptHistory()
    history.note(AttemptRecord(
        pair_key=substituted.key, plan_key=(), candidate="c", mode="walkaround",
        kind="direct", source="deterministic", outcome="routing_failed",
        accepted=False, connected=False, offered_pair_key=offered.key,
    ))
    record = history.records[0]
    assert record.pair_key == substituted.key
    assert record.offered_pair_key == offered.key
    # Round-trips through the checkpoint format, and a legacy record without the
    # field still loads (empty key = not recorded).
    restored = AttemptHistory.from_dicts([record.to_dict()])
    assert restored.records[0].offered_pair_key == offered.key
    legacy = {key: value for key, value in record.to_dict().items()
              if key != "offered_pair_key"}
    assert AttemptHistory.from_dicts([legacy]).records[0].offered_pair_key == ()


# ---------------------------------------------------------------------------
# Hole-derived via seeds
# ---------------------------------------------------------------------------


def test_hole_seeds_are_placed_from_the_actual_drilled_geometry():
    """The seed distance is measured from the hole, not guessed at.

    A hole-clearance refusal names a neighbourhood, and the drilled items in it
    can be read. The seed sits at hole radius + via radius + the board's own
    hole-to-hole rule + one probe pitch from the hole centre, along the direction
    that keeps the route moving toward the far terminal. The engine's prefilter
    and the native DRC remain the authority on whether the spot is lawful.
    """
    from tests.agent.fake_engine import FakeEngine, ViaInfo

    via = ViaInfo(x_mm=25.0, y_mm=10.0, diameter_mm=0.6, drill_mm=0.3)
    engine = FakeEngine(clusters={"A": {(10.0, 15.0, 1)}})
    engine.state.vias.append(via)
    session = AgentSession(engine, board_path="/tmp/fake.kicad_pcb")
    engine.build_connectivity()
    pair = _pair(net=1, start=(10.0, 15.0, 1), target=(40.0, 15.0, 1))

    runner = RoutingRunner.__new__(RoutingRunner)
    runner.config = RunnerConfig(
        board_path="/tmp/fake.kicad_pcb", run_dir="/tmp/run",
        via_hole_seeds=2,
    )
    runner._stop_requested = ""
    hints = (("Hole clearance violation", 25.0, 10.0, 1),)
    seeds = runner._hole_escape_seeds(session, pair, runner.config, hints)
    assert len(seeds) == 2
    # 0.15 hole radius + 0.3 via radius + 0.2 board rule + 0.15 pitch.
    for seed in seeds:
        assert abs(math.hypot(seed[0] - 25.0, seed[1] - 10.0) - 0.8) < 1e-3
    # First seed heads for the target, second for the start: same distance out
    # from the hole, opposite continuations.
    assert seeds[0][0] > 25.0 and seeds[1][0] < 25.0

    # An explicit clearance overrides the board rule; 0 seeds disables the family.
    runner.config = RunnerConfig(
        board_path="/tmp/fake.kicad_pcb", run_dir="/tmp/run",
        via_hole_seeds=1, via_hole_clearance_mm=1.0,
    )
    one = runner._hole_escape_seeds(session, pair, runner.config, hints)
    assert len(one) == 1
    assert abs(math.hypot(one[0][0] - 25.0, one[0][1] - 10.0) - 1.6) < 1e-3
    runner.config = RunnerConfig(
        board_path="/tmp/fake.kicad_pcb", run_dir="/tmp/run", via_hole_seeds=0,
    )
    assert runner._hole_escape_seeds(session, pair, runner.config, hints) == []
    # A refusal that does not name a hole is not this family's business.
    assert runner._hole_escape_seeds(
        session, pair, runner.config,
        (("Clearance violation", 25.0, 10.0, 1),),
    ) == []


def test_the_via_search_records_its_hole_seeds():
    """The search's own evidence names the seeds, so a sample stays a sample."""
    from tests.agent.fake_engine import FakeEngine, ViaInfo

    via = ViaInfo(x_mm=25.0, y_mm=10.0, diameter_mm=0.6, drill_mm=0.3)
    engine = FakeEngine(
        clusters={"A": {(10.0, 15.0, 1)}, "B": {(40.0, 15.0, 1)}},
    )
    engine.state.vias.append(via)
    session = AgentSession(engine, board_path="/tmp/fake.kicad_pcb")
    engine.build_connectivity()
    pair = _pair(net=1, start=(10.0, 15.0, 1), target=(40.0, 15.0, 1))

    history = AttemptHistory()
    history.note(AttemptRecord(
        pair_key=pair.key, plan_key=(), candidate="direct_walkaround",
        mode="walkaround", kind="direct", source="deterministic",
        outcome="routing_failed", accepted=False, connected=False,
        reason="drc_regression", closed_before_refusal=True,
        drc_classes=(("Hole clearance violation", 3),),
        drc_hints=(("Hole clearance violation", 25.0, 10.0, 1),),
        board_digest=None,
    ))

    class _State:
        def __init__(self):
            self.attempts = history
            self.metrics: dict = {}

    runner = RoutingRunner.__new__(RoutingRunner)
    runner.config = RunnerConfig(
        board_path="/tmp/fake.kicad_pcb", run_dir="/tmp/run",
        via_hole_seeds=2, via_search_probes=64,
    )
    runner.state = _State()
    runner._pending_digest = None
    runner._via_position_cache = {}
    runner._stop_requested = ""
    runner._via_free_positions(session, pair, runner.config)
    evidence = runner.state.metrics["via_search"]
    entry = next(iter(evidence.values()))
    assert len(entry["hole_seeds"]) == 2
    assert "sample" in entry["sampled"]


# ---------------------------------------------------------------------------
# Strategy B - fresh-first coverage
# ---------------------------------------------------------------------------


def _two_pair_engine() -> FakeEngine:
    """Two nets: net 1 has two outstanding pairs, net 2 has one."""
    clusters = {
        "A1": {(10.0, 10.0, 1)}, "B1": {(40.0, 10.0, 1)},
        "A2": {(10.0, 20.0, 1)}, "B2": {(11.0, 20.0, 1)},
        "C1": {(60.0, 10.0, 1)}, "D1": {(90.0, 10.0, 1)},
    }
    ratsnest = [
        RatsnestEdge(10.0, 10.0, 40.0, 10.0, 1, 0, 0),   # net 1, long
        RatsnestEdge(10.0, 20.0, 11.0, 20.0, 1, 0, 0),   # net 1, short
        RatsnestEdge(60.0, 10.0, 90.0, 10.0, 2, 0, 0),   # net 2
    ]
    return FakeEngine(clusters=clusters, ratsnest=ratsnest)


def test_scan_without_fresh_is_the_plain_capped_queue():
    engine = _two_pair_engine()
    session = AgentSession(engine, board_path="/tmp/fake.kicad_pcb")
    engine.build_connectivity()
    scan = scan_net_pairs(
        session, resolver=LayerResolver.build(session), max_per_net=1,
        include_components=False, include_substitutions=False,
    )
    assert scan.coverage_promoted == 0
    # One pair per net: the shortest on net 1, and net 2's only pair.
    assert sorted(pair.net_code for pair in scan.pairs) == [1, 2]
    assert scan.skipped_net_cap == 1


def test_fresh_pass_adds_the_never_attempted_pair_behind_a_cap():
    engine = _two_pair_engine()
    session = AgentSession(engine, board_path="/tmp/fake.kicad_pcb")
    engine.build_connectivity()
    resolver = LayerResolver.build(session)
    base = scan_net_pairs(
        session, resolver=resolver, max_per_net=1,
        include_components=False, include_substitutions=False,
    )
    # Pretend this run already spent its attempts on the capped representative.
    attempted = {pair.key for pair in base.pairs}
    scan = scan_net_pairs(
        session, resolver=resolver, max_per_net=1,
        include_components=False, include_substitutions=False,
        fresh=lambda candidate: candidate.key not in attempted,
    )
    assert scan.coverage_promoted == 1
    fresh_pairs = [pair for pair in scan.pairs if pair.key not in attempted]
    assert len(fresh_pairs) == 1
    assert fresh_pairs[0].net_code == 1
    # Nothing is dropped to make room.
    assert {pair.key for pair in base.pairs} <= {pair.key for pair in scan.pairs}


def test_fresh_pass_is_a_no_op_when_a_net_already_offers_a_fresh_pair():
    engine = _two_pair_engine()
    session = AgentSession(engine, board_path="/tmp/fake.kicad_pcb")
    engine.build_connectivity()
    scan = scan_net_pairs(
        session, resolver=LayerResolver.build(session), max_per_net=1,
        include_components=False, include_substitutions=False,
        fresh=lambda candidate: True,
    )
    assert scan.coverage_promoted == 0


def test_attempts_on_counts_only_the_current_generation():
    history = AttemptHistory()
    pair = _pair(net=1)
    history.note(AttemptRecord(
        pair_key=pair.key, plan_key=(), candidate="c", mode="walkaround",
        kind="direct", source="deterministic", outcome="routing_failed",
        accepted=False, connected=False, board_digest="digest-b",
    ))
    assert history.attempts_on(pair, "digest-a") == 0
    assert history.attempts_on(pair, "digest-b") == 1
    assert not history.worked_on(pair, "digest-a")
    assert history.worked_on(pair, "digest-b")
    # The lifetime budget is untouched by the generation filter.
    assert history.attempts_for_pair(pair) == 1


def test_coverage_counts_a_sweep_member_as_work_done():
    """A candidate a sweep evaluated (and rolled back) is not a fresh pair.

    The narrower ``attempts_on`` measure deliberately excludes sweep members, so
    it must not decide coverage: a pair with six evaluated-and-discarded plans
    looked untouched and went straight back to the front of the queue, which is
    the starvation the coverage pass exists to stop.
    """
    history = AttemptHistory()
    pair = _pair(net=1)
    for index in range(6):
        history.note(AttemptRecord(
            pair_key=pair.key, plan_key=("walkaround", ((float(index), 1.0, 1),)),
            candidate=f"detour_{index}", mode="walkaround", kind="detour",
            source="deterministic", outcome="routing_failed", accepted=False,
            connected=False, sweep_member=True, committed=False,
            board_digest="digest-a",
        ))
    assert history.attempts_on(pair, "digest-a") == 0     # no *real* attempt spent
    assert history.records_on(pair, "digest-a") == 6      # but real work was done
    assert history.worked_on(pair, "digest-a")


def test_selector_takes_the_least_attempted_pair_first():
    engine = _two_pair_engine()
    session = AgentSession(engine, board_path="/tmp/fake.kicad_pcb")
    engine.build_connectivity()
    resolver = LayerResolver.build(session)
    scan = scan_net_pairs(
        session, resolver=resolver, max_per_net=2,
        include_components=False, include_substitutions=False,
    )
    assert len(scan.pairs) >= 3
    ordered = sorted(scan.pairs, key=lambda item: item.key)
    spent, untouched = ordered[0], ordered[-1]

    history = AttemptHistory()
    for _ in range(2):
        history.note(AttemptRecord(
            pair_key=spent.key, plan_key=(), candidate="c", mode="walkaround",
            kind="direct", source="deterministic", outcome="routing_failed",
            accepted=False, connected=False, board_digest=None,
        ))

    class _State:
        attempts = history
        metrics: dict = {}

    runner = RoutingRunner.__new__(RoutingRunner)
    runner.config = RunnerConfig(
        board_path="/tmp/fake.kicad_pcb", run_dir="/tmp/run",
        coverage_first=True,
    )
    runner.state = _State()
    chosen = runner._next_pair(list(reversed(ordered)), None)
    assert chosen is not None and chosen.key != spent.key
    assert chosen.key == untouched.key


def test_selector_keeps_the_cursor_rotation_between_equals():
    engine = _two_pair_engine()
    session = AgentSession(engine, board_path="/tmp/fake.kicad_pcb")
    engine.build_connectivity()
    scan = scan_net_pairs(
        session, resolver=LayerResolver.build(session), max_per_net=2,
        include_components=False, include_substitutions=False,
    )
    pairs = list(scan.pairs)

    class _State:
        def __init__(self):
            self.attempts = AttemptHistory()
            self.metrics = {}

    runner = RoutingRunner.__new__(RoutingRunner)
    runner.config = RunnerConfig(
        board_path="/tmp/fake.kicad_pcb", run_dir="/tmp/run",
        coverage_first=True,
    )
    runner.state = _State()
    first = runner._next_pair(pairs, None)
    second = runner._next_pair(pairs, None)
    assert first is not None and second is not None
    assert first.key != second.key


def test_selector_prefers_a_pair_with_an_unanswered_recorded_refusal():
    """Selection is half of the violation-centred search.

    Generating a refusal-derived plan is not enough: if the selector never picks
    the pair the plan belongs to, it is never evaluated. A pair carrying a
    recorded violation therefore outranks an untouched pair, and stops doing so
    once every refusal-derived plan it produces is already tried.
    """
    engine = _two_pair_engine()
    session = AgentSession(engine, board_path="/tmp/fake.kicad_pcb")
    engine.build_connectivity()
    pairs = list(scan_net_pairs(
        session, resolver=LayerResolver.build(session), max_per_net=2,
        include_components=False, include_substitutions=False,
    ).pairs)
    assert len(pairs) >= 2
    hinted, untouched = pairs[0], pairs[-1]

    history = AttemptHistory()
    history.note(AttemptRecord(
        pair_key=hinted.key, plan_key=(), candidate="direct_walkaround",
        mode="walkaround", kind="direct", source="deterministic",
        outcome="routing_failed", accepted=False, connected=False,
        reason="drc_regression", closed_before_refusal=True,
        drc_classes=(("Clearance violation", 4),),
        drc_hints=(("Clearance violation", 25.0, 16.2, 1),),
        board_digest=None,
    ))

    class _State:
        attempts = history
        metrics: dict = {}

    runner = RoutingRunner.__new__(RoutingRunner)
    runner.config = RunnerConfig(
        board_path="/tmp/fake.kicad_pcb", run_dir="/tmp/run",
        coverage_first=True, drc_candidate_limit=2,
    )
    runner.state = _State()
    runner._pending_digest = None
    runner._copper_layers_cache = 2

    # hinted has one record, untouched has none: without the refusal rule the
    # selector would take `untouched` (fewer records on this board).
    assert runner._refusal_unanswered(hinted)
    assert not runner._refusal_unanswered(untouched)
    chosen = runner._next_pair(list(reversed(pairs)), None)
    assert chosen is not None and chosen.key == hinted.key

    # Once the refusal-derived plans are all tried, the pair loses its priority.
    from pcb_world.agent.scheduler import generate_candidates
    for candidate in generate_candidates(
        hinted, copper_layers=2,
        drc_hints=(("Clearance violation", 25.0, 16.2, 1),),
        max_drc_candidates=2,
    ):
        history.note(AttemptRecord(
            pair_key=hinted.key, plan_key=candidate.key(), candidate=candidate.name,
            mode=candidate.mode, kind=candidate.kind, source="deterministic",
            outcome="routing_failed", accepted=False, connected=False,
            board_digest=None,
        ))
    # The extent family is enabled by default and this pure computation cannot
    # reproduce it (it needs the obstacle observation), so it counts as
    # unanswered until a record of that family exists. That is deliberate: a
    # family the pair is entitled to and has never had evaluated is work it
    # still has coming.
    assert runner._refusal_unanswered(hinted)
    history.note(AttemptRecord(
        pair_key=hinted.key, plan_key=("walkaround", ((11.0, 26.0, 1),)),
        candidate="drc_extent_11.00_26.00", mode="walkaround", kind="drc_extent",
        source="deterministic", outcome="routing_failed", accepted=False,
        connected=False, board_digest=None,
    ))
    assert not runner._refusal_unanswered(hinted)


def test_selector_keeps_priority_while_an_enabled_family_has_not_run():
    """An unrun refusal family outranks a pair with no family at all.

    The two session-computed families (obstacle-extent and hole-derived via
    sites) cannot be reproduced from history, so their kind is compared against
    the pair's records. Disabling a family, or a pair whose hint is not that
    family's class, must not manufacture the preference.
    """
    engine = _two_pair_engine()
    session = AgentSession(engine, board_path="/tmp/fake.kicad_pcb")
    engine.build_connectivity()
    pair = list(scan_net_pairs(
        session, resolver=LayerResolver.build(session), max_per_net=1,
        include_components=False, include_substitutions=False,
    ).pairs)[0]
    history = AttemptHistory()
    history.note(AttemptRecord(
        pair_key=pair.key, plan_key=(), candidate="direct_walkaround",
        mode="walkaround", kind="direct", source="deterministic",
        outcome="routing_failed", accepted=False, connected=False,
        reason="drc_regression", closed_before_refusal=True,
        drc_classes=(("Hole clearance violation", 3),),
        drc_hints=(("Hole clearance violation", 25.0, 16.2, 1),),
        board_digest=None,
    ))

    class _State:
        attempts = history
        metrics: dict = {}

    runner = RoutingRunner.__new__(RoutingRunner)
    runner.state = _State()
    runner._pending_digest = None
    runner._copper_layers_cache = 2

    hole_only = RunnerConfig(
        board_path="/tmp/fake.kicad_pcb", run_dir="/tmp/run",
        coverage_first=True, drc_candidate_limit=2, drc_extent_probes=0,
        via_search=True,
    )
    runner.config = hole_only
    # The pure generator's own plans are all tried; what is left is the hole
    # family, which this computation cannot reproduce. A pair with a hole-class
    # hint and no via record therefore still has work coming.
    from pcb_world.agent.scheduler import generate_candidates
    for candidate in generate_candidates(
        pair, copper_layers=2,
        drc_hints=(("Hole clearance violation", 25.0, 16.2, 1),),
        max_drc_candidates=int(hole_only.drc_candidate_limit),
        max_clearance_probes=int(hole_only.drc_clearance_probes),
        drc_clearance_mm=float(hole_only.drc_clearance_mm),
        max_extent_probes=int(hole_only.drc_extent_probes),
    ):
        history.note(AttemptRecord(
            pair_key=pair.key, plan_key=candidate.key(), candidate=candidate.name,
            mode=candidate.mode, kind=candidate.kind, source="deterministic",
            outcome="routing_failed", accepted=False, connected=False,
            board_digest=None,
        ))
    assert runner._refusal_unanswered(pair)
    history.note(AttemptRecord(
        pair_key=pair.key, plan_key=(), candidate="via_free_0_25.00_16.00_L2",
        mode="walkaround", kind="via_free", source="deterministic",
        outcome="routing_failed", accepted=False, connected=False,
        board_digest=None,
    ))
    assert not runner._refusal_unanswered(pair)


def test_a_hole_search_that_ran_and_found_nothing_counts_as_evaluated():
    """An unproductive search is a result, not an unfinished family.

    The runner records the holes family's own search evidence per pair even when
    the search produced no spot. Once that evidence exists for a pair, the family
    has been evaluated and the pair must stop outranking pairs whose hint-class
    families have genuinely never run - otherwise a pair with no lawful spot
    would hold the front of the queue for the rest of the run.
    """
    engine = _two_pair_engine()
    session = AgentSession(engine, board_path="/tmp/fake.kicad_pcb")
    engine.build_connectivity()
    pair = list(scan_net_pairs(
        session, resolver=LayerResolver.build(session), max_per_net=1,
        include_components=False, include_substitutions=False,
    ).pairs)[0]
    history = AttemptHistory()
    history.note(AttemptRecord(
        pair_key=pair.key, plan_key=(), candidate="direct_walkaround",
        mode="walkaround", kind="direct", source="deterministic",
        outcome="routing_failed", accepted=False, connected=False,
        reason="drc_regression", closed_before_refusal=True,
        drc_classes=(("Hole clearance violation", 3),),
        drc_hints=(("Hole clearance violation", 25.0, 16.2, 1),),
        board_digest=None,
    ))
    from pcb_world.agent.scheduler import generate_candidates
    from pcb_world.agent.runner import RunnerConfig as _Config
    for candidate in generate_candidates(
        pair, copper_layers=2,
        drc_hints=(("Hole clearance violation", 25.0, 16.2, 1),),
        max_drc_candidates=2, max_extent_probes=0,
    ):
        history.note(AttemptRecord(
            pair_key=pair.key, plan_key=candidate.key(), candidate=candidate.name,
            mode=candidate.mode, kind=candidate.kind, source="deterministic",
            outcome="routing_failed", accepted=False, connected=False,
            board_digest=None,
        ))

    class _State:
        def __init__(self, metrics):
            self.attempts = history
            self.metrics = metrics

    runner = RoutingRunner.__new__(RoutingRunner)
    runner.config = _Config(
        board_path="/tmp/fake.kicad_pcb", run_dir="/tmp/run",
        coverage_first=True, drc_candidate_limit=2, drc_extent_probes=0,
    )
    runner.state = _State({})
    runner._pending_digest = None
    runner._copper_layers_cache = 2
    assert runner._refusal_unanswered(pair)
    import json as _json
    runner.state.metrics["via_search"] = {
        _json.dumps(pair.key): {"positions": [], "probes": 12, "truncated": ""},
    }
    assert not runner._refusal_unanswered(pair)
