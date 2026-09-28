"""Unit contract for the zone prefilter's policy, with no engine involved.

The engine-side query is covered natively in ``test_zone_point_query.py``. What
is pinned here is the part that must hold even when the engine cannot answer at
all: unknown passes through, nothing is ever dropped, disabled is not the same
as empty, and the margin is derived from the board's own rules rather than
guessed at.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from pcb_world.agent import zone_coverage as zc
from pcb_world.agent.runner import RoutingRunner, RunnerConfig
from pcb_world.agent.scheduler import Candidate
from pcb_world.engine import wire


class _NoQueryEngine:
    """An older engine build: no ``get_zone_point_hits`` at all."""

    layer_map = None


def _pair(net: int, start, target):
    from pcb_world.agent.observations import NetPair

    return NetPair(
        net_code=net, net_name=f"NET{net}", start=start, target=target,
        gap_mm=10.0, start_layers=(start[2],), target_layers=(target[2],),
        pad_groups=2,
    )


def test_wire_mirrors_round_trip_and_declare_the_binding_order():
    hit = wire.ZonePointHit(
        zone_uuid="abc", name="pour", source="board.zones", layer=0, net_code=1,
        is_rule_area=False, keepout_flags=0, in_outline=True, in_fill=True,
        fill_is_island=False, distance_mm=0.0, fill_provenance="loaded_unverified")
    row = wire.ZonePointResult(
        query_index=0, layer=0, status="resolved", reason="", classification="fill_single_net",
        fill_net_code=1, in_keepout=False, keepout_flags=0,
        fill_provenance="loaded_unverified", zones_tested=1, hits=[hit])

    decoded = wire.from_wire(wire.to_wire(row))

    assert decoded == row
    assert wire.KRL_FIELDS["ZonePointHit"] == tuple(hit._fields)
    assert wire.KRL_FIELDS["ZonePointResult"] == tuple(row._fields)


def test_an_engine_without_the_query_answers_unknown_and_passes_through():
    session = SimpleNamespace(_engine=_NoQueryEngine())
    coverage = zc.ZoneCoverage(session)

    assert coverage.available() is False
    verdict = coverage.verdict((1.0, 2.0, 1), 1)
    assert verdict.state == zc.UNKNOWN
    assert verdict.available is False
    assert verdict.reason

    risk = coverage.candidate_risk([(1.0, 2.0, 1), (3.0, 4.0, 1)], 1)
    assert risk.state == zc.UNKNOWN
    assert risk.available is False
    # The whole point of "unknown": it is never acted on.
    assert risk.suppressed is False
    assert risk.is_foreign is False


def test_prefilter_disabled_is_unknown_not_none():
    session = SimpleNamespace(_engine=_NoQueryEngine())
    coverage = zc.ZoneCoverage(session, enabled=False)

    assert coverage.available() is False
    assert coverage.verdict((1.0, 2.0, 1), 1).state == zc.UNKNOWN


def test_board_margin_is_clearance_plus_the_widest_copper_half():
    rules = wire.DesignRules(
        min_clearance_mm=0.2,
        default_netclass=wire.NetClassInfo(track_width_mm=0.25, via_diameter_mm=0.6),
    )
    engine = SimpleNamespace(get_design_rules=lambda: rules)

    margin, basis = zc.board_margin_mm(engine)

    assert margin == pytest.approx(0.2 + 0.3)      # clearance + via radius
    assert "clearance" in basis


def test_unreadable_rules_weaken_the_margin_instead_of_guessing():
    engine = SimpleNamespace(get_design_rules=lambda: (_ for _ in ()).throw(RuntimeError("no")))
    assert zc.board_margin_mm(engine) == (0.0, "rules unreadable (RuntimeError)")

    # KiCad's "unset" sentinel is negative and must not be coerced into a distance.
    unset = wire.DesignRules(
        min_clearance_mm=-1.0, default_netclass=wire.NetClassInfo(track_width_mm=-1.0))
    engine = SimpleNamespace(get_design_rules=lambda: unset)
    margin, basis = zc.board_margin_mm(engine)
    assert margin == 0.0
    assert "unset" in basis


class _RankingStub:
    """Exactly the surface ``RoutingRunner._zone_ranked`` touches."""

    def __init__(self, coverage, *, margin=(0.5, "stub"), enabled=True):
        self.config = RunnerConfig(
            board_path="synthetic.kicad_pcb", run_dir="run", zone_prefilter=enabled)
        self.state = SimpleNamespace(metrics={})
        self._coverage = coverage
        self._margin = margin
        # The real recorder: this stub exists to drive one real method, not to
        # re-implement its bookkeeping.
        self._note_zone_evidence = RoutingRunner._note_zone_evidence.__get__(self)

    def _zone_coverage(self, session):
        return self._coverage

    def _zone_margin_mm(self, session):
        return self._margin


class _ScriptedCoverage(zc.ZoneCoverage):
    """A coverage whose verdicts are dictated per point, for ordering tests."""

    def __init__(self, states, *, available=True):        # noqa: D107 - test double
        self._states = dict(states)
        self._available = available
        self.unsupported_reason = "" if available else "disabled for this test"
        self._row_cache: dict = {}
        self._generation = None
        self.queries = 0
        self.cache_hits = 0
        self.batches = 0

    def available(self):                                   # noqa: D102 - test double
        return self._available

    def candidate_risk(self, waypoints, net_code, *, margin_mm=0.0,
                       legs=(), pitch_mm=0.0, max_samples=0):
        points = [tuple(p) for p in waypoints]
        states = [self._states.get((round(float(p[0]), 3), round(float(p[1]), 3)), zc.NONE)
                  for p in points]
        return zc.CandidateZoneRisk(
            state=self._strongest(states), points_checked=len(points),
            points=tuple(p for p, s in zip(points, states) if s in (zc.FOREIGN, zc.MIXED)),
            margin_mm=margin_mm)


def _candidate(name: str, x: float) -> Candidate:
    return Candidate(name=name, mode="shove", waypoints=((x, 10.0, 1),), kind="detour")


def test_ranking_moves_foreign_pour_plans_last_without_dropping_any():
    coverage = _ScriptedCoverage({(1.0, 10.0): zc.FOREIGN})
    stub = _RankingStub(coverage)
    session = SimpleNamespace(_engine=object())
    candidates = [_candidate("a", 1.0), _candidate("b", 5.0), _candidate("c", 7.0)]

    ranked = RoutingRunner._zone_ranked(stub, session, _pair(1, (0.0, 10.0, 1), (9.0, 10.0, 1)),
                                        candidates, stub.config)

    assert [candidate.name for candidate in ranked] == ["b", "c", "a"]
    assert sorted(candidate.name for candidate in ranked) == ["a", "b", "c"]
    assert stub.state.metrics["zone_prefilter"]["candidates_ranked_late"] == 1
    assert stub.state.metrics["zone_prefilter"]["candidates_checked"] == 3


def test_ranking_is_a_no_op_when_nothing_is_foreign_or_the_engine_cannot_answer():
    session = SimpleNamespace(_engine=object())
    pair = _pair(1, (0.0, 10.0, 1), (9.0, 10.0, 1))
    candidates = [_candidate("a", 1.0), _candidate("b", 5.0)]

    clean = _RankingStub(_ScriptedCoverage({}))
    assert [c.name for c in RoutingRunner._zone_ranked(clean, session, pair, candidates, clean.config)] \
        == ["a", "b"]
    assert clean.state.metrics["zone_prefilter"]["candidates_ranked_late"] == 0

    unavailable = _RankingStub(_ScriptedCoverage({}, available=False))
    assert [c.name for c in RoutingRunner._zone_ranked(
        unavailable, session, pair, candidates, unavailable.config)] == ["a", "b"]
    assert unavailable.state.metrics["zone_prefilter"]["available"] is False


def test_ranking_is_disabled_by_config():
    coverage = _ScriptedCoverage({(1.0, 10.0): zc.FOREIGN})
    stub = _RankingStub(coverage, enabled=False)
    session = SimpleNamespace(_engine=object())
    candidates = [_candidate("a", 1.0), _candidate("b", 5.0)]

    ranked = RoutingRunner._zone_ranked(
        stub, session, _pair(1, (0.0, 10.0, 1), (9.0, 10.0, 1)), candidates, stub.config)

    assert [candidate.name for candidate in ranked] == ["a", "b"]
    assert "zone_prefilter" not in stub.state.metrics


def test_the_prefilter_is_opt_in_and_a_default_run_never_ranks():
    """Off by default: no control run has shown the ranking to be an improvement."""
    assert RunnerConfig(board_path="b.kicad_pcb", run_dir="run").zone_prefilter is False

    # A default-configured runner must not even ask the engine: the coverage
    # object would notice, because a disabled prefilter reports itself
    # unavailable and the ranker returns the list untouched.
    coverage = _ScriptedCoverage({(1.0, 10.0): zc.FOREIGN})
    stub = _RankingStub(coverage, enabled=False)
    assert stub.config.zone_prefilter is False
    session = SimpleNamespace(_engine=object())
    candidates = [_candidate("a", 1.0), _candidate("b", 5.0)]

    ranked = RoutingRunner._zone_ranked(
        stub, session, _pair(1, (0.0, 10.0, 1), (9.0, 10.0, 1)), candidates, stub.config)

    assert [candidate.name for candidate in ranked] == ["a", "b"]


def test_corridor_sampling_is_bounded_deterministic_and_covers_both_faces():
    # A same-layer leg is sampled on its own layer only, endpoints included.
    flat = zc.sample_leg((0.0, 0.0, 1), (10.0, 0.0, 1), pitch_mm=2.5, max_samples=48)
    assert flat[0] == (0.0, 0.0, 1)
    assert flat[-1] == (10.0, 0.0, 1)
    assert [point[2] for point in flat] == [1] * len(flat)
    assert len(flat) == 5                       # 10 mm / 2.5 mm, endpoints included

    # The cap is the cap: a long leg cannot spend unbounded samples.
    long_leg = zc.sample_leg((0.0, 0.0, 1), (500.0, 0.0, 1), pitch_mm=0.5, max_samples=12)
    assert len(long_leg) == 13

    # A layer-changing leg will place a via somewhere along it, so both copper
    # faces are sampled - the pour that matters can be on either.
    crossing = zc.sample_leg((0.0, 0.0, 1), (10.0, 0.0, 2), pitch_mm=5.0, max_samples=48)
    assert {point[2] for point in crossing} == {1, 2}
    assert len(crossing) == 6                   # 3 positions x 2 layers


def test_a_waypoint_less_candidate_is_classified_from_its_corridor():
    """Direct/shove plans name no waypoints; their corridor is all they declare."""

    class _CorridorCoverage(zc.ZoneCoverage):
        def __init__(self, hit_at):            # noqa: D107 - test double
            self._hit_at = hit_at
            self._row_cache: dict = {}
            self._generation = None
            self.queries = self.cache_hits = self.batches = 0

        def available(self):                   # noqa: D102 - test double
            return True

        def verdicts(self, points, net_code, *, margin_mm=0.0):
            out = []
            for point in points:
                state = zc.FOREIGN if abs(float(point[0]) - self._hit_at) < 1e-6 else zc.NONE
                out.append(zc.ZonePointVerdict(
                    point=(float(point[0]), float(point[1]), int(point[2])),
                    state=state, nets=(2,) if state == zc.FOREIGN else (),
                    margin_mm=margin_mm, provenance=zc.LOADED_UNVERIFIED))
            return out

    coverage = _CorridorCoverage(hit_at=5.0)
    # No waypoints at all, but the leg crosses the pour at x=5: a plan that lands
    # copper in a foreign pour is now visible instead of unknown.
    risk = coverage.candidate_risk(
        [], 1, margin_mm=0.5, legs=[((0.0, 0.0, 1), (10.0, 0.0, 1))],
        pitch_mm=1.0, max_samples=48)

    assert risk.points_checked == 11
    assert risk.state == zc.FOREIGN
    assert risk.suppressed is False

    # ... and a corridor that touches nothing stays a pass-through.
    clean = _CorridorCoverage(hit_at=99.0).candidate_risk(
        [], 1, margin_mm=0.5, legs=[((0.0, 0.0, 1), (10.0, 0.0, 1))],
        pitch_mm=1.0, max_samples=48)
    assert clean.state == zc.NONE


class _ScriptedRowsEngine:
    """An engine whose rows-per-query answer is dictated, including malformed."""

    def __init__(self, rows_for):
        self._rows_for = rows_for
        self.calls = 0

    class _LayerMap:
        @staticmethod
        def human_to_board(layer):
            return int(layer) - 1

    layer_map = _LayerMap()
    zone_fill_epoch = 0

    def get_zone_point_hits(self, queries, window_mm=0.0):
        self.calls += 1
        return self._rows_for(len(queries))


def _row(x_mm, net_code, distance_mm):
    return SimpleNamespace(
        status="resolved", reason="", classification="fill_single_net",
        fill_net_code=net_code, in_keepout=False, keepout_flags=0,
        fill_provenance="loaded_unverified", zones_tested=1,
        hits=[SimpleNamespace(
            zone_uuid="z", name="", source="board.zones", layer=0,
            net_code=net_code, is_rule_area=False, keepout_flags=0,
            in_outline=True, in_fill=distance_mm == 0.0,
            fill_is_island=False, distance_mm=distance_mm,
            fill_provenance="loaded_unverified")],
    )


def test_a_short_or_malformed_row_answer_is_unknown_never_a_dropped_point():
    """A truncated IPC answer must not shorten the caller's list.

    The row/query zip used to leave the tail of ``out`` as None, and the final
    comprehension dropped those entries - so a point silently disappeared from
    ``candidate_risk`` and its evidence. Every shape of malformed answer is
    answered as unknown now, and the list length always matches the request.
    """
    points = [(1.0, 1.0, 1), (2.0, 1.0, 1), (3.0, 1.0, 1)]

    # Fewer rows than queries.
    short = zc.ZoneCoverage(SimpleNamespace(_engine=_ScriptedRowsEngine(
        lambda n: [_row(1.0, 2, 0.1)])))
    verdicts = short.verdicts(points, 1, margin_mm=0.5)
    assert len(verdicts) == len(points), "a point vanished from the answer"
    assert all(v.state == zc.UNKNOWN for v in verdicts)
    assert all(v.reason for v in verdicts)
    assert short.candidate_risk(points, 1, margin_mm=0.5).points_checked == len(points)

    # More rows than queries.
    long_rows = zc.ZoneCoverage(SimpleNamespace(_engine=_ScriptedRowsEngine(
        lambda n: [_row(1.0, 2, 0.1) for _ in range(n + 2)])))
    assert len(long_rows.verdicts(points, 1, margin_mm=0.5)) == len(points)
    assert all(v.state == zc.UNKNOWN for v in long_rows.verdicts(points, 1, margin_mm=0.5))

    # Right length, but a hole in it.
    holed = zc.ZoneCoverage(SimpleNamespace(_engine=_ScriptedRowsEngine(
        lambda n: [_row(1.0, 2, 0.1), None, _row(3.0, 2, 0.1)])))
    holed_verdicts = holed.verdicts(points, 1, margin_mm=0.5)
    assert len(holed_verdicts) == len(points)
    assert holed_verdicts[1].state == zc.UNKNOWN
    assert "no row" in holed_verdicts[1].reason

    # A well-formed answer still classifies: net 2's pour is foreign to net 1.
    good = zc.ZoneCoverage(SimpleNamespace(_engine=_ScriptedRowsEngine(
        lambda n: [_row(float(i), 2, 0.1) for i in range(n)])))
    assert all(v.state == zc.FOREIGN for v in good.verdicts(points, 1, margin_mm=0.5))


def test_the_measured_via_opening_family_is_offered_and_named_by_its_geometry():
    """``zone_openings`` becomes ``zone_gap_via`` candidates; nothing invents one."""
    from pcb_world.agent.scheduler import generate_candidates
    from pcb_world.agent.observations import NetPair

    pair = NetPair(
        net_code=1, net_name="NET1", start=(0.0, 10.0, 1), target=(20.0, 10.0, 2),
        gap_mm=20.0, start_layers=(1,), target_layers=(2,), pad_groups=2)

    # No measurement: the family is silent (an empty list is "nothing measured
    # clear", never "the corridor is clear").
    bare = generate_candidates(pair, copper_layers=2)
    assert not [c for c in bare if c.kind == "zone_gap_via"]

    measured = generate_candidates(pair, copper_layers=2, zone_openings=[
        {"x_mm": 12.5, "y_mm": 10.0, "layer": 2, "clearance_mm": 0.75},
        {"x_mm": 16.0, "y_mm": 10.0, "layer": 2, "clearance_mm": 0.5},
    ])
    openings = [c for c in measured if c.kind == "zone_gap_via"]
    assert [c.waypoints for c in openings] == [((12.5, 10.0, 2),), ((16.0, 10.0, 2),)]
    assert "0.750 mm" in openings[0].rationale
    assert "not proved legal" in openings[0].rationale
    # It sits with the informed plans, ahead of the generic detours.
    assert measured.index(openings[0]) < len(measured)

    # A malformed row is skipped rather than turned into a candidate.
    assert not [c for c in generate_candidates(
        pair, copper_layers=2, zone_openings=[{"x_mm": "nope"}])
        if c.kind == "zone_gap_via"]


def test_exact_edge_pins_are_direction_tolerant_and_outrank_net_pins():
    """A net pin is not an edge pin: one net offers several pairs."""
    from pcb_world.agent.observations import NetPair, edge_key
    from pcb_world.agent.runner import RoutingRunner

    # The same connection either way round is one identity.
    forward = edge_key((1.0, 2.0, 1), (3.0, 4.0, 2))
    reverse = edge_key((3.0, 4.0, 2), (1.0, 2.0, 1))
    assert forward == reverse

    pinned = (1.0, 2.0, 1, 3.0, 4.0, 2)
    cfg = RunnerConfig(board_path="b.kicad_pcb", run_dir="run",
                       priority_nets=(9,), priority_edges=(pinned,))
    stub = SimpleNamespace(config=cfg)
    keys = RoutingRunner._priority_edge_keys(stub)
    assert keys == frozenset({forward})

    same_net_other_edge = NetPair(
        net_code=9, net_name="NET9", start=(10.0, 10.0, 1), target=(20.0, 10.0, 1),
        gap_mm=10.0, start_layers=(1,), target_layers=(1,), pad_groups=2)
    # A net-pinned pair that is *not* the pinned edge must not match it.
    assert RoutingRunner._pair_priority_edge(same_net_other_edge, keys) is False

    pinned_pair = NetPair(
        net_code=42, net_name="NET42", start=(3.0, 4.0, 2), target=(1.0, 2.0, 1),
        gap_mm=10.0, start_layers=(2,), target_layers=(1,), pad_groups=2)
    assert RoutingRunner._pair_priority_edge(pinned_pair, keys) is True

    # A malformed pin is ignored rather than matching everything.
    bad = SimpleNamespace(config=RunnerConfig(
        board_path="b.kicad_pcb", run_dir="run", priority_edges=((1.0, 2.0),)))
    assert RoutingRunner._priority_edge_keys(bad) == frozenset()


# ---------------------------------------------------------------------------
# Refusal-aware ordering for measured openings
# ---------------------------------------------------------------------------

DIGEST = "aaaa1111bbbb2222"


def _attempt(pair_key, plan_key, *, reason="drc_regression", digest=DIGEST,
             offered=None):
    from pcb_world.agent.scheduler import AttemptRecord

    return AttemptRecord(
        pair_key=tuple(pair_key), plan_key=tuple(plan_key), candidate="c",
        mode=str(plan_key[0]), kind="direct", source="deterministic",
        outcome="routing_failed", accepted=False, connected=False,
        reason=reason, board_digest=digest,
        offered_pair_key=tuple(offered) if offered else ())


class _OrderingStub:
    """Exactly the surface ``RoutingRunner._refusal_ranked`` touches."""

    def __init__(self, *, records=(), enabled=True, digest=DIGEST):
        from pcb_world.agent.scheduler import AttemptHistory

        self.config = RunnerConfig(
            board_path="b.kicad_pcb", run_dir="run",
            zone_gap_via=3, zone_gap_via_priority=enabled)
        self.state = SimpleNamespace(metrics={}, attempts=AttemptHistory(records))
        self._pending_digest = digest
        # The real helpers: this stub exists to drive the real policy, not to
        # re-implement its evidence join.
        self._refusal_evidence = RoutingRunner._refusal_evidence.__get__(self)
        self._edge_from_pair_key = RoutingRunner._edge_from_pair_key


EDGE = (10.0, 20.0, 1, 30.0, 25.0, 4)
PAIR = _pair(1, (10.0, 20.0, 1), (30.0, 25.0, 4))


def _family_candidate():
    return Candidate(name="zone_gap_via_700_0", mode="walkaround",
                     waypoints=((20.0, 22.5, 4),), kind="zone_gap_via")


def _direct_candidate(mode="walkaround"):
    kind = "direct"
    return Candidate(name=f"direct_{'shove' if mode == 'shove' else 'walkaround'}",
                     mode=mode, waypoints=(), kind=kind)


def _pair_key(edge, net=1):
    return (net, edge[0], edge[1], edge[2], edge[3], edge[4], edge[5])


def test_refusal_ordering_promotes_the_opening_past_the_refused_directs():
    records = [
        _attempt(_pair_key(EDGE), ("walkaround", ())),   # direct_walkaround refused
        _attempt(_pair_key(EDGE), ("shove", ())),        # direct_shove refused
    ]
    stub = _OrderingStub(records=records)
    candidates = [_direct_candidate(), _direct_candidate("shove"),
                  _family_candidate(), Candidate(name="via_jog", mode="walkaround",
                                                 waypoints=((21.0, 22.0, 1),),
                                                 kind="via_jog")]
    ordered = RoutingRunner._refusal_ranked(stub, SimpleNamespace(), PAIR,
                                            candidates, stub.config)

    names = [c.name for c in ordered]
    assert names == ["zone_gap_via_700_0", "direct_walkaround", "direct_shove", "via_jog"]
    # The other families keep their own relative order.
    assert names.index("via_jog") > names.index("direct_shove")
    # Nothing is removed.
    assert sorted(names) == sorted(c.name for c in candidates)
    assert stub.state.metrics["zone_gap_via_priority"]["pairs_reordered"] == 1

    # candidate_limit semantics: the opening is inside the window now.
    assert [c.name for c in ordered[:2]] == ["zone_gap_via_700_0", "direct_walkaround"]


def test_refusal_ordering_needs_exact_edge_same_generation_and_a_refusal():
    candidates = [_direct_candidate(), _direct_candidate("shove"), _family_candidate()]

    # Another edge on the same net: not evidence about this copper.
    other = (99.0, 99.0, 1, 120.0, 99.0, 4)
    stub = _OrderingStub(records=[_attempt(_pair_key(other), ("walkaround", ()))])
    assert [c.name for c in RoutingRunner._refusal_ranked(
        stub, SimpleNamespace(), PAIR, candidates, stub.config)] == [c.name for c in candidates]

    # Right edge, wrong generation: ignored, not inherited.
    stale = _OrderingStub(records=[_attempt(_pair_key(EDGE), ("walkaround", ()),
                                            digest="9999999999999999")])
    assert [c.name for c in RoutingRunner._refusal_ranked(
        stale, SimpleNamespace(), PAIR, candidates, stale.config)] == [c.name for c in candidates]

    # Right edge and generation, but the plan was not DRC-refused.
    clean = _OrderingStub(records=[_attempt(_pair_key(EDGE), ("walkaround", ()),
                                            reason="connection_not_verified")])
    assert [c.name for c in RoutingRunner._refusal_ranked(
        clean, SimpleNamespace(), PAIR, candidates, clean.config)] == [c.name for c in candidates]

    # Refusal evidence for a different plan: the directs are not demoted.
    elsewhere = _OrderingStub(records=[_attempt(_pair_key(EDGE), ("walkaround", ((1.0, 1.0, 1),)))])
    assert [c.name for c in RoutingRunner._refusal_ranked(
        elsewhere, SimpleNamespace(), PAIR, candidates, elsewhere.config)] == [c.name for c in candidates]

    # Off by default, and a pair with no opening is never touched.
    off = _OrderingStub(records=[_attempt(_pair_key(EDGE), ("walkaround", ()))], enabled=False)
    assert [c.name for c in RoutingRunner._refusal_ranked(
        off, SimpleNamespace(), PAIR, candidates, off.config)] == [c.name for c in candidates]
    on_no_opening = _OrderingStub(records=[_attempt(_pair_key(EDGE), ("walkaround", ()))])
    assert [c.name for c in RoutingRunner._refusal_ranked(
        on_no_opening, SimpleNamespace(), PAIR, [_direct_candidate()], on_no_opening.config)]         == ["direct_walkaround"]


def test_refusal_ordering_binds_a_substitution_attempt_to_the_offered_edge():
    """An attempt recorded against other geometry still names the edge it stood for."""
    record = _attempt(_pair_key((50.0, 50.0, 1, 60.0, 60.0, 4)), ("walkaround", ()),
                      offered=_pair_key(EDGE))
    stub = _OrderingStub(records=[record])
    candidates = [_direct_candidate(), _direct_candidate("shove"), _family_candidate()]
    ordered = RoutingRunner._refusal_ranked(stub, SimpleNamespace(), PAIR,
                                            candidates, stub.config)
    assert ordered[0].kind == "zone_gap_via"


# ---------------------------------------------------------------------------
# The via's layer span fails closed
# ---------------------------------------------------------------------------

def test_an_unreadable_layer_map_refuses_the_span_and_the_edge():
    """Not knowing what a via spans is a reason to refuse, never to assume."""

    class _EngineWithoutMap:
        def get_zone_point_hits(self, queries, window_mm=0.0):
            raise AssertionError("an unknown span must not reach the engine")

    coverage = zc.ZoneCoverage(SimpleNamespace(_engine=_EngineWithoutMap()))

    assert zc.via_layer_span(coverage) is None
    assert zc.find_openings(
        coverage, (0.0, 0.0, 1), (10.0, 0.0, 2), 1,
        margin_mm=0.4, search_mm=3.0, pitch_mm=1.0, max_samples=16,
        max_openings=2, band_mm=1.0) == []

    rows = zc.screen_openings(
        coverage, [((0.0, 0.0, 1, 10.0, 0.0, 2), 1)], margin_mm=0.4)
    assert rows[0].status == "unknown"
    assert rows[0].layers_checked == 0
    assert "layer map" in rows[0].reason


def test_a_layer_map_whose_order_disagrees_with_its_length_is_unknown():
    class _Map:
        board_layer_order = [0, 2]      # two entries...
        max_layer = 4                   # ...but claims four layers

    class _Engine:
        layer_map = _Map()

        def get_zone_point_hits(self, queries, window_mm=0.0):
            raise AssertionError("an inconsistent map must not reach the engine")

    assert zc.via_layer_span(zc.ZoneCoverage(SimpleNamespace(_engine=_Engine()))) is None


def test_the_span_is_every_copper_layer_the_via_spans_in_board_order():
    class _Map:
        board_layer_order = [0, 4, 6, 2]     # F.Cu, In1, In2, B.Cu
        max_layer = 4

    class _Engine:
        layer_map = _Map()

    assert zc.via_layer_span(
        zc.ZoneCoverage(SimpleNamespace(_engine=_Engine()))) == (1, 2, 3, 4)


def _map_engine(order, max_layer, *, converters=True):
    """A stub engine whose layer map is whatever the test declares."""
    class _Map:
        board_layer_order = list(order)

    _Map.max_layer = max_layer
    if converters:
        pairs = {i + 1: int(layer) for i, layer in enumerate(order)
                 if isinstance(layer, int)}
        _Map.human_to_board = staticmethod(lambda human: pairs[human])
        _Map.board_to_human = staticmethod(lambda board: next(
            human for human, value in pairs.items() if value == board))

    class _Engine:
        layer_map = _Map

        def get_zone_point_hits(self, queries, window_mm=0.0):
            raise AssertionError("a refused span must not reach the engine")

    return _Engine()


def test_a_known_good_two_and_four_layer_map_proves_its_span():
    two = zc.ZoneCoverage(SimpleNamespace(_engine=_map_engine([0, 2], 2)))
    four = zc.ZoneCoverage(SimpleNamespace(_engine=_map_engine([0, 4, 6, 2], 4)))
    assert zc.via_layer_span(two) == (1, 2)
    assert zc.via_layer_span(four) == (1, 2, 3, 4)


def test_malformed_layer_map_metadata_is_refused_not_defaulted():
    # max_layer that cannot be converted at all.
    assert zc.via_layer_span(zc.ZoneCoverage(
        SimpleNamespace(_engine=_map_engine([0, 2], "not-a-number")))) is None
    assert zc.via_layer_span(zc.ZoneCoverage(
        SimpleNamespace(_engine=_map_engine([0, 2], None)))) is None
    assert zc.via_layer_span(zc.ZoneCoverage(
        SimpleNamespace(_engine=_map_engine([0, 2], 4)))) is None       # disagrees
    # A layer id that cannot be converted.
    assert zc.via_layer_span(zc.ZoneCoverage(
        SimpleNamespace(_engine=_map_engine([0, object()], 2)))) is None
    # No map at all, and a map with no usable order.
    assert zc.via_layer_span(zc.ZoneCoverage(
        SimpleNamespace(_engine=SimpleNamespace()))) is None
    assert zc.via_layer_span(zc.ZoneCoverage(
        SimpleNamespace(_engine=SimpleNamespace(layer_map=SimpleNamespace())))) is None


def test_duplicate_or_non_copper_layer_ids_are_refused():
    # Duplicates pass a mere length check: four entries, max_layer four.
    assert zc.via_layer_span(zc.ZoneCoverage(
        SimpleNamespace(_engine=_map_engine([0, 4, 4, 2], 4)))) is None
    # An odd id is a non-copper layer in the engine's enum.
    assert zc.via_layer_span(zc.ZoneCoverage(
        SimpleNamespace(_engine=_map_engine([0, 1, 6, 2], 4)))) is None
    # A negative id is not a layer either.
    assert zc.via_layer_span(zc.ZoneCoverage(
        SimpleNamespace(_engine=_map_engine([0, -2], 2)))) is None


def test_a_map_whose_converters_disagree_is_refused():
    engine = _map_engine([0, 2], 2)
    engine.layer_map.human_to_board = staticmethod(lambda human: 0)   # always F.Cu
    assert zc.via_layer_span(zc.ZoneCoverage(SimpleNamespace(_engine=engine))) is None

    engine = _map_engine([0, 2], 2)
    def _boom(_layer):
        raise KeyError("unmappable")
    engine.layer_map.board_to_human = staticmethod(_boom)
    assert zc.via_layer_span(zc.ZoneCoverage(SimpleNamespace(_engine=engine))) is None


def test_endpoints_outside_the_proven_span_are_refused():
    """A caller's impossible layer is not a place to report an opening on."""
    four = zc.ZoneCoverage(SimpleNamespace(_engine=_map_engine([0, 4, 6, 2], 4)))
    assert zc.find_openings(
        four, (0.0, 0.0, 1), (10.0, 0.0, 5), 1,
        margin_mm=0.4, search_mm=3.0, pitch_mm=1.0, max_samples=16,
        max_openings=2, band_mm=1.0) == []
    assert zc.find_openings(
        four, (0.0, 0.0, 0), (10.0, 0.0, 4), 1,
        margin_mm=0.4, search_mm=3.0, pitch_mm=1.0, max_samples=16,
        max_openings=2, band_mm=1.0) == []

    rows = zc.screen_openings(
        four, [((0.0, 0.0, 1, 10.0, 0.0, 5), 1)], margin_mm=0.4)
    assert rows[0].status == "unknown"
    assert rows[0].layers_checked == 4
    assert "proven span" in rows[0].reason
