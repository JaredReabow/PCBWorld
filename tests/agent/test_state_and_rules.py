"""Snapshot semantics (session-bound tokens, canonical geometry) and rule proof."""

from __future__ import annotations

import dataclasses
import os

import pytest

from pcb_world.agent import (
    AgentSession,
    InvalidActionError,
    Outcome,
    RuleContext,
    RulesUnavailableError,
    StructuredAction,
    assert_rules_applicable,
    canonical_rows,
    engine_rule_status,
    geometry_digest,
    nets_changed,
    parse_token,
    resolve_rule_context,
    rows_digest,
    state_fingerprint,
    unverifiable_properties,
)
from pcb_world.agent.state import StateProbe, snapshot_token

from tests.agent.fake_engine import FakeEngine, PadInfo, TrackInfo, ViaInfo


def _engine(**kwargs) -> FakeEngine:
    return FakeEngine(
        clusters={"A": {(10.0, 10.0, 1)}, "B": {(40.0, 10.0, 1)}},
        pads=[PadInfo(10.0, 10.0, net_code=1), PadInfo(40.0, 10.0, net_code=1)],
        **kwargs,
    )


def _session(engine, **kwargs) -> AgentSession:
    kwargs.setdefault("board_path", "/tmp/synthetic.kicad_pcb")
    return AgentSession(engine, **kwargs)


# ---------------------------------------------------------------------------
# Snapshots
# ---------------------------------------------------------------------------


def test_snapshot_reports_observed_state_only():
    session = _session(_engine())
    snap = session.snapshot()
    assert snap.outcome is Outcome.OK
    assert snap.route_active is False
    assert snap.head is None and snap.target is None and snap.layer is None
    assert snap.current_net_code is None
    assert snap.track_count == 0 and snap.via_count == 0
    assert snap.allowed_next_actions == ("net_select", "start_route", "connect_targets")
    assert snap.schema_version == "1.0"
    assert snap.session_id == session.session_id
    assert parse_token(snap.token)[0] == session.session_id


def test_snapshot_revision_and_token_move_together():
    session = _session(_engine())
    first = session.snapshot()
    second = session.snapshot()
    assert second.revision == first.revision + 1
    assert second.token != first.token


def test_unreadable_engine_yields_unverified_and_advertises_no_mutation():
    session = _session(_engine(breaking_reads=True))
    snap = session.snapshot()
    assert snap.outcome is Outcome.UNVERIFIED
    assert snap.route_active is None
    assert snap.track_count is None and snap.unrouted_count is None
    assert snap.geometry_digest is None
    assert snap.allowed_next_actions == ()          # unknown state: no mutations
    assert snap.verified is False


def test_token_is_required_by_default():
    engine = _engine()
    session = _session(engine)
    snap = session.act(StructuredAction.start_route(10.0, 10.0, 1))   # no token
    assert snap.outcome is Outcome.INVALID_ACTION
    assert snap.evidence["reason"] == "token_required"
    assert engine.get_tracks() == []
    assert engine.is_routing() is False


def test_token_opt_out_is_explicit_and_documented():
    engine = _engine()
    session = _session(engine, require_tokens=False)
    snap = session.act(StructuredAction.start_route(10.0, 10.0, 1))
    assert snap.outcome is Outcome.OK
    assert session.require_tokens is False


def test_older_token_is_refused_before_mutation():
    engine = _engine()
    session = _session(engine)
    first = session.snapshot()
    second = session.snapshot()          # state moved on
    before = list(engine.get_tracks())
    snap = session.act(StructuredAction.start_route(10.0, 10.0, 1), token=first.token)
    assert snap.outcome is Outcome.STALE_STATE
    assert snap.evidence["token_status"] == "stale"
    assert snap.evidence["current_token"] == second.token
    assert engine.get_tracks() == before


def test_external_geometry_change_with_the_same_count_is_stale():
    """The finding that motivated fingerprint binding: same item count, new copper."""
    engine = _engine()
    engine.state.tracks.append(TrackInfo(10.0, 10.0, 20.0, 10.0, width_mm=0.25))
    session = _session(engine)
    snap = session.snapshot()
    assert snap.track_count == 1

    # Something outside the session rewrites the track without changing the count.
    engine.state.tracks[0] = dataclasses.replace(engine.state.tracks[0], width_mm=0.5)

    refused = session.act(StructuredAction.start_route(10.0, 10.0, 1), token=snap.token)
    assert refused.outcome is Outcome.STALE_STATE
    assert refused.evidence["token_status"] == "stale"
    assert engine.is_routing() is False


def test_token_from_another_session_is_refused():
    engine = _engine()
    other = _session(engine).snapshot()
    session = _session(engine)
    mine = session.snapshot()
    assert other.token != mine.token
    refused = session.act(StructuredAction.start_route(10.0, 10.0, 1), token=other.token)
    assert refused.outcome is Outcome.STALE_STATE
    assert refused.evidence["token_status"] == "foreign"
    assert engine.is_routing() is False


def test_malformed_token_is_refused_as_invalid_not_stale():
    engine = _engine()
    session = _session(engine)
    refused = session.act(StructuredAction.start_route(10.0, 10.0, 1), token="not-a-token")
    assert refused.outcome is Outcome.INVALID_ACTION
    assert refused.evidence["token_status"] == "malformed"


def test_act_accepts_the_current_token_and_reports_success():
    engine = _engine()
    session = _session(engine)
    snap = session.snapshot()
    after = session.act(StructuredAction.start_route(10.0, 10.0, 1), token=snap.token)
    assert after.outcome is Outcome.OK
    assert after.route_active is True
    assert after.head == (10.0, 10.0, 1.0)
    assert after.allowed_next_actions == ("make_line", "make_via", "finish", "net_end")


def test_movement_without_a_route_is_no_active_route():
    engine = _engine()
    session = _session(engine)
    snap = session.snapshot()
    refused = session.act(
        StructuredAction.make_line(40.0, 10.0, "walkaround"), token=snap.token
    )
    assert refused.outcome is Outcome.NO_ACTIVE_ROUTE
    assert refused.evidence["reason"] == "wrong_phase"
    assert engine.get_tracks() == []


def test_failed_dispatch_is_routing_failed_not_ok():
    engine = _engine(fail_fix_at={(40.0, 10.0)})
    session = _session(engine)
    snap = session.snapshot()
    started = session.act(StructuredAction.start_route(10.0, 10.0, 1), token=snap.token)
    refused = session.act(
        StructuredAction.make_line(40.0, 10.0, "walkaround"), token=started.token
    )
    assert refused.outcome in (Outcome.ROUTING_FAILED, Outcome.UNSUPPORTED)
    assert refused.evidence.get("drc_delta", {}).get("acceptable", True) in (True, False)


# ---------------------------------------------------------------------------
# Canonical geometry
# ---------------------------------------------------------------------------


def test_geometry_digest_covers_geometry_not_just_counts():
    base = TrackInfo(0.0, 0.0, 1.0, 0.0, width_mm=0.25, layer=1, net_code=1)
    d0 = geometry_digest([base], [])
    assert geometry_digest([dataclasses.replace(base, width_mm=0.5)], []) != d0
    assert geometry_digest([dataclasses.replace(base, layer=2)], []) != d0
    assert geometry_digest([dataclasses.replace(base, net_code=2)], []) != d0
    assert geometry_digest([base, base], []) != geometry_digest(
        [base, dataclasses.replace(base, x2_mm=2.0)], []
    )


def test_geometry_digest_detects_a_via_layer_span_change():
    """The specific miss root found: same position, diameter and drill, new span."""
    base = ViaInfo(1.0, 1.0, diameter_mm=0.6, drill_mm=0.3, top_layer=1, bottom_layer=2)
    assert geometry_digest([], [base]) != geometry_digest(
        [], [dataclasses.replace(base, bottom_layer=4)]
    )
    assert geometry_digest([], [base]) != geometry_digest(
        [], [dataclasses.replace(base, top_layer=2)]
    )
    assert geometry_digest([], [base]) != geometry_digest(
        [], [dataclasses.replace(base, drill_mm=0.4)]
    )


def test_canonical_rows_are_order_independent_and_nanometre_exact():
    a = TrackInfo(0.0, 0.0, 1.0, 0.0, width_mm=0.25, layer=1, net_code=1)
    b = TrackInfo(5.0, 5.0, 6.0, 5.0, width_mm=0.25, layer=1, net_code=1)
    assert rows_digest(canonical_rows([a, b], [])) == rows_digest(canonical_rows([b, a], []))
    # 1 nm is the quantum: a whole micrometre is visible, and so is a single nm.
    assert geometry_digest([a], []) != geometry_digest(
        [dataclasses.replace(a, x2_mm=1.000001)], []
    )


def test_nets_changed_reports_the_displaced_net():
    a_before = TrackInfo(0.0, 0.0, 1.0, 0.0, layer=1, net_code=1)
    other_before = TrackInfo(2.0, 0.0, 3.0, 0.0, layer=1, net_code=2)
    other_after = TrackInfo(2.0, 0.5, 3.0, 0.5, layer=1, net_code=2)
    before = canonical_rows([a_before, other_before], [])
    after = canonical_rows([a_before, other_after], [])
    assert nets_changed(before, after) == (2,)


def test_unverifiable_properties_are_declared():
    limits = unverifiable_properties()
    assert any("arc" in item for item in limits)
    assert any("locked" in item for item in limits)


def test_state_fingerprint_and_token_shape():
    probe = StateProbe(route_active=False, track_count=0, geometry_digest="abc")
    assert state_fingerprint(probe) != state_fingerprint(
        StateProbe(route_active=True, track_count=0, geometry_digest="abc")
    )
    token = snapshot_token(3, probe, "sess123")
    assert parse_token(token) == ("sess123", 3, state_fingerprint(probe))
    assert parse_token("garbage") is None
    assert parse_token(None) is None


# ---------------------------------------------------------------------------
# Endpoint identity
# ---------------------------------------------------------------------------


def test_endpoint_refuses_a_point_with_no_copper():
    session = _session(_engine())
    with pytest.raises(InvalidActionError) as exc:
        session.endpoint(25.0, 25.0, 1)
    assert exc.value.reason == "endpoint_unknown"
    assert "no copper" in str(exc.value)


def test_endpoint_refuses_an_ambiguous_cluster():
    engine = FakeEngine(
        clusters={"A": {(10.0, 10.0, 1), (11.0, 10.0, 1)}},
        pads=[
            PadInfo(10.0, 10.0, net_code=1),
            PadInfo(11.0, 10.0, net_code=2),
        ],
    )
    session = _session(engine)
    with pytest.raises(InvalidActionError) as exc:
        session.endpoint(10.0, 10.0, 1)
    assert exc.value.reason == "endpoint_ambiguous"


def test_endpoint_refuses_copper_without_a_resolvable_net():
    engine = FakeEngine(clusters={"A": {(10.0, 10.0, 1)}}, pads=[])
    session = _session(engine)
    with pytest.raises(InvalidActionError) as exc:
        session.endpoint(10.0, 10.0, 1)
    assert exc.value.reason == "endpoint_unknown"


def test_endpoint_resolves_the_net_from_the_pad_in_the_cluster():
    session = _session(_engine())
    endpoint = session.endpoint(10.0, 10.0, 1)
    assert endpoint.net_code == 1
    assert endpoint.pads == ("P.1",)
    assert session.net_at(10.0, 10.0, 1) == 1
    assert session.net_at(25.0, 25.0, 1) == 0        # conservative wrapper


# ---------------------------------------------------------------------------
# Rule context
# ---------------------------------------------------------------------------


class _RulesEngine:
    """Minimal backend that answers the routing-rule-context questions."""

    def __init__(
        self, path: str | None, loaded: bool = True, error: str = "",
        project_loaded: bool = True,
    ) -> None:
        self._path = path
        self._loaded = loaded
        self._error = error
        self._project_loaded = project_loaded

    def get_routing_rules_path(self) -> str:
        return self._path or ""

    def was_routing_rules_loaded_from_file(self) -> bool:
        return self._loaded

    def get_last_drc_rules_load_error(self) -> str:
        return self._error

    def was_project_loaded_from_file(self) -> bool:
        return self._project_loaded


def _ctx_with_rules(tmp_path) -> RuleContext:
    board = tmp_path / "board.kicad_pcb"
    board.write_text("(kicad_pcb)")
    ctx = resolve_rule_context(str(board))
    with open(ctx.rules_path, "w", encoding="utf-8") as handle:
        handle.write("(version 1)\n")
    return ctx


def test_resolve_rule_context_maps_board_to_dru_and_pro(tmp_path):
    board = tmp_path / "board.kicad_pcb"
    board.write_text("(kicad_pcb)")
    ctx = resolve_rule_context(str(board))
    assert ctx.rules_path == str(tmp_path / "board.kicad_dru")
    assert ctx.project_path == str(tmp_path / "board.kicad_pro")
    assert ctx.rules_path_exists is False
    ctx2 = resolve_rule_context(
        str(board), rules_path=str(tmp_path / "custom.kicad_dru"),
        project_path=str(tmp_path / "other.kicad_pro"),
    )
    assert ctx2.rules_path == str(tmp_path / "custom.kicad_dru")
    assert ctx2.project_path == str(tmp_path / "other.kicad_pro")


def test_resolve_rule_context_falls_back_to_the_engines_project_path():
    """A session without a board path still gets a context (the old AttributeError)."""
    engine = _RulesEngine(path="")
    engine.get_project_path = lambda: "/tmp/derived/board.kicad_pro"   # type: ignore[method-assign]
    ctx = resolve_rule_context(None, engine=engine)
    assert ctx.rules_path.endswith("board.kicad_dru")
    assert ctx.project_path.endswith("board.kicad_pro")
    assert ctx == resolve_rule_context(None, engine=engine)


def test_engine_rule_status_reports_unanswerable_backends():
    status = engine_rule_status(object())
    assert status["native_validation_blocked"] is True
    assert status["probe_error"]
    assert status["routing_rules_path"] is None


def test_assert_rules_applicable_refuses_an_unanswerable_backend(tmp_path):
    ctx = _ctx_with_rules(tmp_path)
    with pytest.raises(RulesUnavailableError) as exc:
        assert_rules_applicable(object(), ctx)
    assert exc.value.detail["native_validation_blocked"] is True


def test_assert_rules_applicable_refuses_an_unloaded_project(tmp_path):
    ctx = _ctx_with_rules(tmp_path)
    with pytest.raises(RulesUnavailableError) as exc:
        assert_rules_applicable(
            _RulesEngine(path=ctx.rules_path, project_loaded=False), ctx
        )
    assert "not read from disk" in str(exc.value)


def test_assert_rules_applicable_refuses_a_rule_load_error(tmp_path):
    ctx = _ctx_with_rules(tmp_path)
    with pytest.raises(RulesUnavailableError) as exc:
        assert_rules_applicable(
            _RulesEngine(path=ctx.rules_path, error="DRC rules file not found: x"), ctx
        )
    assert "failed to load" in str(exc.value)


def test_assert_rules_applicable_refuses_when_the_engine_ignores_the_file(tmp_path):
    ctx = _ctx_with_rules(tmp_path)
    with pytest.raises(RulesUnavailableError) as exc:
        assert_rules_applicable(_RulesEngine(path="", loaded=False), ctx)
    assert "is not using it" in str(exc.value)


def test_assert_rules_applicable_refuses_a_different_loaded_file(tmp_path):
    ctx = _ctx_with_rules(tmp_path)
    other = tmp_path / "other.kicad_dru"
    other.write_text("(version 1)\n", encoding="utf-8")
    with pytest.raises(RulesUnavailableError) as exc:
        assert_rules_applicable(_RulesEngine(path=str(other)), ctx)
    assert "but the routing engine loaded" in str(exc.value)


def test_assert_rules_applicable_refuses_a_reported_file_that_vanished(tmp_path):
    ctx = _ctx_with_rules(tmp_path)
    with pytest.raises(RulesUnavailableError) as exc:
        assert_rules_applicable(
            _RulesEngine(path=str(tmp_path / "gone.kicad_dru")), ctx
        )
    assert "no longer exists" in str(exc.value)


def test_a_missing_context_can_never_downgrade_a_loaded_file(tmp_path):
    """Root's regression: ctx pointing at 'missing' must not erase real rules."""
    ctx = _ctx_with_rules(tmp_path)
    broken = RuleContext(
        board_path=ctx.board_path, rules_path=str(tmp_path / "missing.kicad_dru"),
        project_path=ctx.project_path, source="explicit",
    )
    with pytest.raises(RulesUnavailableError) as exc:
        assert_rules_applicable(_RulesEngine(path=ctx.rules_path), broken)
    assert "but the routing engine loaded" in str(exc.value)
    assert "drc_rules_path" not in exc.value.detail or exc.value.detail["drc_rules_path"] != ""


def test_assert_rules_applicable_reports_the_loaded_file(tmp_path):
    ctx = _ctx_with_rules(tmp_path)
    evidence = assert_rules_applicable(_RulesEngine(path=ctx.rules_path), ctx)
    assert evidence["drc_rules_path"] == ctx.rules_path
    assert evidence["engine_rules"]["routing_rules_loaded_from_file"] is True


def test_assert_rules_applicable_accepts_project_implicit_rules(tmp_path):
    """No .kicad_dru: the project's own implicit rules are the applicable context."""
    board = tmp_path / "board.kicad_pcb"
    board.write_text("(kicad_pcb)")
    ctx = resolve_rule_context(str(board))
    evidence = assert_rules_applicable(_RulesEngine(path="", loaded=False), ctx)
    assert evidence["drc_rules_path"] == ""
    assert "implicit" in evidence["note"]
    assert os.path.isfile(str(tmp_path / "board.kicad_pcb"))
