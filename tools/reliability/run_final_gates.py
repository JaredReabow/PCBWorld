#!/usr/bin/env python3
"""Run the three production final gates for one experimental generation.

    run_final_gates.py --run-dir DIR [--generation DIR]
                       [--cli PATH] [--cli-runs N] [--cli-build-root DIR]
                       [--work-dir DIR] [--evidence PATH] [--promote]

The gates are the ones the store's promotion requires: a fresh native reopen and
baseline replay, a fresh two-board native terminal partition, and the complete
CLI report from the vetted reporter - the last bound to the terminal proof the
second gate just produced. Without ``--cli`` the run is native-only and the
evidence is refused rather than promoted, since a native pass is not a CLI
acceptance.

``--cli-build-root`` points the task-isolated build-tree CLI at its own
frameworks (``KICAD_RUN_FROM_BUILD_DIR=1`` plus that directory's
``kicad/KiCad.app/Contents/Frameworks``); the user's installed KiCad settings are
never touched. Nothing is written to the boards, and ``--promote`` only moves the
run's own accepted pointer after every gate has passed.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pcb_world.agent.artifacts import ArtifactStore, ExperimentalArtifactStore  # noqa: E402
from pcb_world.agent.cli_gate import CliGateConfig  # noqa: E402
from pcb_world.agent.final_gates import (  # noqa: E402
    FinalGateConfig,
    production_final_gates,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--run-dir", required=True,
                        help="run directory holding experimental_staging/")
    parser.add_argument("--generation", default=None,
                        help="generation directory (default: the current one)")
    parser.add_argument("--cli", default=None, help="kicad-cli to use for the CLI gate")
    parser.add_argument("--cli-runs", type=int, default=2,
                        help="CLI runs per board; two is the minimum the gate accepts")
    parser.add_argument("--cli-build-root", default=None,
                        help="build tree the CLI lives in (sets its isolated env)")
    parser.add_argument("--timeout-s", type=float, default=3600.0)
    parser.add_argument("--work-dir", default=None)
    parser.add_argument("--evidence", default=None,
                        help="where to write the evidence (default: with the generation)")
    parser.add_argument("--promote", action="store_true",
                        help="move the accepted pointer when every gate passes")
    return parser


def main(argv: list[str]) -> int:
    args = _parser().parse_args(argv)
    run_dir = os.path.abspath(args.run_dir)
    staging_root = os.path.join(run_dir, "experimental_staging")
    frozen = os.path.join(staging_root, "reference")
    if not os.path.isdir(frozen):
        raise SystemExit(f"no experimental staging in {run_dir} (looked for {frozen})")
    store = ExperimentalArtifactStore(
        run_dir,
        os.path.join(frozen, "board.kicad_pcb"),
        os.path.join(frozen, "board.kicad_pro"),
        os.path.join(frozen, "board.kicad_dru"),
    )
    current = store.current()
    if current is None:
        raise SystemExit("experimental staging has no current generation to validate")
    folder = args.generation or current[0]
    if os.path.realpath(folder) != os.path.realpath(current[0]):
        raise SystemExit(
            f"only the current staging generation can be validated "
            f"(current: {current[0]})"
        )
    cli_env = None
    if args.cli_build_root:
        frameworks = os.path.join(
            os.path.abspath(args.cli_build_root),
            "kicad", "KiCad.app", "Contents", "Frameworks",
        )
        cli_env = {"KICAD_RUN_FROM_BUILD_DIR": "1", "DYLD_LIBRARY_PATH": frameworks}
    cli_config = None
    if args.cli:
        cli_config = CliGateConfig(
            cli_path=args.cli, runs=max(2, int(args.cli_runs)), env=cli_env,
            timeout_s=args.timeout_s,
        )
    config = FinalGateConfig(
        repo_root=str(ROOT), timeout_s=args.timeout_s, work_dir=args.work_dir,
        cli=cli_config,
    )
    gates = production_final_gates(store, folder, config=config)
    evidence = store.run_final_validation(
        folder, native_gate=gates["native_gate"],
        terminal_gate=gates["terminal_gate"], cli_gate=gates["cli_gate"],
        raise_on_problems=False,
    )
    evidence_path = args.evidence or os.path.join(folder, "final_gate_evidence.json")
    with open(evidence_path, "w", encoding="utf-8") as handle:
        json.dump(evidence, handle, indent=2, sort_keys=True)
        handle.flush()
        os.fsync(handle.fileno())
    summary = {
        "ok": bool(evidence.get("ok")),
        "problems": list(evidence.get("problems") or []),
        "generation": evidence["generation"],
        "evidence": evidence_path,
        "native_ok": bool(evidence["native"].get("ok")),
        "terminal_ok": bool(evidence["terminals"].get("ok")),
        "terminal_complete": bool(evidence["terminals"].get("complete")),
        "cli_status": evidence["cli"].get("status"),
        "binding": evidence["binding"],
        "promoted": False,
    }
    if args.promote and evidence.get("ok"):
        accepted = ArtifactStore(run_dir)
        folder_out, manifest = store.promote_after_final_gates(
            accepted, folder, final_evidence=evidence,
            checkpoint=dict(store.last_checkpoint or {}),
        )
        summary["promoted"] = True
        summary["accepted_generation"] = os.path.basename(folder_out)
        summary["accepted_board_sha256"] = manifest["board_sha256"]
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if evidence.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
