"""The native final gates against a real engine, in real owned child processes.

``test_final_gates.py`` doubles the child process; this file runs it. Each case
stages a generation the way the runner does (geometry digest, progress and the
captured reference baseline travel in the staging manifest) and then asks the
production adapters for their verdicts.
"""

from __future__ import annotations

import re
from pathlib import Path

from pcb_world.agent.artifacts import ExperimentalArtifactStore
from pcb_world.agent.final_gates import (
    FinalGateConfig,
    NativeGateChild,
    capture_reference_baseline,
    production_final_gates,
)
from pcb_world.agent.observations import progress_summary
from pcb_world.agent.scheduler import sha256_file
from pcb_world.agent.session import AgentSession
from tests.agent import synthetic_boards as sb


def _reference_board(board_dir: Path) -> tuple[str, str, str]:
    board = sb.write_board(
        board_dir / "reference.kicad_pcb",
        pads=[sb.Pad("PA1", 10.0, 10.0, 1), sb.Pad("PB1", 40.0, 10.0, 1)],
        segments=[sb.Segment(10.0, 10.0, 40.0, 10.0, 1)],
        nets=(1,),
    )
    rules = sb.write_rules(board_dir / "reference.kicad_dru", 0.2)
    return board, str(Path(board).with_suffix(".kicad_pro")), rules


def _reference_baseline(tmp_path, board, project, rules) -> dict:
    """The reference's baseline envelope, captured by the production child.

    The envelope names the board/project/rules bytes and the engine build, which is
    what the verifying child re-checks before it replays a single identity.
    """
    child = NativeGateChild(FinalGateConfig(work_dir=str(tmp_path / "capture-work")))
    return capture_reference_baseline(
        child, board_path=board, project_path=project, rules_path=rules,
    )


def _candidate_evidence(engine_factory, board, baseline) -> dict:
    """What a staging manifest records for the candidate that was saved."""
    engine = engine_factory(board)
    session = AgentSession(engine, board_path=board)
    return {
        "geometry_digest": session.board_digest(),
        "progress": progress_summary(session),
        "source_drc_baseline": baseline,
    }


def _stage(tmp_path, reference, candidate, project, rules, evidence) -> tuple:
    """A staging store bound to ``reference`` holding ``candidate``."""
    store = ExperimentalArtifactStore(str(tmp_path / "run"), reference, project, rules)
    folder, _manifest = store.stage(candidate, project, rules, {}, evidence=evidence)
    return store, folder


def _gates(store, folder, tmp_path):
    config = FinalGateConfig(
        work_dir=str(tmp_path / "final-gate-work"),
        timeout_s=600.0,
    )
    return production_final_gates(store, folder, config=config)


def test_the_native_gate_reopens_the_saved_generation_in_a_child(
    native_engine_checked, engine_factory, board_dir, tmp_path
):
    board, project, rules = _reference_board(board_dir)
    baseline = _reference_baseline(tmp_path, board, project, rules)
    evidence = _candidate_evidence(engine_factory, board, baseline)
    store, folder = _stage(tmp_path, board, board, project, rules, evidence)
    gates = _gates(store, folder, tmp_path)
    binding = store.generation_hashes(folder)

    verdict = gates["native_gate"](binding)
    assert verdict["ok"] is True, verdict.get("problems") or verdict.get("error")
    assert verdict["child_returncode"] == 0
    assert verdict["mode"] == "verify_generation"
    assert verdict["policy"] == "collision-aware-inventory-v1"
    assert verdict["baseline_policy"] == "collision-aware-inventory-v1"
    # The child measured the same bytes the store binds.
    for field, value in binding.items():
        assert verdict[field] == value
    assert verdict["engine"]["provenance_checked"] is True
    assert verdict["engine"]["cpp_hash"]
    assert verdict["engine"]["module_sha256"]
    assert verdict["drc_delta"]["acceptable"] is True
    assert verdict["baseline"]["relevant"] == verdict["candidate"]["relevant"]
    # The synthetic board gives every item its own UUID, so the child's inventory
    # proves every key it reported - the native inventory path really ran.
    assert verdict["baseline"]["keys_without_inventory_proof"] == 0


def test_the_terminal_gate_compares_both_boards_in_one_child(
    native_engine_checked, engine_factory, board_dir, tmp_path
):
    board, project, rules = _reference_board(board_dir)
    baseline = _reference_baseline(tmp_path, board, project, rules)
    evidence = _candidate_evidence(engine_factory, board, baseline)
    store, folder = _stage(tmp_path, board, board, project, rules, evidence)
    gates = _gates(store, folder, tmp_path)
    binding = store.generation_hashes(folder)

    verdict = gates["terminal_gate"](binding)
    assert verdict["ok"] is True, verdict.get("reasons") or verdict.get("error")
    assert verdict["complete"] is True
    assert verdict["fresh_process"] is True
    assert verdict["candidate_board_sha256"] == binding["candidate_board_sha256"]
    assert verdict["original_reference_sha256"] == binding["reference_board_sha256"]
    # Both pads of the reference are joined by the segment, so the partition has
    # one cluster over two terminals.
    assert verdict["reference"]["partition"]["terminals"] == 2
    assert verdict["reference"]["partition"]["clusters"] == 1
    assert verdict["candidate"]["partition"] == verdict["reference"]["partition"]


def test_the_native_gate_refuses_a_candidate_that_adds_a_violation(
    native_engine_checked, engine_factory, board_dir, tmp_path
):
    """A real added violation is refused with the identity that was added."""
    board, project, rules = _reference_board(board_dir)
    baseline = _reference_baseline(tmp_path, board, project, rules)
    # Two thru-hole pads 0.1 mm apart: the engine reports hole spacing (code 14).
    hostile = sb.write_board(
        board_dir / "hostile.kicad_pcb",
        pads=[sb.Pad("PA1", 10.0, 10.0, 1, kind="thru"),
              sb.Pad("PB1", 40.0, 10.0, 1),
              sb.Pad("PC1", 20.0, 20.0, 2, kind="thru"),
              sb.Pad("PD1", 20.1, 20.0, 2, kind="thru")],
        nets=(1, 2),
    )
    evidence = _candidate_evidence(engine_factory, hostile, baseline)
    store, folder = _stage(tmp_path, board, hostile, project, rules, evidence)
    gates = _gates(store, folder, tmp_path)

    verdict = gates["native_gate"](store.generation_hashes(folder))
    assert verdict["ok"] is False
    assert verdict["child_returncode"] == 1
    assert any("added relevant" in problem for problem in verdict["problems"])
    assert verdict["drc_delta"]["added_relevant_occurrences"] >= 1


def test_the_terminal_gate_refuses_a_candidate_that_splits_a_connection(
    native_engine_checked, engine_factory, board_dir, tmp_path
):
    """Removing the linking copper is a split relation, not a count change."""
    board, project, rules = _reference_board(board_dir)
    baseline = _reference_baseline(tmp_path, board, project, rules)
    text = Path(board).read_text(encoding="utf-8")
    without_segment = re.sub(r"  \(segment .*?\n  \)\n", "", text, count=1, flags=re.S)
    assert without_segment != text
    split = board_dir / "split.kicad_pcb"
    split.write_text(without_segment, encoding="utf-8")
    # The engine refuses a board whose project cannot be loaded, so the split
    # candidate carries the same project as the reference.
    Path(split).with_suffix(".kicad_pro").write_text(
        Path(project).read_text(encoding="utf-8"), encoding="utf-8",
    )
    evidence = _candidate_evidence(engine_factory, str(split), baseline)
    store, folder = _stage(tmp_path, board, str(split), project, rules, evidence)
    gates = _gates(store, folder, tmp_path)

    verdict = gates["terminal_gate"](store.generation_hashes(folder))
    assert verdict["ok"] is False
    assert verdict["complete"] is True
    assert verdict["split_relations"], verdict.get("reasons")
    assert any("no longer connected" in reason for reason in verdict["reasons"])
    assert verdict["candidate"]["partition"]["clusters"] == 2


def test_the_capture_mode_produces_a_bound_envelope(
    native_engine_checked, engine_factory, board_dir, tmp_path
):
    """The production capture path, in an owned child, and its envelope's binding."""
    board, project, rules = _reference_board(board_dir)
    child = NativeGateChild(FinalGateConfig(work_dir=str(tmp_path / "work")))
    envelope = capture_reference_baseline(
        child, board_path=board, project_path=project, rules_path=rules,
    )
    assert envelope["ok"] is True
    assert envelope["policy"] == "collision-aware-inventory-v1"
    assert envelope["board_sha256"] == sha256_file(board)
    assert envelope["project_sha256"] == sha256_file(project)
    assert envelope["rules_sha256"] == sha256_file(rules)
    assert envelope["engine"]["cpp_hash"] and envelope["engine"]["module_sha256"]
    assert envelope["engine"]["provenance_checked"] is True
    assert envelope["baseline"]["violations"] is not None
    assert envelope["rules"]["routing_rules_loaded_from_file"] is True

    # The envelope is exactly what the verify path accepts for this reference.
    evidence = _candidate_evidence(engine_factory, board, envelope)
    store, folder = _stage(tmp_path, board, board, project, rules, evidence)
    gates = _gates(store, folder, tmp_path)
    verdict = gates["native_gate"](store.generation_hashes(folder))
    assert verdict["ok"] is True, verdict.get("problems") or verdict.get("error")
    assert verdict["baseline_binding"]["board_sha256"] == envelope["board_sha256"]


def test_the_native_gate_refuses_a_baseline_from_another_reference(
    native_engine_checked, engine_factory, board_dir, tmp_path
):
    """A substituted baseline cannot ride on the reference's hashes."""
    board, project, rules = _reference_board(board_dir)
    other = sb.write_board(
        board_dir / "other_reference.kicad_pcb",
        pads=[sb.Pad("PA1", 10.0, 10.0, 1), sb.Pad("PB1", 30.0, 10.0, 1)],
        segments=[sb.Segment(10.0, 10.0, 30.0, 10.0, 1)],
        nets=(1,),
    )
    child = NativeGateChild(FinalGateConfig(work_dir=str(tmp_path / "work")))
    other_rules = sb.write_rules(board_dir / "other_reference.kicad_dru", 0.2)
    foreign = capture_reference_baseline(
        child, board_path=other,
        project_path=str(Path(other).with_suffix(".kicad_pro")), rules_path=other_rules,
    )
    evidence = _candidate_evidence(engine_factory, board, foreign)
    store, folder = _stage(tmp_path, board, board, project, rules, evidence)
    gates = _gates(store, folder, tmp_path)

    verdict = gates["native_gate"](store.generation_hashes(folder))
    assert verdict["ok"] is False
    error = str(verdict.get("error") or "")
    assert "not this reference's baseline" in error
    assert "different board" in error


def test_the_native_gate_refuses_a_baseline_from_a_stale_engine(
    native_engine_checked, engine_factory, board_dir, tmp_path
):
    """A baseline measured by another build is not this run's baseline."""
    board, project, rules = _reference_board(board_dir)
    child = NativeGateChild(FinalGateConfig(work_dir=str(tmp_path / "work")))
    envelope = capture_reference_baseline(
        child, board_path=board, project_path=project, rules_path=rules,
    )
    envelope["engine"] = {**envelope["engine"], "cpp_hash": "deadbeef"}
    evidence = _candidate_evidence(engine_factory, board, envelope)
    store, folder = _stage(tmp_path, board, board, project, rules, evidence)
    gates = _gates(store, folder, tmp_path)

    verdict = gates["native_gate"](store.generation_hashes(folder))
    assert verdict["ok"] is False
    error = str(verdict.get("error") or "")
    assert "not this reference's baseline" in error
    assert "different engine" in error and "cpp_hash" in error
