"""The production final-gate adapters: binding, order, and stale evidence.

The child *process* is doubled here so the tests stay fast; the real child is
exercised in ``test_native_final_gates.py``. What these tests pin is the wiring
the callbacks-only version could not show: the request names every hash it wants
validated, the terminal gate runs before the CLI gate that leans on its proof,
and evidence produced for one generation cannot promote another.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from pcb_world.agent.artifacts import ArtifactStore, ExperimentalArtifactStore
from pcb_world.agent.cli_gate import CliGateConfig
from pcb_world.agent.final_gates import (
    FinalGateConfig,
    GateUnavailableError,
    production_final_gates,
)


class ChildDouble:
    """Stand-in for the owned verifier process, recording every request."""

    def __init__(self):
        self.requests: list[dict] = []

    def __call__(self, command, *, timeout_s):
        request = json.loads(Path(command[-1]).read_text(encoding="utf-8"))
        self.requests.append(request)
        mode = request.get("mode")
        payload = {
            "ok": True,
            "mode": mode,
            "child": "double",
            **{
                field: request.get(f"expected_{field}")
                for field in (
                    "candidate_board_sha256", "candidate_project_sha256",
                    "candidate_rules_sha256", "reference_board_sha256",
                    "reference_project_sha256", "reference_rules_sha256",
                )
            },
        }
        if mode == "terminal_partition":
            payload.update({
                "complete": True, "fresh_process": True, "reasons": [],
                "original_reference_sha256": request.get(
                    "expected_reference_board_sha256"),
            })
        return subprocess.CompletedProcess(
            list(command), 0, stdout=json.dumps(payload) + "\n", stderr="",
        )


def _stage(tmp_path, *, name="run", board_text="board", with_staging_evidence=True):
    reference = tmp_path / "reference.kicad_pcb"
    reference.write_text("frozen reference", encoding="utf-8")
    (tmp_path / "reference.kicad_pro").write_text("{}\n", encoding="utf-8")
    (tmp_path / "reference.kicad_dru").write_text("(version 1)\n", encoding="utf-8")
    store = ExperimentalArtifactStore(
        str(tmp_path / name), str(reference),
        str(tmp_path / "reference.kicad_pro"), str(tmp_path / "reference.kicad_dru"),
    )
    candidate = tmp_path / "candidate.kicad_pcb"
    candidate.write_text(board_text, encoding="utf-8")
    evidence = {}
    if with_staging_evidence:
        evidence = {
            "geometry_digest": "digest-1",
            "progress": {"unrouted": 1},
            "source_drc_baseline": {
                "policy": "collision-aware-inventory-v1",
                "board_sha256": "b" * 64, "project_sha256": "p" * 64,
                "rules_sha256": "r" * 64,
                "engine": {"cpp_hash": "e290ee11", "module_sha256": "m" * 64,
                           "version": "1.4", "provenance_checked": True},
                "baseline": {"policy": "collision-aware-inventory-v1",
                             "violations": []},
            },
        }
    folder, _manifest = store.stage(
        str(candidate), str(tmp_path / "reference.kicad_pro"),
        str(tmp_path / "reference.kicad_dru"), {}, evidence=evidence,
    )
    return store, folder


def _cli_stub(tmp_path) -> str:
    cli = tmp_path / "kicad-cli"
    cli.write_text(
        "#!/bin/sh\n"
        "if [ \"$1\" = \"--version\" ]; then echo 'KiCad 9.0.8'; exit 0; fi\n"
        "for arg in \"$@\"; do prev=\"${prev:-}\"; if [ \"$prev\" = \"--output\" ]; then out=\"$arg\"; fi; prev=\"$arg\"; done\n"
        "printf '%s' '{\"kicad_version\":\"9.0.8\","
        "\"included_severities\":[\"error\",\"warning\",\"exclusion\"],"
        "\"violations\":[],\"unconnected_items\":[]}' > \"$out\"\n",
        encoding="utf-8",
    )
    cli.chmod(0o755)
    return str(cli)


def test_the_adapters_require_the_staging_evidence_they_compare_against(tmp_path):
    store, folder = _stage(tmp_path, with_staging_evidence=False)
    config = FinalGateConfig(work_dir=str(tmp_path / "work"),
                             process_runner=ChildDouble())
    gates = production_final_gates(store, folder, config=config)
    with pytest.raises(GateUnavailableError, match="geometry digest"):
        gates["native_gate"](store.generation_hashes(folder))


def test_the_adapters_only_run_against_the_current_generation(tmp_path):
    store, folder = _stage(tmp_path)
    other = tmp_path / "elsewhere"
    other.mkdir()
    config = FinalGateConfig(work_dir=str(tmp_path / "work"),
                             process_runner=ChildDouble())
    with pytest.raises(GateUnavailableError, match="current staging generation"):
        production_final_gates(store, str(other), config=config)


def test_the_native_request_names_every_hash_and_the_recorded_baseline(tmp_path):
    store, folder = _stage(tmp_path)
    child = ChildDouble()
    config = FinalGateConfig(work_dir=str(tmp_path / "work"), process_runner=child)
    gates = production_final_gates(store, folder, config=config)
    binding = store.generation_hashes(folder)
    evidence = gates["native_gate"](binding)

    request = child.requests[-1]
    assert request["mode"] == "verify_generation"
    assert request["generation_dir"] == folder
    assert request["geometry_digest"] == "digest-1"
    assert request["expected_progress"] == {"unrouted": 1}
    assert request["baseline"]["policy"] == "collision-aware-inventory-v1"
    assert request["baseline"]["baseline"]["violations"] == []
    for field, value in binding.items():
        assert request[f"expected_{field}"] == value
    assert request["reference_board_path"].endswith("reference/board.kicad_pcb")
    assert evidence["ok"] is True


def test_the_terminal_gate_runs_before_the_cli_gate_that_uses_its_proof(tmp_path):
    store, folder = _stage(tmp_path)
    child = ChildDouble()
    config = FinalGateConfig(
        work_dir=str(tmp_path / "work"), process_runner=child,
        cli=CliGateConfig(cli_path=_cli_stub(tmp_path), runs=2),
    )
    gates = production_final_gates(store, folder, config=config)
    evidence = store.run_final_validation(
        folder, native_gate=gates["native_gate"], terminal_gate=gates["terminal_gate"],
        cli_gate=gates["cli_gate"],
    )
    assert [request["mode"] for request in child.requests] == [
        "verify_generation", "terminal_partition",
    ]
    assert evidence["terminals"]["fresh_process"] is True
    assert evidence["terminals"]["ok"] is True
    assert evidence["cli"]["status"] == "verified", evidence["cli"]["reasons"]
    assert evidence["cli"]["terminal_proof_bound"] is True
    # Every gate names both sides of the binding.
    for gate, fields in (
        ("native", ("candidate_board_sha256", "reference_board_sha256")),
        ("terminals", ("candidate_board_sha256", "original_reference_sha256")),
        ("cli", ("candidate_board_sha256", "source_board_sha256")),
    ):
        for field in fields:
            assert evidence[gate][field] is not None

    accepted = ArtifactStore(str(tmp_path / "run"))
    promoted, manifest = store.promote_after_final_gates(
        accepted, folder, final_evidence=evidence, checkpoint={},
    )
    assert manifest["accepted"] is True
    assert accepted.current() is not None


def test_evidence_for_one_generation_cannot_promote_another(tmp_path):
    store, folder_a = _stage(tmp_path)
    child = ChildDouble()
    config = FinalGateConfig(
        work_dir=str(tmp_path / "work"), process_runner=child,
        cli=CliGateConfig(cli_path=_cli_stub(tmp_path), runs=2),
    )
    gates = production_final_gates(store, folder_a, config=config)
    evidence_a = store.run_final_validation(
        folder_a, native_gate=gates["native_gate"], terminal_gate=gates["terminal_gate"],
        cli_gate=gates["cli_gate"],
    )
    candidate_b = tmp_path / "candidate_b.kicad_pcb"
    candidate_b.write_text("a different repair", encoding="utf-8")
    folder_b, _ = store.stage(
        str(candidate_b), str(tmp_path / "reference.kicad_pro"),
        str(tmp_path / "reference.kicad_dru"), {},
        evidence={
            "geometry_digest": "digest-2", "progress": {"unrouted": 2},
            "source_drc_baseline": {"policy": "collision-aware-inventory-v1",
                                    "violations": []},
        },
    )
    accepted = ArtifactStore(str(tmp_path / "run"))
    with pytest.raises(ValueError, match="not bound to the current candidate"):
        store.promote_after_final_gates(
            accepted, folder_b, final_evidence=evidence_a, checkpoint={},
        )
    assert accepted.current() is None


def test_a_native_only_verdict_cannot_promote(tmp_path):
    """No CLI gate means no acceptance: final evidence cannot even be assembled."""
    store, folder = _stage(tmp_path)
    child = ChildDouble()
    config = FinalGateConfig(work_dir=str(tmp_path / "work"), process_runner=child)
    gates = production_final_gates(store, folder, config=config)
    with pytest.raises(ValueError, match="complete CLI gate did not report verified"):
        store.run_final_validation(
            folder, native_gate=gates["native_gate"],
            terminal_gate=gates["terminal_gate"], cli_gate=gates["cli_gate"],
        )
    # Nothing was promoted, so the accepted pointer was never created.
    assert not (tmp_path / "run" / "artifacts" / "accepted_artifact.json").exists()


def test_the_remaining_budget_caps_each_gate(tmp_path):
    store, folder = _stage(tmp_path)
    remaining = {"s": 5.0}
    child = ChildDouble()
    config = FinalGateConfig(
        work_dir=str(tmp_path / "work"), process_runner=child, timeout_s=3600.0,
        remaining_budget_s=lambda: remaining["s"],
    )
    gates = production_final_gates(store, folder, config=config)
    gates["native_gate"](store.generation_hashes(folder))
    assert child.requests[-1]["call_timeout_s"] == 5.0

    remaining["s"] = 0.0
    with pytest.raises(GateUnavailableError, match="no remaining time budget"):
        gates["terminal_gate"](store.generation_hashes(folder))
    assert len(child.requests) == 1               # nothing was spawned


def test_a_child_that_changes_an_input_is_refused(tmp_path):
    """The recorded verdict must describe the bytes the gate actually read."""
    store, folder = _stage(tmp_path)

    class Mutating(ChildDouble):
        def __call__(self, command, *, timeout_s):
            payload = super().__call__(command, timeout_s=timeout_s)
            (Path(folder) / "board.kicad_pcb").write_text("tampered", encoding="utf-8")
            return payload

    config = FinalGateConfig(work_dir=str(tmp_path / "work"), process_runner=Mutating())
    gates = production_final_gates(store, folder, config=config)
    with pytest.raises(GateUnavailableError, match="changed while the gate was running"):
        gates["native_gate"](store.generation_hashes(folder))
