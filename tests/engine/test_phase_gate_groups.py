"""The phase gate's own wiring for the patch-reproducibility group.

``check_phase.py`` reports three groups. A non-reproducible engine patch set is
never acceptable, so that group failing is a problem whether or not ``--strict``
was asked for; a *missing* engine checkout is a skip that only ``--strict``
turns into a problem. These tests pin that, because "the gate is green" must not
be able to mean "the group never ran".
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _load_check_phase():
    path = REPO_ROOT / "tools" / "reliability" / "check_phase.py"
    spec = importlib.util.spec_from_file_location("check_phase_engine_groups", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["check_phase_engine_groups"] = module
    spec.loader.exec_module(module)
    return module


check_phase = _load_check_phase()


def _report(patches: dict) -> dict:
    return {"groups": [
        {"group": "unit", "exit_status": 0, "summary": "1 passed in 1s",
         "counts": {"passed": 1}, "load_failure": False, "load_failure_marker": ""},
        {"group": "native", "exit_status": 0,
         "summary": f"{check_phase.EXPECTED_NATIVE_TESTS} passed in 1s",
         "counts": {"passed": check_phase.EXPECTED_NATIVE_TESTS},
         "load_failure": False, "load_failure_marker": ""},
        patches,
    ]}


def test_a_broken_patch_set_is_a_problem_in_every_mode():
    patches = {"group": "patches", "exit_status": 1,
               "summary": "DIFFERS: kicad-patches/rl/pns_rl_router.cpp",
               "counts": {}, "load_failure": False, "load_failure_marker": ""}
    report = _report(patches)
    for strict in (False, True):
        status, problems = check_phase.evaluate_report(report, strict=strict)
        assert status == 1
        assert any("patches group exited 1" in problem for problem in problems)


def test_a_missing_patch_group_only_fails_strict_mode():
    patches = {"group": "patches", "exit_status": None,
               "summary": "skipped: no engine checkout", "counts": {},
               "load_failure": False, "load_failure_marker": ""}
    report = _report(patches)
    status, problems = check_phase.evaluate_report(report, strict=False)
    assert status == 0 and problems == []
    status, problems = check_phase.evaluate_report(report, strict=True)
    assert status == 1
    assert any("patches group did not run" in problem for problem in problems)


def test_the_regression_is_in_the_native_group():
    assert ("tests/engine/test_reporter_cache_canonical_order.py"
            in check_phase.NATIVE_TESTS), (
        "the reporter regression must be wired into the native group, or strict "
        "mode would not run it")
    assert check_phase.PATCH_CHECK == "tools/reliability/check_engine_patches.py"
