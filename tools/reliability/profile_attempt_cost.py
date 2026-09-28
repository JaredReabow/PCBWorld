#!/usr/bin/env python3
"""Where one routing attempt's wall time actually goes.

The bounded V3 pass spent ~130 s per offered pair and the existing attempt
telemetry could not say why: probe-only records deliberately carry
``duration_s = 0.0`` and the engine calls underneath them were never timed. This
script runs the real :class:`RoutingRunner` against a real board with a timing
proxy around the engine, so the breakdown comes out of a genuine run rather
than a re-implementation of one.

    profile_attempt_cost.py --board B --project P --rules R --run-dir D \
        [--attempts N] [--time-limit S] [--out report.json]

Nothing is promoted: the run directory is a scratch directory and the script
refuses to touch an existing accepted artifact (``--run-dir`` must not exist or
must be empty). Read-only w.r.t. its inputs.
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import sys
import time
import traceback
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

os.environ.setdefault("KICAD_ENGINE_REUSE", "0")

from pcb_world.agent.runner import RunnerConfig, RoutingRunner  # noqa: E402


# Engine calls grouped by the question they answer. ``run_drc`` is the full
# native acceptance pass; ``build_connectivity`` rebuilds the connectivity
# graph; ``checkpoint``/``restore`` are the snapshot machinery a probe pays
# twice per candidate; ``get_board_items`` and friends are inventory reads that
# look cheap individually and expensive in aggregate.
CALL_GROUPS: dict[str, str] = {
    "run_drc": "drc_full",
    "run_drc_incremental": "drc_incremental",
    "clear_drc_cache": "drc_other",
    "get_drc_result": "drc_other",
    "build_connectivity": "connectivity",
    "checkpoint": "checkpoint",
    "restore": "checkpoint",
    "restore_incremental": "checkpoint",
    "release_checkpoint": "checkpoint",
    "has_checkpoint": "checkpoint",
    "get_board_snapshot": "probe",
    "get_points": "probe",
    "get_connected_points": "probe",
    "get_ratsnest": "probe",
    "get_unrouted_count": "probe",
    "get_pad_groups": "probe",
    "get_net_names": "probe",
    "get_board_items": "inventory",
    "get_tracks": "inventory",
    "get_vias": "inventory",
    "get_pads": "inventory",
    "get_footprints": "inventory",
    "get_keepouts": "inventory",
    "fill_zones": "zones",
    "save": "save",
    "start_route": "pns",
    "move": "pns",
    "fix_route": "pns",
    "cancel_route": "pns",
    "finish": "pns",
    "switch_layer": "pns",
    "toggle_via": "pns",
    "flip_posture": "pns",
    "undo_last_segment": "pns",
    "start_drag": "pns",
    "fix_drag": "pns",
    "cancel_drag": "pns",
}

TIMED_PREFIXES = ("run_drc", "get_", "build_", "checkpoint", "restore", "release_",
                  "has_", "fill_", "save", "start_", "move", "fix_", "cancel_",
                  "switch_", "toggle_", "flip_", "undo_", "clear_", "rewind_",
                  "delete_", "lock_", "cleanup_", "set_")


class TimingProxy:
    """Transparent engine proxy that accumulates per-call wall time."""

    def __init__(self, engine: Any) -> None:
        self._engine = engine
        self.calls: collections.Counter = collections.Counter()
        self.seconds: collections.Counter = collections.Counter()
        self.by_group: collections.Counter = collections.Counter()
        self.slowest: list[tuple[float, str]] = []
        self.drc_callers: collections.Counter = collections.Counter()

    @staticmethod
    def _caller() -> str:
        """The first repo frame outside this profiler: which path asked for a DRC."""
        for frame in reversed(traceback.extract_stack()[:-2]):
            name = frame.filename
            if "profile_attempt_cost" in name or "router_client" in name:
                continue
            if any(part in name for part in ("/pcb_world/", "/tools/reliability/")):
                return f"{os.path.basename(name)}:{frame.lineno}:{frame.name}"
        return "unknown"

    def __getattr__(self, name: str) -> Any:
        attribute = getattr(self._engine, name)
        if not callable(attribute) or not name.startswith(TIMED_PREFIXES):
            return attribute
        group = CALL_GROUPS.get(name, "other")

        def timed(*args: Any, **kwargs: Any) -> Any:
            started = time.perf_counter()
            try:
                return attribute(*args, **kwargs)
            finally:
                elapsed = time.perf_counter() - started
                self.calls[name] += 1
                self.seconds[name] += elapsed
                self.by_group[group] += elapsed
                self.slowest.append((elapsed, name))
                if name in ("run_drc", "run_drc_incremental"):
                    self.drc_callers[self._caller()] += 1
                if len(self.slowest) > 4000:
                    self.slowest.sort(reverse=True)
                    del self.slowest[2000:]

        return timed

    def report(self) -> dict[str, Any]:
        return {
            "calls": dict(self.calls.most_common()),
            "seconds_by_call": {k: round(v, 3)
                                for k, v in self.seconds.most_common()},
            "seconds_by_group": {k: round(v, 3)
                                 for k, v in self.by_group.most_common()},
            "slowest_calls": [
                {"call": name, "seconds": round(seconds, 3)}
                for seconds, name in sorted(self.slowest, reverse=True)[:15]
            ],
            "drc_callers": dict(self.drc_callers.most_common()),
        }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--board", required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--rules", required=True)
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--attempts", type=int, default=6)
    parser.add_argument("--time-limit", type=float, default=1800.0)
    parser.add_argument("--candidate-limit", type=int, default=6)
    parser.add_argument(
        "--incremental-drc", action="store_true",
        help="enable the opt-in scoped native DRC pass for the acceptance delta",
    )
    parser.add_argument("--out", default=None)
    return parser


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv[1:])
    run_dir = Path(args.run_dir)
    if run_dir.exists() and any(run_dir.iterdir()):
        raise SystemExit(f"refusing to profile into a non-empty run dir: {run_dir}")
    run_dir.mkdir(parents=True, exist_ok=True)

    proxies: list[TimingProxy] = []

    def engine_factory(board_path: str) -> TimingProxy:
        from pcb_world.engine import KiCadEngine

        proxy = TimingProxy(KiCadEngine(board_path, project_path=args.project))
        proxies.append(proxy)
        return proxy

    attempts: list[dict[str, Any]] = []

    def scratch_terminal(folder: str, request: dict[str, Any]) -> dict[str, Any]:
        """Profiling-only stand-in for the fresh two-board terminal gate."""
        return {
            "ok": True,
            "complete": True,
            "test_double": True,
            "profile_only": True,
            "reasons": [],
        }

    def scratch_verifier(folder: str, request: dict[str, Any]) -> dict[str, Any]:
        """Profiling-only stand-in for the fresh-process acceptance gates.

        The profiler measures engine cost, so it must not pay for two extra
        native child processes per promoted artifact. This verdict is never
        evidence of acceptance and is recorded as ``test_double``; the scratch
        run directory is not an accepted artifact store.
        """
        return {
            "ok": True,
            "reopened": False,
            "test_double": True,
            "profile_only": True,
            "progress": request["expected_progress"],
            "terminal_partition": scratch_terminal(folder, request),
        }

    def on_progress(payload: dict[str, Any]) -> None:
        attempts.append(dict(payload))
        print(
            f"  attempt {payload.get('attempts'):>3} "
            f"accepted={payload.get('accepted')} "
            f"elapsed={payload.get('elapsed_s')}s "
            f"progress={payload.get('progress')}",
            flush=True,
        )

    config = RunnerConfig(
        board_path=args.board,
        run_dir=str(run_dir),
        project_path=args.project,
        rules_path=args.rules,
        max_attempts=args.attempts,
        max_model_requests=0,
        time_limit_s=args.time_limit,
        candidate_limit=args.candidate_limit,
        max_per_net=1,
        per_net_tries=3,
        verify_artifacts=False,
        incremental_drc=bool(args.incremental_drc),
        terminal_verifier=scratch_terminal,
        artifact_verifier=scratch_verifier,
        engine_factory=engine_factory,
        progress_callback=on_progress,
    )
    runner = RoutingRunner(config)
    started = time.perf_counter()
    report = runner.run()
    wall = time.perf_counter() - started

    evidence_path = run_dir / "routing_evidence.json"
    evidence: dict[str, Any] = {}
    if evidence_path.is_file():
        evidence = json.loads(evidence_path.read_text())

    pair_times: dict[str, list[float]] = collections.defaultdict(list)
    for record in evidence.get("attempt_outcomes_by_attempt", []) or []:
        pair_times[str(record.get("pair_key"))].append(float(record.get("duration_s") or 0.0))

    engine_report = {
        "proxies": [proxy.report() for proxy in proxies],
        "merged_calls": dict(collections.Counter(
            {k: v for proxy in proxies for k, v in proxy.calls.items()}
        ).most_common()),
        "merged_seconds_by_group": dict(collections.Counter(
            {k: v for proxy in proxies for k, v in proxy.by_group.items()}
        ).most_common()),
    }
    output = {
        "board": args.board,
        "project": args.project,
        "rules": args.rules,
        "attempts_requested": args.attempts,
        "wall_seconds": round(wall, 2),
        "runner_status": report.status,
        "runner_stop_reason": report.stop_reason,
        "runner_timings": report.timings,
        "engine": engine_report,
        "attempt_records": len(evidence.get("attempts", []) or []),
        "plan_evaluations": evidence.get("plan_evaluations"),
        "pairs_attempted": evidence.get("pairs_attempted"),
        "attempt_outcomes": evidence.get("attempt_outcomes"),
        "accepted_closures": evidence.get("accepted_closures"),
        "progress_before": evidence.get("progress_before"),
        "progress_after": evidence.get("progress_after"),
        "progress_timeline": (evidence.get("progress") or {}).get("timeline")
        if isinstance(evidence.get("progress"), dict) else None,
        "callback_samples": attempts,
    }
    out_path = Path(args.out) if args.out else run_dir / "profile_report.json"
    out_path.write_text(json.dumps(output, indent=2, sort_keys=True))

    print(f"\nwall {wall:.1f}s  status={report.status} stop={report.stop_reason}")
    print(f"plan evaluations: {evidence.get('plan_evaluations')}  "
          f"accepted: {evidence.get('accepted_closures')}")
    print("engine seconds by group:")
    for group, seconds in engine_report["merged_seconds_by_group"].items():
        print(f"  {group:<16} {seconds:>9.2f}s")
    print("top engine calls:")
    for name, seconds in sorted(
        engine_report["proxies"][0]["seconds_by_call"].items(),
        key=lambda item: -item[1],
    )[:15] if engine_report["proxies"] else []:
        print(f"  {name:<26} {seconds:>9.2f}s "
              f"x{engine_report['proxies'][0]['calls'].get(name)}")
    print(f"report: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
