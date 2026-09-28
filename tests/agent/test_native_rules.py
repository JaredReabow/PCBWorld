"""Native coverage for the rule context and native-DRC acceptance.

Two claims are measured here, and they are different:

* **loaded** — the routing-time DRC engine resolved and read the project's
  ``<board>.kicad_dru`` (the engine patch makes this observable);
* **honoured on validation** — the backend's own DRC, given that file, reports a
  violation for copper the file forbids.

The routing *geometry* does not follow the file on this build, which is exactly
why the session gates commits on the DRC verdict instead of on a rule-loading
flag. These tests therefore require the corridor case to be **rejected and
restored**, never accepted through an override.
"""

from __future__ import annotations

import pathlib

from pcb_world.agent import AgentSession, Outcome
from pcb_world.agent.rules import engine_rule_status, resolve_rule_context
from pcb_world.agent.state import canonical_rows
from tests.agent import synthetic_boards as sb

START = (10.0, 15.0, 1)
TARGET = (40.0, 15.0, 1)
RULE_CLEARANCE_MM = 1.0
CORRIDOR_GAP_MM = 1.0


def _corridor_board(directory, stem: str) -> str:
    """NET1 pads either side of a NET2 corridor whose slot is ``CORRIDOR_GAP_MM``."""
    gap = CORRIDOR_GAP_MM
    return sb.write_board(
        pathlib.Path(directory) / f"{stem}.kicad_pcb",
        pads=[sb.Pad("PA1", *START[:2], 1), sb.Pad("PB1", *TARGET[:2], 1)],
        segments=[
            sb.Segment(25.0, 0.5, 25.0, 15.0 - gap / 2.0, 2),
            sb.Segment(25.0, 15.0 + gap / 2.0, 25.0, 29.5, 2),
        ],
    )


def _rows(engine) -> tuple:
    return canonical_rows(engine.get_tracks(), engine.get_vias())


def _connect(session, start, target, mode="walkaround", **kwargs):
    """Run a transaction with the token from a fresh snapshot (the default contract)."""
    return session.connect_targets(start, target, mode, token=session.snapshot().token, **kwargs)


def test_routing_engine_loads_the_projects_rule_file(engine_factory, board_dir):
    board = _corridor_board(board_dir, "rulectx")
    sb.write_rules(pathlib.Path(board).with_suffix(".kicad_dru"), RULE_CLEARANCE_MM)
    engine = engine_factory(board)

    status = engine_rule_status(engine)
    assert status["native_validation_blocked"] is False
    assert status["routing_rules_loaded_from_file"] is True
    assert pathlib.Path(str(status["routing_rules_path"])).name == "rulectx.kicad_dru"
    assert status["last_drc_rules_load_error"] == ""
    assert status["project_loaded_from_file"] is True

    # Without the sibling rule file the engine says so instead of implying rules
    # it never loaded; the context is then the project's implicit rules.
    board2 = _corridor_board(board_dir, "norules")
    engine2 = engine_factory(board2)
    status2 = engine_rule_status(engine2)
    assert status2["routing_rules_path"] == ""
    assert status2["routing_rules_loaded_from_file"] is False


def test_backend_honours_the_custom_rule_when_validating(engine_factory, board_dir):
    """The rule file changes the backend's verdict on *identical, untouched* copper.

    Two parallel tracks on different nets sit 0.8 mm apart: legal under the
    project's 0.2 mm netclass clearance, illegal under a synthetic 1.0 mm rule.
    Nothing is routed here, so the only variable is the rule context.
    """
    board = sb.write_board(
        board_dir / "validate.kicad_pcb",
        pads=[sb.Pad("PA1", 10.0, 10.0, 1), sb.Pad("PB1", 40.0, 10.0, 1)],
        segments=[
            sb.Segment(20.0, 5.0, 30.0, 5.0, 2),
            sb.Segment(20.0, 5.8, 30.0, 5.8, 3),
        ],
        nets=(1, 2, 3),
    )
    rules = sb.write_rules(pathlib.Path(board).with_suffix(".kicad_dru"), RULE_CLEARANCE_MM)
    engine = engine_factory(board)

    default_violations = engine.run_drc("")
    custom_violations = engine.run_drc(rules)
    default_clearance = [
        v for v in default_violations if "clearance" in str(v.error_type).lower()
    ]
    custom_clearance = [
        v for v in custom_violations if "clearance" in str(v.error_type).lower()
    ]

    assert not default_clearance, "the fixture is not legal under the project rules"
    assert custom_clearance, "custom clearance rule produced no violation"
    assert str(RULE_CLEARANCE_MM) in str(custom_clearance[0].message)


def test_custom_rule_corridor_is_rejected_with_verified_restoration(
    engine_factory, board_dir
):
    """The finding's acceptance case: invalid copper must not be kept, ever.

    The router places copper through a corridor the loaded 1.0 mm rule forbids.
    The attempt must come back rejected, the copper must be gone, and the
    pre-existing DRC picture must be identical afterwards.
    """
    board = _corridor_board(board_dir, "corridor")
    rules = sb.write_rules(pathlib.Path(board).with_suffix(".kicad_dru"), RULE_CLEARANCE_MM)
    engine = engine_factory(board)
    session = AgentSession(engine, board_path=board)
    before_rows = _rows(engine)
    before_drc = sorted(
        (v.error_code, v.error_type, round(v.x_mm, 4), round(v.y_mm, 4))
        for v in engine.run_drc(rules)
    )
    before_unrouted = engine.get_unrouted_count()

    result = _connect(session, START, TARGET, "walkaround")

    assert result.outcome is Outcome.ROUTING_FAILED
    assert result.accepted is False
    assert result.committed is False
    assert result.connected is False                      # reported from the restored board
    assert result.evidence["reason"] == "drc_regression"
    assert result.evidence["drc_delta"]["added_relevant"], "no violation was reported"
    assert result.evidence["rollback"]["copper_digest_matches"] is True
    assert result.evidence["rollback"]["session_state_matches"] is True
    assert _rows(engine) == before_rows
    after_drc = sorted(
        (v.error_code, v.error_type, round(v.x_mm, 4), round(v.y_mm, 4))
        for v in engine.run_drc(rules)
    )
    assert after_drc == before_drc
    assert engine.get_unrouted_count() == before_unrouted
    assert result.dirty is False


def test_a_rule_file_that_fails_to_load_refuses_mutations(engine_factory, board_dir):
    """Fail closed when the engine cannot load the rules the board ships."""
    board = _corridor_board(board_dir, "brokenrules")
    pathlib.Path(board).with_suffix(".kicad_dru").write_text(
        "(version 1)\n(this is not a valid rule file\n", encoding="utf-8",
    )
    engine = engine_factory(board)
    status = engine_rule_status(engine)
    assert status["routing_rules_loaded_from_file"] is False
    assert status["last_drc_rules_load_error"], "the load failure must be reported"

    session = AgentSession(engine, board_path=board)
    before = _rows(engine)
    result = _connect(session, START, TARGET, "walkaround")
    assert result.outcome is Outcome.UNSUPPORTED
    assert "failed to load" in str(result.evidence.get("message", ""))
    assert result.committed is False
    assert _rows(engine) == before


def test_requested_missing_rule_file_is_surfaced_and_refuses_further_mutation(
    engine_factory, board_dir
):
    board = _corridor_board(board_dir, "missing")
    engine = engine_factory(board)
    session = AgentSession(engine, board_path=board)
    before = _rows(engine)

    # No .kicad_dru exists, so the applicable context is the project's implicit
    # rules and the connection is attempted normally.
    ok = _connect(session, START, TARGET, "walkaround")
    assert ok.outcome in (Outcome.OK, Outcome.ROUTING_FAILED)

    # Asking DRC for a file that is not there is reported, not silently ignored…
    missing = str(pathlib.Path(board).with_suffix(".kicad_dru"))
    engine.run_drc(missing)
    error = engine.get_last_drc_rules_load_error()
    assert error and "not found" in error

    # …and while that error stands, a mutating call fails closed.
    refused = _connect(session, START, TARGET, "walkaround")
    assert refused.outcome is Outcome.UNSUPPORTED
    assert "failed to load" in str(refused.evidence.get("message", ""))
    assert _rows(engine) == before or refused.committed is False


def test_rule_context_reports_the_engine_state(engine_factory, board_dir):
    board = _corridor_board(board_dir, "context")
    sb.write_rules(pathlib.Path(board).with_suffix(".kicad_dru"), RULE_CLEARANCE_MM)
    engine = engine_factory(board)
    ctx = resolve_rule_context(board)
    assert ctx.rules_path_exists is True
    status = engine_rule_status(engine)
    assert status["project_loaded_from_file"] is True
    session = AgentSession(engine, board_path=board, rule_context=ctx)
    snap = session.snapshot()
    assert snap.outcome is Outcome.OK
