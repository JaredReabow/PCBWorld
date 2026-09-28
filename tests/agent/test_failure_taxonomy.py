"""Failure-taxonomy contract: one category per edge, counts that sum, no leaks.

The taxonomy is the artifact phase 9's strategy choice rests on, so the tests pin
the properties that make it trustworthy rather than the exact numbers on one
board:

* every edge gets exactly one category, and the totals sum to the edge count;
* precedence is fixed, so a stale edge cannot be reported as a refusal;
* history is joined only where it is bound to this board, by pair key or by
  component identity, and everything else is counted as dropped;
* a run-level fact (the scan's own cap counters) never masquerades as a
  per-edge claim;
* the public view carries no net name, no coordinate and no refusal position.
"""

from __future__ import annotations

import json

import pytest

from pcb_world.engine.wire import RatsnestEdge
from pcb_world.agent.observations import LayerResolver
from pcb_world.engine.containers import PadInfo
from tests.agent.fake_engine import FakeEngine

from tools.reliability.failure_taxonomy import (
    CATEGORY_CONNECTION_NOT_VERIFIED,
    CATEGORY_COPPER_ABSENT_ANCHOR,
    CATEGORY_DRC_REGRESSION,
    CATEGORY_INSUFFICIENT_COMPONENT_ANCHORS,
    CATEGORY_LAYER_UNRESOLVABLE,
    CATEGORY_MEANING,
    CATEGORY_ORDER,
    CATEGORY_PLANS_EXHAUSTED,
    CATEGORY_UNNAMED_ANCHOR,
    CATEGORY_UNATTEMPTED_CAP_OBSERVED,
    CATEGORY_UNROUTABLE_DEGENERATE,
    CATEGORY_UNATTEMPTED,
    CATEGORY_VIA_NO_CONTINUATION,
    EdgeAttempt,
    EdgeFact,
    ForeignCopperIndex,
    NO_FAILED_STEP_RECORDED,
    build_taxonomy,
    canonical_edge_key,
    classify_edge,
    gap_class,
    join_attempts,
    load_history,
    observe_edges,
    public_summary,
)


def _fact(**overrides) -> EdgeFact:
    base = dict(
        index=0,
        key=(7, 1.0, 2.0, 1, 4.0, 5.0, 1),
        net_code=7,
        net_name="net7",
        start=(1.0, 2.0, 1),
        target=(4.0, 5.0, 1),
        gap_mm=4.2426,
        pair_kind="line",
        pad_groups_on_net=2,
        components_on_net=2,
        component_pairs_on_net=1,
        start_net_at=7,
        target_net_at=7,
        start_cluster_items=2,
        target_cluster_items=2,
        anchor_absent=False,
        already_connected=False,
        component_start="net:7:mem:aaaa",
        component_target="net:7:mem:bbbb",
    )
    base.update(overrides)
    return EdgeFact(**base)


# -- identity --------------------------------------------------------------


def test_canonical_key_is_order_independent():
    forward = canonical_edge_key([7, 1.0, 2.0, 1, 4.0, 5.0, 3])
    reverse = canonical_edge_key([7, 4.0, 5.0, 3, 1.0, 2.0, 1])
    assert forward == reverse
    assert forward[0] == 7


def test_gap_class_buckets_are_ordered():
    assert gap_class(0.0) == "coincident"
    assert gap_class(0.5) == "under_1mm"
    assert gap_class(3.0) == "1_to_5mm"
    assert gap_class(10.0) == "5_to_20mm"
    assert gap_class(50.0) == "over_20mm"


# -- precedence ------------------------------------------------------------


def test_observable_board_facts_outrank_recorded_attempts():
    """A stale edge stays stale even when a plan for it was refused."""
    refused = EdgeAttempt(records=3, closed_before_refusal=True,
                          refused_classes=(("Clearance violation", 4),))
    stale = _fact(already_connected=True)
    assert classify_edge(stale, refused)[0] != CATEGORY_DRC_REGRESSION
    degenerate = _fact(pair_kind="coincident_same_layer")
    assert classify_edge(degenerate, refused)[0] == CATEGORY_UNROUTABLE_DEGENERATE
    unresolved = _fact(layer_error="through-hole anchor spans copper")
    assert classify_edge(unresolved, refused)[0] == CATEGORY_LAYER_UNRESOLVABLE


def test_copper_absent_and_unnamed_anchor_are_different_categories():
    absent = _fact(start_net_at=0, start_cluster_items=0)
    unnamed = _fact(start_net_at=0, start_cluster_items=3)
    assert classify_edge(absent, EdgeAttempt())[0] == CATEGORY_COPPER_ABSENT_ANCHOR
    assert classify_edge(unnamed, EdgeAttempt())[0] == CATEGORY_UNNAMED_ANCHOR


def test_insufficient_component_anchors_needs_a_multi_component_net():
    orphan = _fact(component_start="", component_target="")
    category, detail = classify_edge(orphan, EdgeAttempt())
    assert category == CATEGORY_INSUFFICIENT_COMPONENT_ANCHORS
    assert detail["components_on_net"] == 2
    # A single-component net is not "insufficient": there is nothing to join.
    single = _fact(components_on_net=1, component_pairs_on_net=0,
                   component_start="", component_target="")
    assert classify_edge(single, EdgeAttempt())[0] == CATEGORY_UNATTEMPTED


def test_drc_regression_keeps_the_class_and_geometry():
    attempt = EdgeAttempt(
        records=2,
        closed_before_refusal=True,
        refused_classes=(("Clearance violation", 9),
                         ("Hole clearance violation", 3)),
        refusal_geometry=(("Clearance violation", 12.5, 4.25, 1),),
    )
    category, detail = classify_edge(_fact(), attempt)
    assert category == CATEGORY_DRC_REGRESSION
    assert detail["classes"] == [["Clearance violation", 9],
                                 ["Hole clearance violation", 3]]
    assert detail["geometry"] == [["Clearance violation", 12.5, 4.25, 1]]
    assert detail["closed_before_refusal"] is True


def test_via_no_continuation_requires_both_halves():
    via_plans = EdgeAttempt(
        records=1, via_plans=3, via_checks=40, via_proved=0,
        reasons=(("connection_not_verified", 3),))
    assert classify_edge(_fact(), via_plans)[0] == CATEGORY_VIA_NO_CONTINUATION
    # A via plan alone is not a failure; a run-level search alone is not this
    # pair's. Neither may claim the category.
    assert classify_edge(_fact(), EdgeAttempt(
        records=1, via_plans=3, via_checks=0, via_proved=0,
        reasons=(("connection_not_verified", 1),),
    ))[0] == CATEGORY_CONNECTION_NOT_VERIFIED
    assert classify_edge(_fact(), EdgeAttempt(
        records=1, via_plans=0, via_checks=40, via_proved=0,
        reasons=(("connection_not_verified", 1),),
    ))[0] == CATEGORY_CONNECTION_NOT_VERIFIED


def test_connection_not_verified_and_plans_exhausted_are_distinct():
    not_verified = EdgeAttempt(records=1,
                               reasons=(("connection_not_verified", 1),))
    assert classify_edge(_fact(), not_verified)[0] == CATEGORY_CONNECTION_NOT_VERIFIED
    exhausted = EdgeAttempt(
        records=1, outcomes=(("plans_exhausted", 1),),
        reasons=(("all deterministic plans evaluated and no planner configured", 1),))
    category, detail = classify_edge(_fact(), exhausted)
    assert category == CATEGORY_PLANS_EXHAUSTED
    assert detail["outcomes"] == [["plans_exhausted", 1]]


def test_connection_not_verified_names_where_the_plans_stopped():
    """The category carries the stopping point the run itself recorded.

    ``via_plans`` says what kind of plan was tried; it cannot say whether the
    plan died at its start, on its line, or on the via it needed. The record now
    names the first step that did not succeed, and the category reports those
    counts — including the bucket for records that carry no stopping point at
    all, which is a statement about the evidence and not about the board.
    """
    attempt = EdgeAttempt(
        records=3,
        reasons=(("connection_not_verified", 3),),
        failed_step_kinds=(("line", 1), ("no_failed_step_recorded", 1), ("via", 1)),
    )
    category, detail = classify_edge(_fact(), attempt)
    assert category == CATEGORY_CONNECTION_NOT_VERIFIED
    assert detail["failed_step_kinds"] == [
        ["line", 1], ["no_failed_step_recorded", 1], ["via", 1],
    ]


def test_the_stopping_point_is_aggregated_per_edge_from_the_records():
    """It is read off the record, and a record without one is named, not guessed.

    A checkpoint written before the runner kept the failing step carries no
    ``failed_step_kind``; that record is counted as having no stopping point
    recorded rather than silently folded in with a real one.
    """
    key = canonical_edge_key((7, 1.0, 2.0, 1, 4.0, 5.0, 3))
    attempts, _stats = join_attempts(
        [key], [frozenset()],
        [
            _record(key, plan_key=["walkaround", [[2.0, 2.0, 1]]],
                    failed_step_kind="via", failed_step_index=2),
            _record(key, plan_key=["shove", [[3.0, 2.0, 1]]],
                    failed_step_kind="line", failed_step_index=1),
            _record(key, plan_key=["direct", []]),  # predates the field
        ],
        "digest-a",
    )
    assert attempts[0].records == 3
    assert attempts[0].failed_step_kinds == (
        ("line", 1), ("no_failed_step_recorded", 1), ("via", 1))


def test_unattempted_with_and_without_cap_evidence_are_separated():
    """The cap category is run-level evidence, and its name says so.

    An unattempted edge in a run whose bounded scan withheld pairs is not the
    same claim as "this edge was the one withheld": the run state keeps the
    counter, not the per-net breakdown. The category therefore reports the
    weaker, true statement, and a run that reported no cap leaves the edge
    plainly unattempted.
    """
    capped = EdgeAttempt(cap_evidence=(("skipped_net_cap", 54),))
    assert classify_edge(_fact(), capped)[0] == CATEGORY_UNATTEMPTED_CAP_OBSERVED
    assert CATEGORY_UNATTEMPTED_CAP_OBSERVED == "unattempted_cap_observed"
    meaning = CATEGORY_MEANING[CATEGORY_UNATTEMPTED_CAP_OBSERVED]
    lowered = meaning.lower()
    assert "run-level" in lowered and "does not prove" in lowered
    assert classify_edge(_fact(), EdgeAttempt())[0] == CATEGORY_UNATTEMPTED
    # Zero counts are not evidence: the counter has to have fired.
    zeroed = EdgeAttempt(cap_evidence=(("skipped_net_cap", 0),))
    assert classify_edge(_fact(), zeroed)[0] == CATEGORY_UNATTEMPTED


def test_every_category_is_reachable_and_precedence_is_ordered():
    assert len(CATEGORY_ORDER) == len(set(CATEGORY_ORDER))
    assert CATEGORY_ORDER[0] == "already_connected_stale_edge"
    # Checked separately so a new category cannot be added without a test.
    produced = {
        classify_edge(_fact(already_connected=True), EdgeAttempt())[0],
        classify_edge(_fact(pair_kind="coincident_same_layer"), EdgeAttempt())[0],
        classify_edge(_fact(layer_error="x"), EdgeAttempt())[0],
        classify_edge(_fact(start_net_at=0, start_cluster_items=0),
                      EdgeAttempt())[0],
        classify_edge(_fact(start_net_at=0, start_cluster_items=2),
                      EdgeAttempt())[0],
        classify_edge(_fact(component_start="", component_target=""),
                      EdgeAttempt())[0],
        classify_edge(_fact(), EdgeAttempt(records=1, closed_before_refusal=True))[0],
        classify_edge(_fact(), EdgeAttempt(records=1, via_plans=1, via_checks=1))[0],
        classify_edge(_fact(), EdgeAttempt(
            records=1, reasons=(("connection_not_verified", 1),)))[0],
        classify_edge(_fact(), EdgeAttempt(
            records=1, outcomes=(("plans_exhausted", 1),)))[0],
        classify_edge(_fact(), EdgeAttempt(
            cap_evidence=(("skipped_net_cap", 1),)))[0],
        classify_edge(_fact(), EdgeAttempt())[0],
    }
    assert produced == set(CATEGORY_ORDER)


# -- history join ----------------------------------------------------------


def _record(pair_key, **overrides):
    base = {
        "pair_key": list(pair_key),
        "plan_key": ["walkaround", []],
        "candidate": "direct_walkaround",
        "kind": "direct",
        "outcome": "routing_failed",
        "reason": "connection_not_verified",
        "board_digest": "digest-a",
        "component_start": "",
        "component_target": "",
    }
    base.update(overrides)
    return base


def test_join_matches_by_key_and_by_reversed_key():
    key = (7, 1.0, 2.0, 1, 4.0, 5.0, 3)
    reversed_key = (7, 4.0, 5.0, 3, 1.0, 2.0, 1)
    attempts, stats = join_attempts(
        [canonical_edge_key(key)], [frozenset()],
        [_record(reversed_key)], "digest-a",
    )
    assert attempts[0].attempted
    assert stats["records_used"] == 1


def test_join_matches_substituted_records_by_component_identity():
    attempts, stats = join_attempts(
        [(7, 1.0, 2.0, 1, 4.0, 5.0, 3)],
        [frozenset({"net:7:mem:aaaa", "net:7:mem:bbbb"})],
        [_record((9, 30.0, 30.0, 1, 33.0, 33.0, 2),
                 component_start="net:7:mem:aaaa",
                 component_target="net:7:mem:bbbb",
                 offer_source="substitution", substituted=True)],
        "digest-a",
    )
    assert attempts[0].attempted
    assert attempts[0].substitution_records == 1
    assert stats["substitution_records"] == 1
    assert stats["substitution_records_attributed"] == 1
    assert stats["substitution_records_unattributed"] == 0


def test_join_matches_a_substitution_by_its_offered_edge_key():
    """The exact link beats the membership fallback.

    A substitution's ``pair_key`` is the geometry actually attempted, so it names
    no outstanding edge. ``offered_pair_key`` is the edge the scan drew, and it
    attributes the record even when the component identities would not have: here
    the supplied components belong to no edge, so only the offered key can make
    the record evidence about this connection.
    """
    offered = (7, 1.0, 2.0, 1, 4.0, 5.0, 3)
    attempts, stats = join_attempts(
        [canonical_edge_key(offered)], [frozenset({"net:7:mem:zzzz"})],
        [_record((9, 30.0, 30.0, 1, 33.0, 33.0, 2),
                 offered_pair_key=list(offered),
                 component_start="net:7:mem:aaaa",
                 component_target="net:7:mem:bbbb",
                 offer_source="substitution", substituted=True)],
        "digest-a",
    )
    assert attempts[0].attempted
    assert stats["records_by_offered_key"] == 1
    assert stats["records_unmatched"] == 0
    assert stats["substitution_records_attributed"] == 1


def test_a_direct_attempt_carries_its_own_offered_key():
    """``offered_pair_key == pair_key`` for a direct attempt, and both work."""
    key = (7, 1.0, 2.0, 1, 4.0, 5.0, 3)
    attempts, stats = join_attempts(
        [canonical_edge_key(key)], [frozenset()],
        [_record(key, offered_pair_key=list(key))], "digest-a",
    )
    assert attempts[0].attempted
    assert stats["records_used"] == 1
    # Matched by its own key first: the offered key is the same statement here.
    assert stats["records_by_offered_key"] == 0


def test_a_substitution_from_a_legacy_run_is_counted_not_guessed():
    """A record written before the field existed cannot be attributed.

    Legacy history has no ``offered_pair_key`` and no matching component
    membership, so it stays out of the per-edge counts and is reported as an
    unattributed substitution rather than being assigned to a nearby edge.
    """
    attempts, stats = join_attempts(
        [(7, 1.0, 2.0, 1, 4.0, 5.0, 3)], [frozenset()],
        [_record((9, 30.0, 30.0, 1, 33.0, 33.0, 2),
                 offer_source="substitution", substituted=True)],
        "digest-a",
    )
    assert not attempts[0].attempted
    assert stats["substitution_records"] == 1
    assert stats["substitution_records_bound"] == 1
    assert stats["substitution_records_attributed"] == 0
    assert stats["substitution_records_unattributed"] == 1
    assert stats["records_unmatched"] == 1


def test_join_drops_records_measured_on_other_copper():
    attempts, stats = join_attempts(
        [(7, 1.0, 2.0, 1, 4.0, 5.0, 3)], [frozenset()],
        [_record((7, 1.0, 2.0, 1, 4.0, 5.0, 3), board_digest="digest-b"),
         _record((7, 1.0, 2.0, 1, 4.0, 5.0, 3), board_digest=None)],
        "digest-a",
    )
    assert not attempts[0].attempted
    assert stats["records_other_digest"] == 2
    assert stats["records_used"] == 0


def test_join_counts_unmatched_records_instead_of_hiding_them():
    _attempts, stats = join_attempts(
        [(7, 1.0, 2.0, 1, 4.0, 5.0, 3)], [frozenset()],
        [_record((11, 50.0, 50.0, 1, 51.0, 51.0, 1))], "digest-a",
    )
    assert stats["records_unmatched"] == 1
    assert stats["records_used"] == 0


def test_via_search_is_per_pair_and_run_metadata_is_not_leaked(tmp_path):
    run = tmp_path / "run_state.json"
    pair = [7, 1.0, 2.0, 1, 4.0, 5.0, 3]
    run.write_text(json.dumps({
        "attempts": [_record(pair)],
        "stop_reason": "no_progress",
        "best_board_sha256": "a" * 64,
        "metrics": {
            "transplant": {"digest": "digest-a"},
            "via_search": {json.dumps(pair): {
                "continuation_checks": 12, "continuation_proved": 0}},
            "last_scan": {
                "skipped_net_cap": 4,
                "component_coverage": {"components_omitted_by_window": 2,
                                       "pairs_omitted_by_cap": 3},
            },
        },
    }), encoding="utf-8")
    records, provenance, run_meta = load_history([run])
    assert provenance["runs"][0]["via_search_pairs"] == 1
    assert "via_by_pair" not in provenance["runs"][0]
    attempts, _stats = join_attempts(
        [canonical_edge_key(pair)], [frozenset()], records, "digest-a", run_meta,
    )
    assert attempts[0].via_checks == 12
    assert attempts[0].via_proved == 0
    assert attempts[0].via_search_ran_without_continuation
    assert dict(attempts[0].cap_evidence)["skipped_net_cap"] == 4


def test_unattempted_edge_inherits_only_run_level_cap_evidence():
    """A never-measured edge gets the scan's own counters, never a per-edge claim."""
    run_meta = {"r": {"board_digest": "digest-a",
                      "scan_omissions": {"skipped_net_cap": 7},
                      "via_by_pair": {}, "unanchorable": {}}}
    attempts, _stats = join_attempts(
        [(7, 1.0, 2.0, 1, 4.0, 5.0, 3)], [frozenset()], [], "digest-a", run_meta,
    )
    assert not attempts[0].attempted
    assert dict(attempts[0].cap_evidence)["skipped_net_cap"] == 7
    assert attempts[0].via_checks == 0


def test_run_metadata_from_another_board_is_not_used():
    run_meta = {"r": {"board_digest": "digest-b",
                      "scan_omissions": {"skipped_net_cap": 7},
                      "via_by_pair": {}, "unanchorable": {}}}
    attempts, _stats = join_attempts(
        [(7, 1.0, 2.0, 1, 4.0, 5.0, 3)], [frozenset()], [], "digest-a", run_meta,
    )
    assert not attempts[0].cap_evidence_present


# -- obstacle index --------------------------------------------------------


class _Item:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def _index_engine(tracks=(), vias=(), pads=()):
    engine = _Item(get_tracks=lambda: list(tracks), get_vias=lambda: list(vias),
                   get_pads=lambda: list(pads), copper_layers=2)
    return engine


def test_foreign_copper_index_matches_a_full_scan():
    track = _Item(net_code=9, x1_mm=0.0, y1_mm=0.0, x2_mm=10.0, y2_mm=0.0,
                  layer=0, width_mm=0.25)
    same_net = _Item(net_code=7, x1_mm=0.0, y1_mm=1.0, x2_mm=10.0, y2_mm=1.0,
                     layer=0, width_mm=0.25)
    engine = _index_engine(tracks=[track, same_net])
    resolver = LayerResolver(copper_layers=2, mapping={0: 1, 1: 2})
    index = ForeignCopperIndex(engine, resolver, bucket_mm=2.0)
    assert index.nearest_mm(5.0, 0.4, 7, 2.0) == pytest.approx(0.4)
    # The same-net track is not an obstacle.
    assert index.nearest_mm(5.0, 1.0, 7, 0.1) is None
    # Outside the radius there is nothing to report.
    assert index.nearest_mm(5.0, 9.0, 7, 2.0) is None


# -- observation pass ------------------------------------------------------


def _board_engine():
    def pad(uuid, ref, x, physical):
        return PadInfo(x_mm=x, y_mm=0.0, width_mm=1.0, height_mm=1.0, layer=0,
                       net_code=7, net_name="net7", pad_name="1",
                       footprint_ref=ref, uuid=uuid, physical_id=physical,
                       pad_type="smd", shape="rect")

    pads = [
        pad("p1", "U1", 0.0, "ph1"),
        pad("p2", "U2", 10.0, "ph2"),
    ]
    clusters = {
        "a": {(0.0, 0.0, 1)},
        "b": {(10.0, 0.0, 1)},
    }
    edges = [RatsnestEdge(0.0, 0.0, 10.0, 0.0, 7, 0, 0)]
    return FakeEngine(clusters=clusters, pads=pads, ratsnest=edges)


def test_observe_edges_reports_a_proved_anchor_pair():
    from pcb_world.agent.session import AgentSession

    engine = _board_engine()
    session = AgentSession(engine, board_path="/tmp/fake.kicad_pcb")
    facts, coverage = observe_edges(session, include_obstacles=False)
    assert coverage["edges"] == 1
    fact = facts[0]
    assert fact.net_code == 7
    assert fact.anchor_state == "proved"
    assert fact.pair_kind == "line"
    assert fact.layer_relation == "same_layer"
    assert fact.key == canonical_edge_key(
        (7, 0.0, 0.0, 1, 10.0, 0.0, 1))


def test_build_taxonomy_totals_sum_to_the_edge_count():
    from pcb_world.agent.session import AgentSession

    def factory(_board):
        engine = _board_engine()
        engine.build_connectivity()
        return AgentSession(engine, board_path="/tmp/fake.kicad_pcb")

    report = build_taxonomy("/tmp/fake.kicad_pcb", session_factory=factory,
                            include_obstacles=False)
    assert sum(report["categories"].values()) == report["coverage"]["edges"] == 1
    assert report["board_digest"] is not None


# -- public view -----------------------------------------------------------


def test_public_summary_carries_no_net_name_and_no_coordinates():
    from pcb_world.agent.session import AgentSession

    def factory(_board):
        engine = _board_engine()
        engine.build_connectivity()
        return AgentSession(engine, board_path="/tmp/fake.kicad_pcb")

    report = build_taxonomy("/tmp/fake.kicad_pcb", session_factory=factory,
                            include_obstacles=False)
    private = json.dumps(report)
    assert "net7" in private                      # the private view names the net
    public = json.dumps(public_summary(report))
    assert "net7" not in public
    assert "component_start" not in public
    assert "start_net_at" not in public
    # Buckets, not measurements: no raw coordinate pair survives.
    assert "0.0, 0.0" not in public
    assert "10.0" not in public
    assert "components_by_net" not in public


def test_public_summary_publishes_the_stopping_point_counts():
    """Step kinds are harness vocabulary, so the board-wide profile is public.

    No net, no coordinate and no rule value goes with them; the bucket for a
    record with no stopping point is named for the evidence, not a cause.
    """
    summary = public_summary({
        "taxonomy_version": 1, "board_sha256": "a" * 64, "board_digest": "d",
        "inputs": {}, "coverage": {}, "history": {}, "categories": {},
        "attempted": {}, "anchor_state": {}, "edges": [],
        "failed_step_kinds": {"line": 2, NO_FAILED_STEP_RECORDED: 1},
    })
    assert summary["failed_step_kinds"] == {"line": 2, NO_FAILED_STEP_RECORDED: 1}


def test_public_summary_keeps_the_edge_and_component_pair_counts_apart():
    summary = public_summary({
        "taxonomy_version": 1, "board_sha256": "a" * 64, "board_digest": "d",
        "inputs": {}, "coverage": {"edges": 5, "component_pairs_total": 9},
        "history": {}, "categories": {"unattempted_no_record": 5},
        "attempted": {"False": 5}, "anchor_state": {"proved": 5},
        "edges": [],
    })
    assert summary["coverage"]["edges"] == 5
    assert summary["coverage"]["component_pairs_total"] == 9
