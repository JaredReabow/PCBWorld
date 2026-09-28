"""Native binding of the DRC error codes to the engine this build came from.

The gate classifies violations by their numeric code, and a wrong number silently
excludes a real violation from acceptance — which is exactly the defect that
excluded drilled-hole spacing (14) as "connectivity noise" while treating a
dangling via (12) as gating. These tests pin the mapping to the enum in the
pinned engine source and prove the classification against records the real engine
produced.
"""

from __future__ import annotations

import pytest

from pcb_world.agent import AgentSession, Outcome
from pcb_world.agent.drc_gate import (
    ENUM_EXPECTATIONS,
    is_connectivity_finding,
    parse_drc_enum,
    take_violations,
    violation_key,
)
from tests.agent import synthetic_boards as sb
from tests.agent.conftest import BUILD_DIR

ENGINE_HEADER = BUILD_DIR / "kicad_src" / "pcbnew" / "drc" / "drc_item.h"
START = (10.0, 10.0, 1)
TARGET = (40.0, 10.0, 1)


def test_the_enum_matches_the_engine_source_this_build_came_from():
    """No silent renumbering: the constants are bound to the pinned build source."""
    if not ENGINE_HEADER.is_file():
        pytest.fail(
            f"cannot verify the DRC error codes: the engine source this build was "
            f"compiled from is not present at {ENGINE_HEADER}. Point "
            "PCBWORLD_KICAD_RL_BUILD_DIR at the build tree (it keeps kicad_src/), or "
            "the enum mapping cannot be trusted.",
            pytrace=False,
        )
    values = parse_drc_enum(ENGINE_HEADER.read_text(encoding="utf-8", errors="replace"))
    for name, expected in ENUM_EXPECTATIONS.items():
        assert values.get(name) == expected, (
            f"{name}: this package says {expected}, the engine enum says "
            f"{values.get(name)} — the gate would mis-classify violations"
        )


def test_a_real_hole_spacing_violation_is_relevant(engine_factory, board_dir):
    """Two thru-hole drills 0.1 mm apart: the engine reports code 14."""
    board = sb.write_board(
        board_dir / "hole_spacing.kicad_pcb",
        pads=[sb.Pad("PA1", 10.0, 10.0, 1, kind="thru"),
              sb.Pad("PB1", 10.5, 10.0, 2, kind="thru")],
        nets=(1, 2),
    )
    engine = engine_factory(board)
    violations = engine.run_drc("")
    hole_spacing = [v for v in violations if int(v.error_code) == 14]

    assert hole_spacing, "the engine no longer reports a hole-spacing violation here"
    record = hole_spacing[0]
    assert "hole" in str(record.error_type).lower()
    # The regression: this must NOT be treated as a connectivity signal.
    assert is_connectivity_finding(record) is False
    violation_set = take_violations(engine, "")
    assert violation_key(record) in violation_set.keys
    assert violation_set.relevant >= 1


def test_a_real_dangling_via_is_a_connectivity_signal(engine_factory, board_dir):
    board = sb.write_board(
        board_dir / "dangling_via.kicad_pcb",
        pads=[sb.Pad("PA1", 10.0, 10.0, 1), sb.Pad("PB1", 40.0, 10.0, 1)],
        vias=[sb.Via(20.0, 10.0, 1)],
    )
    engine = engine_factory(board)
    violations = engine.run_drc("")
    dangling = [v for v in violations if int(v.error_code) == 12]

    assert dangling, "the engine no longer reports a dangling-via violation here"
    record = dangling[0]
    assert is_connectivity_finding(record) is True
    assert int(record.error_code) == 12          # the corrected mapping


class _HoleSpacingInjector:
    """A real engine whose second DRC run reports a hole-spacing violation.

    The record is produced by the engine itself (copied from a real run on the
    too-close-drills board), so the classification and rejection paths are
    exercised with an authentic code/message pair rather than a fabricated one.
    """

    def __init__(self, inner, record) -> None:
        self._inner = inner
        self._record = record
        self._runs = 0

    def run_drc(self, rules_path: str = ""):
        self._runs += 1
        violations = list(self._inner.run_drc(rules_path))
        if self._runs >= 2:
            violations.append(self._record)
        return violations

    def __getattr__(self, name):
        return getattr(self._inner, name)


def test_the_gate_rejects_a_new_hole_spacing_violation(engine_factory, board_dir):
    """The end-to-end consequence of the enum fix: a new code-14 rejects the route."""
    source_board = sb.write_board(
        board_dir / "record_source.kicad_pcb",
        pads=[sb.Pad("PA1", 10.0, 10.0, 1, kind="thru"),
              sb.Pad("PB1", 10.5, 10.0, 2, kind="thru")],
        nets=(1, 2),
    )
    source_engine = engine_factory(source_board)
    record = next(
        (v for v in source_engine.run_drc("") if int(v.error_code) == 14), None
    )
    assert record is not None

    board = sb.direct_board(board_dir / "hole_reject.kicad_pcb")
    engine = engine_factory(board)
    session = AgentSession(engine, board_path=board)
    snapshot = session.snapshot()
    injecting = _HoleSpacingInjector(engine, record)
    session._engine = injecting
    session._gate = type(session._gate)(session)

    result = session.connect_targets(START, TARGET, "walkaround", token=snapshot.token)

    assert result.outcome is Outcome.ROUTING_FAILED
    assert result.accepted is False
    assert result.committed is False
    assert result.evidence["reason"] == "drc_regression"
    added = result.evidence["drc_delta"]["added_relevant"]
    assert any(int(item["error_code"]) == 14 for item in added)
    assert result.evidence["rollback"]["copper_digest_matches"] is True
    assert engine.get_track_count() == 0
