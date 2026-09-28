#!/usr/bin/env python3
"""One command that runs the agent-reliability phase checks.

Three groups, reported separately because they prove different things:

* **unit** — validation, snapshots, rule gating, DRC-delta policy and transaction
  bookkeeping against a test double. No engine, no board, always runnable.
* **native** — the same contract against a real ``kicad_rl_router`` build over
  synthetic boards.
* **patches** — ``tools/reliability/check_engine_patches.py``: applying the engine
  patch series to the pinned commit reproduces this checkout's engine tree byte
  for byte. Deterministic and cheap, so a failure is a problem in every mode; a
  missing engine checkout is a skip that only ``--strict`` turns into a problem.

Default mode allows a missing engine: the native group is reported as skipped and
**no acceptance is claimed**. ``--strict`` is the acceptance mode — it requires a
native build, at least ``--expected-native`` native tests to actually run with no
skips, and treats a load/provenance failure as an error rather than a skip. That
is the difference between "the phase is green" and "the phase was executed".

Writes ``docs/agent-work/reliability/evidence/phase_check.json`` with the exact
command, exit status, counts, skip counts and the strict verdict. No network and
no model calls.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
EVIDENCE = REPO_ROOT / "docs" / "agent-work" / "reliability" / "evidence" / "phase_check.json"

UNIT_TESTS = [
    "tests/agent/test_artifact_store.py",
    "tests/agent/test_owned_engine_deadline.py",
    "tests/agent/test_modes_and_validation.py",
    "tests/agent/test_state_and_rules.py",
    "tests/agent/test_transaction_unit.py",
    "tests/agent/test_fault_injection.py",
    "tests/agent/test_drc_classification.py",
    "tests/agent/test_drc_inventory_identity.py",
    "tests/agent/test_evidence_validation.py",
    "tests/agent/test_kicad_metadata.py",
    "tests/agent/test_serialized_metadata.py",
    "tests/agent/test_reference_baseline.py",
    "tests/agent/test_scheduler_unit.py",
    "tests/agent/test_runner_scripted.py",
    "tests/agent/test_failure_taxonomy.py",
    "tests/agent/test_coverage_and_clearance.py",
    "tests/agent/test_free_via_search.py",
    "tests/agent/test_planner_client.py",
    "tests/agent/test_integration_gaps.py",
    "tests/agent/test_phase3_integrity.py",
    "tests/agent/test_cli_gate.py",
    "tests/agent/test_terminals.py",
    "tests/agent/test_final_gates.py",
    "tests/agent/test_tool_api.py",
    "tests/agent/test_gate_integrity.py",
    "tests/agent/test_zone_prefilter_unit.py",
    # The phase gate's own group wiring (patch reproducibility must be required).
    "tests/engine/test_phase_gate_groups.py",
]
NATIVE_TESTS = [
    "tests/agent/test_native_contract.py",
    "tests/agent/test_native_rules.py",
    "tests/agent/test_native_drc_enum.py",
    "tests/agent/test_native_runner.py",
    "tests/agent/test_native_layers.py",
    "tests/agent/test_native_fill.py",
    "tests/agent/test_native_terminals.py",
    "tests/agent/test_native_item_inventory.py",
    "tests/agent/test_native_pour_endpoints.py",
    "tests/agent/test_native_component_graph.py",
    "tests/agent/test_native_via_sizes.py",
    "tests/agent/test_native_drc_incremental.py",
    "tests/agent/test_native_final_gates.py",
    "tests/agent/test_zone_point_query.py",
    # The reporter regressions: the copper-clearance pair-cache contract on both
    # the RL module and the CLI-loaded provider, plus the engine patch
    # reproducibility check. Required coverage - no skip, no waiver.
    "tests/engine/test_reporter_cache_canonical_order.py",
]

#: The patch-reproducibility check, run as its own group.
PATCH_CHECK = "tools/reliability/check_engine_patches.py"

# The native group must execute at least this many tests in strict mode. Bump it
# when native coverage grows; the gate is here so a collection/load failure cannot
# masquerade as a pass.
EXPECTED_NATIVE_TESTS = 144

_COUNT_RE = re.compile(r"(\d+) (passed|failed|skipped|error|errors|deselected|warning|warnings)")
_LOAD_FAILURE_MARKERS = (
    "RouterProvenanceError",
    "ImportError while importing test module",
    "INTERNALERROR",
    "engine server died",
)


def build_dir() -> Path:
    return Path(os.environ.get("PCBWORLD_KICAD_RL_BUILD_DIR", REPO_ROOT / "build_rl"))


def router_lib_dir() -> Path:
    explicit = os.environ.get("PCBWORLD_KICAD_RL_MODULE_DIR")
    if explicit:
        return Path(explicit)
    return build_dir() / "pcbnew" / "python" / "rl"


def native_available() -> bool:
    return bool(glob.glob(str(router_lib_dir() / "kicad_rl_router*")))


def child_env() -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(REPO_ROOT), env.get("PYTHONPATH", "")]
    ).rstrip(os.pathsep)
    env.setdefault("PCBWORLD_KICAD_RL_BUILD_DIR", str(build_dir()))
    frameworks = build_dir() / "kicad" / "KiCad.app" / "Contents" / "Frameworks"
    if frameworks.is_dir():
        existing = env.get("DYLD_LIBRARY_PATH", "")
        if str(frameworks) not in existing.split(":"):
            env["DYLD_LIBRARY_PATH"] = (
                f"{frameworks}:{existing}" if existing else str(frameworks)
            )
    env.setdefault("KICAD_ENGINE_REUSE", "0")
    return env


def parse_counts(output: str) -> dict[str, int]:
    """Counts from pytest's summary line(s) — passed/failed/skipped/errors."""
    counts: dict[str, int] = {}
    for line in output.strip().splitlines():
        if " in " not in line and "==" not in line and "passed" not in line:
            continue
        for number, word in _COUNT_RE.findall(line):
            key = "error" if word.startswith("error") else word.rstrip("s")
            counts[key] = counts.get(key, 0) + int(number)
    return counts


def has_load_failure(output: str) -> tuple[bool, str]:
    for marker in _LOAD_FAILURE_MARKERS:
        if marker in output:
            return True, marker
    return False, ""


def run_group(name: str, files: list[str], env: dict[str, str]) -> dict:
    cmd = [
        sys.executable, "-m", "pytest", *files,
        "-q", "-o", "addopts=", "-p", "no:cacheprovider",
    ]
    started = time.time()
    proc = subprocess.run(cmd, cwd=REPO_ROOT, env=env, capture_output=True, text=True)
    output = proc.stdout + proc.stderr
    load_failed, marker = has_load_failure(output)
    summary_line = ""
    for line in reversed(output.strip().splitlines()):
        if "passed" in line or "failed" in line or "error" in line:
            summary_line = line.strip()
            break
    return {
        "group": name,
        "command": " ".join(cmd),
        "exit_status": proc.returncode,
        "summary": summary_line,
        "counts": parse_counts(output),
        "load_failure": load_failed,
        "load_failure_marker": marker,
        "duration_s": round(time.time() - started, 1),
        "tail": output.strip().splitlines()[-12:],
    }


def run_patch_check(env: dict[str, str]) -> dict:
    """The patch-reproducibility group: one script, exit status and tail."""
    started = time.time()
    script = REPO_ROOT / PATCH_CHECK
    cmd = [sys.executable, str(script)]
    proc = subprocess.run(cmd, cwd=REPO_ROOT, env=env, capture_output=True,
                          text=True)
    output = (proc.stdout + proc.stderr).strip()
    summary_line = output.splitlines()[-1].strip() if output else ""
    load_failed, marker = has_load_failure(output)
    return {
        "group": "patches",
        "command": " ".join(cmd),
        "exit_status": proc.returncode,
        "summary": summary_line,
        "counts": {},
        "load_failure": load_failed,
        "load_failure_marker": marker,
        "duration_s": round(time.time() - started, 1),
        "tail": output.splitlines()[-12:],
    }


def evaluate_report(
    report: dict, *, strict: bool, expected_native: int = EXPECTED_NATIVE_TESTS
) -> tuple[int, list[str]]:
    """Decide the exit status from a finished report. Pure, so it is unit-tested."""
    problems: list[str] = []
    for group in report["groups"]:
        if group["group"] == "unit":
            if group["exit_status"] != 0:
                problems.append(f"unit group exited {group['exit_status']}")
            if group.get("load_failure"):
                problems.append(f"unit group failed to load: {group['load_failure_marker']}")
            continue

        if group["group"] == "patches":
            if group["exit_status"] is None:            # never ran
                if strict:
                    problems.append(
                        "patches group did not run and --strict was requested: "
                        + str(group["summary"]))
                continue
            if group["exit_status"] != 0:
                problems.append(
                    f"patches group exited {group['exit_status']} "
                    "(the engine patch series no longer reproduces this tree)")
            if group.get("load_failure"):
                problems.append(
                    f"patches group failed to load: {group['load_failure_marker']}")
            continue

        # native
        if group["exit_status"] is None:            # never ran
            if strict:
                problems.append(
                    "native group did not run and --strict was requested: " + str(group["summary"])
                )
            continue
        if group["exit_status"] != 0:
            problems.append(f"native group exited {group['exit_status']}")
        if group.get("load_failure"):
            problems.append(f"native group failed to load: {group['load_failure_marker']}")
        counts = group.get("counts", {})
        if counts.get("failed") or counts.get("error"):
            problems.append(
                f"native group reported {counts.get('failed', 0)} failed / "
                f"{counts.get('error', 0)} errors"
            )
        if strict:
            executed = counts.get("passed", 0)
            skipped = counts.get("skipped", 0)
            if skipped:
                problems.append(f"native group skipped {skipped} test(s) in strict mode")
            if executed < expected_native:
                problems.append(
                    f"native group executed {executed} test(s), expected at least "
                    f"{expected_native}"
                )
    return (1 if problems else 0), problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--strict", action="store_true",
        help="acceptance mode: require the native group to execute (no skip allowed)",
    )
    parser.add_argument(
        "--expected-native", type=int, default=EXPECTED_NATIVE_TESTS,
        help=f"minimum native tests that must execute in --strict mode "
             f"(default {EXPECTED_NATIVE_TESTS})",
    )
    parser.add_argument(
        "--evidence", type=Path, default=EVIDENCE,
        help="where to write the JSON evidence file",
    )
    args = parser.parse_args(argv)

    env = child_env()
    available = native_available()
    report: dict = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "python": sys.executable,
        "repo_root": str(REPO_ROOT),
        "build_dir": str(build_dir()),
        "native_engine_available": available,
        "strict": bool(args.strict),
        "expected_native_tests": int(args.expected_native),
        "groups": [],
    }

    unit = run_group("unit", UNIT_TESTS, env)
    report["groups"].append(unit)
    print(f"[unit]   exit {unit['exit_status']}: {unit['summary']}")

    if available:
        native = run_group("native", NATIVE_TESTS, env)
        report["groups"].append(native)
        print(f"[native] exit {native['exit_status']}: {native['summary']}")
        if native.get("load_failure"):
            print(f"[native] LOAD FAILURE: {native['load_failure_marker']}")
    else:
        report["groups"].append({
            "group": "native",
            "command": None,
            "exit_status": None,
            "summary": (
                f"skipped: no kicad_rl_router build in {router_lib_dir()} - "
                "no native acceptance claimed"
            ),
            "counts": {},
            "load_failure": False,
            "load_failure_marker": "",
            "duration_s": 0.0,
            "tail": [],
        })
        print(f"[native] skipped: no router build in {router_lib_dir()}")

    if (REPO_ROOT / "engine" / ".git").exists() or (REPO_ROOT / "engine" / "HEAD").exists():
        patches = run_patch_check(env)
        report["groups"].append(patches)
        print(f"[patches] exit {patches['exit_status']}: {patches['summary']}")
    else:
        report["groups"].append({
            "group": "patches",
            "command": None,
            "exit_status": None,
            "summary": "skipped: no engine checkout to reproduce",
            "counts": {},
            "load_failure": False,
            "load_failure_marker": "",
            "duration_s": 0.0,
            "tail": [],
        })
        print("[patches] skipped: no engine checkout")

    exit_status, problems = evaluate_report(
        report, strict=args.strict, expected_native=args.expected_native
    )
    report["problems"] = problems
    report["exit_status"] = exit_status
    report["native_executed"] = next(
        (g.get("counts", {}).get("passed", 0) for g in report["groups"]
         if g["group"] == "native"), 0,
    )

    args.evidence.parent.mkdir(parents=True, exist_ok=True)
    args.evidence.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"evidence: {args.evidence}")
    for problem in problems:
        print(f"PROBLEM: {problem}")
    return exit_status


if __name__ == "__main__":
    raise SystemExit(main())
