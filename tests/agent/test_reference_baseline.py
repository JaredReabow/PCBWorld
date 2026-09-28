"""A captured baseline must prove it is *this* reference's, from *this* build.

Replaying a violation multiset without checking where it came from would let a
substituted, more permissive baseline pass a gate whose verdict then names the
reference board's hashes.
"""

from __future__ import annotations

import types

from pcb_world.agent.drc_gate import take_violations
from pcb_world.agent.reference_baseline import (
    POLICY,
    capture_envelope,
    envelope_problems,
)
from tests.agent.fake_engine import FakeViolation


class Engine:
    def __init__(self, violations):
        self._violations = list(violations)

    def run_drc(self, _rules):
        return list(self._violations)

    def get_last_drc_rules_load_error(self):
        return ""

    def get_project_path(self):
        return ""

    def get_pads(self):
        return []

    def get_vias(self):
        return []

    def get_tracks(self):
        return []


def _envelope(tmp_path, *, board_text="board", project_text="{}\n", rules_text="(v1)\n"):
    board = tmp_path / "reference.kicad_pcb"
    project = tmp_path / "reference.kicad_pro"
    rules = tmp_path / "reference.kicad_dru"
    board.write_text(board_text, encoding="utf-8")
    project.write_text(project_text, encoding="utf-8")
    rules.write_text(rules_text, encoding="utf-8")
    violations = take_violations(
        Engine([FakeViolation(error_code=5, error_type="Clearance violation",
                              item_a="a", item_b="b", x_mm=1.0, y_mm=2.0)]), "",
    )
    return capture_envelope(
        violations, board_path=str(board), project_path=str(project),
        rules_path=str(rules),
    )


def _check(envelope, **overrides):
    expected = {
        "policy": POLICY,
        "board_sha256": envelope["board_sha256"],
        "project_sha256": envelope["project_sha256"],
        "rules_sha256": envelope["rules_sha256"],
        "engine": envelope["engine"],
    }
    expected.update(overrides)
    return envelope_problems(envelope, **expected)


def test_a_matching_envelope_is_replayable(tmp_path):
    envelope = _envelope(tmp_path)
    assert envelope["policy"] == POLICY
    assert envelope["board_sha256"] and envelope["engine"] is not None
    assert envelope["baseline"]["violations"]
    assert _check(envelope) == []


def test_a_baseline_from_another_board_is_refused(tmp_path):
    envelope = _envelope(tmp_path)
    problems = _check(envelope, board_sha256="0" * 64)
    assert any("different board" in problem for problem in problems)


def test_a_baseline_from_other_sidecars_is_refused(tmp_path):
    envelope = _envelope(tmp_path)
    for field in ("project_sha256", "rules_sha256"):
        problems = _check(envelope, **{field: "1" * 64})
        assert any(f"different {field.split('_')[0]}" in problem for problem in problems)
    # A reference with no rules file is not the reference this baseline saw.
    assert any("different rules" in problem
               for problem in _check(envelope, rules_sha256=None))


def test_a_baseline_from_a_stale_engine_is_refused(tmp_path):
    envelope = _envelope(tmp_path)
    stale = dict(envelope["engine"])
    stale["cpp_hash"] = "deadbeef"
    problems = _check(envelope, engine=stale)
    assert any("different engine" in problem and "cpp_hash" in problem
               for problem in problems)
    stale = dict(envelope["engine"])
    stale["module_sha256"] = "0" * 64
    assert any("different engine" in problem
               for problem in _check(envelope, engine=stale))
    # An engine that names no build hash is not proof of anything.
    assert any("must both name the router" in problem
               for problem in _check(envelope, engine={"cpp_hash": None}))


def test_a_baseline_without_provenance_is_refused(tmp_path):
    board = tmp_path / "reference.kicad_pcb"
    board.write_text("board", encoding="utf-8")
    violations = take_violations(Engine([]), "")
    envelope = capture_envelope(
        violations, board_path=str(board), project_path=None, rules_path=None,
        provenance=None, provenance_problem="RouterProvenanceError: edited C++",
    )
    assert envelope["engine"] is None
    problems = _check(envelope, engine=envelope["engine"])
    assert any("does not name the engine" in problem for problem in problems)
    assert any("edited C++" in problem for problem in problems)


def test_a_baseline_under_another_policy_or_without_violations_is_refused(tmp_path):
    envelope = _envelope(tmp_path)
    wrong_policy = dict(envelope, policy="uuid-pair-only")
    assert any("measured under policy" in problem for problem in _check(wrong_policy))
    no_violations = dict(envelope)
    no_violations.pop("baseline")
    assert any("no violation multiset" in problem
               for problem in _check(no_violations))
    assert any("no captured baseline envelope" in problem
               for problem in envelope_problems(
                   None, policy=POLICY, board_sha256=None, project_sha256=None,
                   rules_sha256=None, engine=None))
