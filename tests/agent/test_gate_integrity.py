"""The phase gate itself: a green run must mean tests actually executed."""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _load_check_phase():
    path = REPO_ROOT / "tools" / "reliability" / "check_phase.py"
    spec = importlib.util.spec_from_file_location("check_phase_under_test", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["check_phase_under_test"] = module
    spec.loader.exec_module(module)
    return module


check_phase = _load_check_phase()


def _report(*, unit_exit=0, native_exit=0,
            native_summary=f"{check_phase.EXPECTED_NATIVE_TESTS} passed in 5s",
            native_absent=False, unit_load_failure=False, native_load_failure=False,
            unit_counts=None, native_counts=None):
    groups = [{
        "group": "unit",
        "exit_status": unit_exit,
        "summary": "121 passed in 0.15s",
        "counts": unit_counts if unit_counts is not None else {"passed": 121},
        "load_failure": unit_load_failure,
        "load_failure_marker": "ImportError while importing test module" if unit_load_failure else "",
    }]
    if native_absent:
        groups.append({
            "group": "native", "exit_status": None,
            "summary": "skipped: no kicad_rl_router build", "counts": {},
            "load_failure": False, "load_failure_marker": "",
        })
    else:
        groups.append({
            "group": "native", "exit_status": native_exit, "summary": native_summary,
            "counts": (
                native_counts if native_counts is not None
                else {"passed": check_phase.EXPECTED_NATIVE_TESTS}
            ),
            "load_failure": native_load_failure,
            "load_failure_marker": "RouterProvenanceError" if native_load_failure else "",
        })
    return {"groups": groups}


def test_parse_counts_reads_a_pytest_summary_line():
    counts = check_phase.parse_counts("18 passed, 2 skipped, 1 warning in 5.28s")
    assert counts == {"passed": 18, "skipped": 2, "warning": 1}
    assert check_phase.parse_counts("1 failed, 17 passed in 3s") == {"failed": 1, "passed": 17}


def test_load_failure_detection():
    ok, marker = check_phase.has_load_failure("... RouterProvenanceError: nope ...")
    assert ok and marker == "RouterProvenanceError"
    ok, _ = check_phase.has_load_failure("18 passed in 5s")
    assert not ok


def test_default_mode_allows_a_missing_engine_but_strict_does_not():
    report = _report(native_absent=True)
    exit_status, problems = check_phase.evaluate_report(report, strict=False)
    assert exit_status == 0 and problems == []
    exit_status, problems = check_phase.evaluate_report(report, strict=True)
    assert exit_status == 1
    assert any("did not run" in p for p in problems)


def test_strict_mode_requires_the_expected_number_of_native_tests():
    report = _report(native_counts={"passed": 3}, native_summary="3 passed in 1s")
    exit_status, problems = check_phase.evaluate_report(report, strict=True, expected_native=18)
    assert exit_status == 1
    assert any("expected at least 18" in p for p in problems)


def test_strict_mode_rejects_any_native_skip():
    report = _report(native_counts={"passed": 18, "skipped": 18},
                     native_summary="18 passed, 18 skipped in 5s")
    exit_status, problems = check_phase.evaluate_report(report, strict=True)
    assert exit_status == 1
    assert any("skipped" in p for p in problems)


def test_strict_mode_rejects_a_load_or_provenance_failure():
    report = _report(native_load_failure=True, native_exit=1,
                     native_counts={"failed": 1}, native_summary="1 failed in 0.4s")
    exit_status, problems = check_phase.evaluate_report(report, strict=True)
    assert exit_status == 1
    assert any("failed to load" in p for p in problems)
    assert any("exited 1" in p for p in problems)


def test_unit_failure_always_fails_the_gate():
    report = _report(unit_exit=1, unit_counts={"failed": 1})
    exit_status, problems = check_phase.evaluate_report(report, strict=False)
    assert exit_status == 1
    assert any("unit group exited 1" in p for p in problems)


def test_a_complete_run_passes():
    report = _report()
    exit_status, problems = check_phase.evaluate_report(report, strict=True)
    assert exit_status == 0 and problems == []


def test_native_availability_uses_the_build_directory(tmp_path, monkeypatch):
    empty = tmp_path / "empty"
    (empty / "pcbnew" / "python" / "rl").mkdir(parents=True)
    monkeypatch.setenv("PCBWORLD_KICAD_RL_MODULE_DIR", str(empty / "pcbnew" / "python" / "rl"))
    assert check_phase.native_available() is False

    staged = tmp_path / "staged"
    staged.mkdir()
    (staged / "kicad_rl_router.so").write_text("")
    monkeypatch.setenv("PCBWORLD_KICAD_RL_MODULE_DIR", str(staged))
    assert check_phase.native_available() is True


def test_expected_native_count_matches_the_native_suite():
    """A guard so adding native tests without bumping the gate is visible."""
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", *check_phase.NATIVE_TESTS, "--collect-only", "-q",
         "-o", "addopts=", "-p", "no:cacheprovider"],
        cwd=REPO_ROOT, capture_output=True, text=True,
        env={**os.environ, "PYTHONPATH": str(REPO_ROOT)},
    )
    collected = [
        line for line in proc.stdout.splitlines()
        if "::" in line and "test" in line
    ]
    assert len(collected) >= check_phase.EXPECTED_NATIVE_TESTS, (
        "the native suite collects fewer tests than the gate expects: "
        f"{len(collected)} < {check_phase.EXPECTED_NATIVE_TESTS}"
    )
