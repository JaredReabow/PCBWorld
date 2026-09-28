"""Failure-boundary checks for immutable artifact generations."""

from __future__ import annotations

import json

import pytest

from pcb_world.agent.artifacts import ArtifactStore, ExperimentalArtifactStore


def _bound_native(binding):
    """Stand-in for the native gate: reports ok and names what it validated."""
    return {"ok": True, **binding}


def _bound_cli(binding):
    """Stand-in for the complete-CLI gate (see cli_gate.CliVerdict.to_evidence)."""
    return {
        "status": "verified", "report_complete": True,
        "source_board_sha256": binding["reference_board_sha256"],
        "source_project_sha256": binding["reference_project_sha256"],
        "source_rules_sha256": binding["reference_rules_sha256"],
        "candidate_board_sha256": binding["candidate_board_sha256"],
        "candidate_project_sha256": binding["candidate_project_sha256"],
        "candidate_rules_sha256": binding["candidate_rules_sha256"],
    }


def _bound_terminal(binding):
    """Stand-in for the terminal-partition gate (terminals.TerminalDelta)."""
    return {
        "ok": True, "complete": True,
        "original_reference_sha256": binding["reference_board_sha256"],
        "reference_project_sha256": binding["reference_project_sha256"],
        "reference_rules_sha256": binding["reference_rules_sha256"],
        "candidate_board_sha256": binding["candidate_board_sha256"],
        "candidate_project_sha256": binding["candidate_project_sha256"],
        "candidate_rules_sha256": binding["candidate_rules_sha256"],
    }


def test_unpointed_generation_does_not_replace_accepted_generation(tmp_path):
    board = tmp_path / "candidate.kicad_pcb"
    board.write_text("first")
    store = ArtifactStore(str(tmp_path / "run"))
    first_dir, first_manifest = store.stage(str(board), None, None, {"verified": True})
    assert store.current() is None
    store.promote(first_dir, first_manifest)
    pointer_before = (store.root / store.POINTER).read_bytes()

    board.write_text("second")
    second_dir, second_manifest = store.stage(str(board), None, None, {"verified": False})
    # Simulates a crash after generation-ready and before the pointer commit.
    assert (store.root / store.POINTER).read_bytes() == pointer_before
    current, manifest = store.current()
    assert current == first_dir
    assert manifest["board_sha256"] == first_manifest["board_sha256"]
    assert second_manifest["board_sha256"] != first_manifest["board_sha256"]


def test_pointer_refuses_tampered_generation_file(tmp_path):
    board = tmp_path / "candidate.kicad_pcb"
    board.write_text("board")
    store = ArtifactStore(str(tmp_path / "run"))
    folder, manifest = store.stage(str(board), None, None, {})
    store.promote(folder, manifest)
    (tmp_path / "run" / "artifacts" / manifest["generation"] /
     "board.kicad_pcb").write_text("tampered")
    with pytest.raises(ValueError, match="file hash mismatch"):
        store.current()


def test_pointer_and_manifest_disagreement_fails_closed(tmp_path):
    board = tmp_path / "candidate.kicad_pcb"
    board.write_text("board")
    store = ArtifactStore(str(tmp_path / "run"))
    folder, manifest = store.stage(str(board), None, None, {})
    store.promote(folder, manifest)
    path = store.root / store.POINTER
    payload = json.loads(path.read_text())
    payload["manifest"]["verified"] = True
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="pointer and generation manifest disagree"):
        store.current()


def test_experimental_checkpoint_resumes_with_original_reference_binding(tmp_path):
    original = tmp_path / "original.kicad_pcb"
    project = tmp_path / "original.kicad_pro"
    rules = tmp_path / "original.kicad_dru"
    candidate = tmp_path / "repair.kicad_pcb"
    original.write_text("frozen original")
    project.write_text("project")
    rules.write_text("rules")
    candidate.write_text("experimental repair")
    store = ExperimentalArtifactStore(
        str(tmp_path / "run"), str(original), str(project), str(rules),
    )
    folder, manifest = store.stage(
        str(candidate), str(project), str(rules), {"attempts": 4},
        evidence={"local_native": "passed"},
    )
    assert manifest["label"] == "experimental_staging_only"
    assert manifest["accepted"] is False
    reopened = ExperimentalArtifactStore(
        str(tmp_path / "run"), str(original), str(project), str(rules),
    )
    current, current_manifest = reopened.current()
    checkpoint = reopened.last_checkpoint
    assert current == folder
    assert checkpoint == {"attempts": 4}
    assert current_manifest["original_reference_sha256"] == reopened.reference_hash


def test_experimental_local_pass_cannot_promote_against_wrong_reference(tmp_path):
    original = tmp_path / "original.kicad_pcb"
    project = tmp_path / "original.kicad_pro"
    rules = tmp_path / "original.kicad_dru"
    candidate = tmp_path / "repair.kicad_pcb"
    original.write_text("frozen original")
    project.write_text("project")
    rules.write_text("rules")
    candidate.write_text("experimental repair")
    staging = ExperimentalArtifactStore(
        str(tmp_path / "run"), str(original), str(project), str(rules),
    )
    folder, _manifest = staging.stage(str(candidate), str(project), str(rules), {})
    accepted = ArtifactStore(str(tmp_path / "run"))
    # A CLI verdict that names the candidate as its source is not evidence about
    # the frozen reference, so it cannot promote - even with ok booleans.
    evidence = staging.run_final_validation(
        folder,
        native_gate=_bound_native, cli_gate=_bound_cli, terminal_gate=_bound_terminal,
    )
    evidence["cli"]["source_board_sha256"] = staging._hash(candidate)
    with pytest.raises(ValueError, match="CLI gate does not name"):
        staging.promote_after_final_gates(
            accepted, folder, final_evidence=evidence, checkpoint={})
    assert accepted.current() is None


def test_experimental_promotion_requires_native_cli_and_original_terminals(tmp_path):
    original = tmp_path / "original.kicad_pcb"
    candidate = tmp_path / "repair.kicad_pcb"
    original.write_text("original")
    candidate.write_text("repaired")
    staging = ExperimentalArtifactStore(str(tmp_path / "run"), str(original))
    folder, _manifest = staging.stage(str(candidate), None, None, {})
    accepted = ArtifactStore(str(tmp_path / "run"))
    evidence = staging.run_final_validation(
        folder,
        native_gate=_bound_native, cli_gate=_bound_cli, terminal_gate=_bound_terminal,
    )
    evidence["terminals"]["original_reference_sha256"] = "wrong"
    with pytest.raises(ValueError, match="terminal gate does not name"):
        staging.promote_after_final_gates(
            accepted, folder, final_evidence=evidence, checkpoint={})
    assert accepted.current() is None


def test_runner_staging_mode_never_writes_the_accepted_pointer(tmp_path):
    from pcb_world.agent.runner import RoutingRunner
    from tests.agent.test_runner_scripted import _config, _engine

    original = tmp_path / "frozen_original.kicad_pcb"
    original.write_text("immutable reference")
    report = RoutingRunner(_config(
        tmp_path, _engine(), name="staging-run", max_attempts=0,
        experimental_staging=True,
        acceptance_reference_board_path=str(original),
    )).run()
    run_dir = tmp_path / "staging-run"
    assert report.status == "experimental"
    assert report.stop_reason == "experimental_staging_only"
    assert not (run_dir / "artifacts" / ArtifactStore.POINTER).exists()
    staging = ExperimentalArtifactStore(str(run_dir), str(original))
    current = staging.current()
    assert current is not None
    _folder, manifest = current
    assert manifest["accepted"] is False
    assert manifest["label"] == "experimental_staging_only"


def test_stale_evidence_for_another_candidate_cannot_promote(tmp_path):
    """Evidence captured for candidate A must not promote the current candidate B.

    The binding names A's board/project/rules hashes; B's differ, so the store
    recomputes the current hashes and refuses rather than accepting a pass that
    once described a different board.
    """
    original = tmp_path / "original.kicad_pcb"
    original.write_text("frozen original")
    project = tmp_path / "original.kicad_pro"
    rules = tmp_path / "original.kicad_dru"
    project.write_text("{}")
    rules.write_text("(version 1)")
    staging = ExperimentalArtifactStore(str(tmp_path / "run"), str(original),
                                        str(project), str(rules))
    accepted = ArtifactStore(str(tmp_path / "run"))

    candidate_a = tmp_path / "a.kicad_pcb"
    candidate_a.write_text("repair A")
    folder_a, _ = staging.stage(str(candidate_a), str(project), str(rules), {})
    evidence_a = staging.run_final_validation(
        folder_a, native_gate=_bound_native, cli_gate=_bound_cli,
        terminal_gate=_bound_terminal,
    )

    candidate_b = tmp_path / "b.kicad_pcb"
    candidate_b.write_text("repair B")
    folder_b, _ = staging.stage(str(candidate_b), str(project), str(rules), {})

    with pytest.raises(ValueError, match="not bound to the current candidate"):
        staging.promote_after_final_gates(
            accepted, folder_b, final_evidence=evidence_a, checkpoint={})
    assert accepted.current() is None

    # The same evidence still promotes the candidate it was actually produced
    # for: the binding is over the bytes, so the identical repair qualifies.
    folder_a2, _ = staging.stage(str(candidate_a), str(project), str(rules), {})
    promoted_dir, manifest = staging.promote_after_final_gates(
        accepted, folder_a2, final_evidence=evidence_a, checkpoint={})
    assert manifest["accepted"] is True
    assert accepted.current() is not None


def test_final_validation_refuses_gate_evidence_that_does_not_bind(tmp_path):
    original = tmp_path / "original.kicad_pcb"
    original.write_text("frozen original")
    candidate = tmp_path / "repair.kicad_pcb"
    candidate.write_text("repair")
    staging = ExperimentalArtifactStore(str(tmp_path / "run"), str(original))
    folder, _ = staging.stage(str(candidate), None, None, {})

    def unbound_native(_binding):
        return {"ok": True}          # no hashes: cannot be tied to a board

    with pytest.raises(ValueError, match="did not bind its evidence"):
        staging.run_final_validation(
            folder, native_gate=unbound_native, cli_gate=_bound_cli,
            terminal_gate=_bound_terminal,
        )
