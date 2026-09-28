#!/usr/bin/env python3
"""CLI for the resumable routing agent (phase 2).

Deterministic-first: candidate plans are generated and ranked in code, and the
planner (if configured) is asked only for connections the deterministic set could
not close. Every mutating request goes through the accepted phase-1 safety API
(token, schema, endpoint identity, proven rule context, native DRC acceptance,
verified rollback).

Examples
--------

Offline / CI (no network, no model)::

    python tools/reliability/route_agent.py --board b.kicad_pcb --run-dir run \\
        --provider scripted --max-attempts 20

DeepSeek planning, bounded (task-local key file or DEEPSEEK_API_KEY)::

    python tools/reliability/route_agent.py --board b.kicad_pcb --run-dir run \\
        --provider deepseek --model deepseek-flash \\
        --max-model-requests 20 --max-attempts 200 --time-limit 1800

Resume a stopped run (provenance is verified against the current inputs)::

    python tools/reliability/route_agent.py --board b.kicad_pcb --run-dir run \\
        --provider scripted --resume run/run_state.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pcb_world.agent.runner import (          # noqa: E402
    OpenAICompatiblePlanner,
    RunnerConfig,
    RoutingRunner,
    ScriptedPlanner,
    resolve_inputs,
)
from pcb_world.agent.scheduler import (        # noqa: E402
    ProvenanceMismatchError,
    RunState,
    build_provenance,
)


DEFAULT_DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEFAULT_DEEPSEEK_MODEL = "deepseek-flash"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--board", required=True, help="input .kicad_pcb to route")
    parser.add_argument("--project", default=None, help="explicit .kicad_pro")
    parser.add_argument("--rules", default=None, help="explicit .kicad_dru")
    parser.add_argument("--run-dir", required=True, help="output/checkpoint directory")

    parser.add_argument(
        "--provider",
        default="scripted",
        choices=["scripted", "deepseek", "openai-compatible", "repo-api", "repo-remote"],
        help="planner backend; 'scripted' needs no network and no model",
    )
    parser.add_argument("--model", default=None, help="planner model id")
    parser.add_argument("--base-url", default=None, help="OpenAI-compatible base URL")
    parser.add_argument(
        "--allow-host", default=None,
        help="additionally allow this host for --provider openai-compatible",
    )
    parser.add_argument("--chat-path", default="/chat/completions")
    parser.add_argument(
        "--api-key-file", default=None,
        help="file holding the API key (default: the existing task-local key file, "
             "else DEEPSEEK_API_KEY); never logged or copied",
    )
    parser.add_argument("--api-provider", default="openai",
                        help="for --provider repo-api: openai|anthropic|google|together")
    parser.add_argument("--remote-url", default=None,
                        help="for --provider repo-remote: OpenAI-shaped server URL")
    parser.add_argument("--temperature", type=float, default=0.0)
    # Reasoning models spend part of this budget on hidden reasoning before the
    # JSON answer. 1600 measured as an empty answer on the V3 pilot (all of the
    # allowance went to reasoning); 8000 produced the pilot's only usable plan,
    # and the planner escalates once from here on a truncation.
    parser.add_argument("--max-new-tokens", type=int, default=8000)
    parser.add_argument("--planner-timeout", type=float, default=60.0)
    parser.add_argument("--planner-retries", type=int, default=3)

    parser.add_argument("--max-attempts", type=int, default=40)
    parser.add_argument("--max-model-requests", type=int, default=0,
                        help="0 = deterministic only (no planner calls)")
    parser.add_argument("--max-total-tokens", type=int, default=0, help="0 = unlimited")
    parser.add_argument("--time-limit", type=float, default=900.0, help="seconds")
    parser.add_argument("--engine-call-timeout", type=float, default=300.0,
                        help="hard deadline per owned KiCad native operation (seconds)")
    parser.add_argument("--max-pairs", type=int, default=None)
    parser.add_argument("--max-per-net", type=int, default=1)
    parser.add_argument("--per-net-tries", type=int, default=3)
    parser.add_argument("--candidate-limit", type=int, default=6)
    parser.add_argument("--stall-patience", type=int, default=6)
    parser.add_argument("--observe-radius", type=float, default=3.0)
    parser.add_argument("--obstacle-limit", type=int, default=8)

    parser.add_argument("--resume", default=None, help="path to run_state.json")
    parser.add_argument("--record-prompts", action="store_true",
                        help="write planner observations to <run-dir>/observations.jsonl")
    parser.add_argument("--quiet", action="store_true")
    return parser


def rebind_resume_run_dir(
    state: RunState, resume_path: str, run_dir: str
) -> dict[str, Any] | None:
    """Point a resumed checkpoint at the directory its state file actually lives in.

    A checkpoint records the run directory it was written in. Copying that
    directory is the documented way to branch a run, and after a copy the state
    file lives somewhere else - so the recorded value is rebound to the file's
    own directory. Any *other* ``--run-dir`` is still refused, because resuming
    there would write the state file and the board artefacts to different places.

    Returns an error payload to print, or ``None`` when the resume may proceed.
    """
    checkpoint_dir = os.path.dirname(os.path.abspath(resume_path))
    if os.path.abspath(state.run_dir) != checkpoint_dir:
        state.run_dir = checkpoint_dir
    if os.path.abspath(state.run_dir) != os.path.abspath(run_dir):
        return {
            "error": "resume_run_dir_mismatch",
            "reason": "the checkpoint belongs to another run directory",
            "checkpoint_run_dir": state.run_dir,
            "requested_run_dir": os.path.abspath(run_dir),
        }
    return None


def build_planner(args: argparse.Namespace):
    """Build the requested planner backend (never called for ``scripted`` alone)."""
    if args.provider == "scripted":
        return ScriptedPlanner()
    if args.provider == "deepseek":
        return OpenAICompatiblePlanner(
            base_url=args.base_url or DEFAULT_DEEPSEEK_BASE_URL,
            model=args.model or DEFAULT_DEEPSEEK_MODEL,
            api_key_file=args.api_key_file,
            timeout_s=args.planner_timeout,
            max_retries=args.planner_retries,
            temperature=args.temperature,
            max_tokens=args.max_new_tokens,
            chat_path=args.chat_path,
        )
    if args.provider == "openai-compatible":
        if not args.base_url:
            raise SystemExit("--provider openai-compatible requires --base-url")
        return OpenAICompatiblePlanner(
            base_url=args.base_url,
            model=args.model or "default",
            api_key_file=args.api_key_file,
            api_key_env=("DEEPSEEK_API_KEY", "OPENAI_API_KEY"),
            timeout_s=args.planner_timeout,
            max_retries=args.planner_retries,
            temperature=args.temperature,
            max_tokens=args.max_new_tokens,
            allow_host=args.allow_host,
            chat_path=args.chat_path,
        )
    from methods.llm_agent.policy.structured_agent import (
        build_api_planner,
        build_remote_planner,
    )

    if args.provider == "repo-api":
        return build_api_planner(
            api_provider=args.api_provider, api_model=args.model,
            temperature=args.temperature, max_new_tokens=args.max_new_tokens,
        )
    if not args.remote_url:
        raise SystemExit("--provider repo-remote requires --remote-url")
    return build_remote_planner(
        remote_url=args.remote_url, model=args.model or "default",
        temperature=args.temperature, max_new_tokens=args.max_new_tokens,
    )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    planner = None
    if args.max_model_requests > 0 or args.provider != "scripted":
        planner = build_planner(args)

    state = None
    if args.resume:
        # Same resolution the runner uses, so a checkpoint written with inferred
        # sidecars is not refused (or accepted) on a differently built hash.
        inputs = resolve_inputs(
            args.board, project_path=args.project, rules_path=args.rules
        )
        provenance = build_provenance(
            board_path=inputs.board_path, project_path=inputs.project_path,
            rules_path=inputs.rules_path,
        )
        try:
            state = RunState.load(args.resume, expected_provenance=provenance)
        except ProvenanceMismatchError as exc:
            print(json.dumps({"error": "provenance_mismatch", **exc.detail}, indent=2))
            return 3
        mismatch = rebind_resume_run_dir(state, args.resume, args.run_dir)
        if mismatch is not None:
            print(json.dumps(mismatch, indent=2))
            return 3

    config = RunnerConfig(
        board_path=args.board,
        project_path=args.project,
        rules_path=args.rules,
        run_dir=args.run_dir,
        max_attempts=args.max_attempts,
        max_model_requests=args.max_model_requests,
        max_total_tokens=args.max_total_tokens,
        time_limit_s=args.time_limit,
        engine_call_timeout_s=args.engine_call_timeout,
        max_pairs=args.max_pairs,
        max_per_net=args.max_per_net,
        per_net_tries=args.per_net_tries,
        candidate_limit=args.candidate_limit,
        stall_patience=args.stall_patience,
        observe_radius_mm=args.observe_radius,
        obstacle_limit=args.obstacle_limit,
        planner=planner,
        record_prompts=args.record_prompts,
        progress_callback=(
            None if args.quiet else
            (lambda update: print(json.dumps({"progress": update}), flush=True))
        ),
    )

    try:
        report = RoutingRunner(config, state=state).run()
    except Exception as exc:  # noqa: BLE001 - a CLI must report, not traceback
        print(json.dumps({"error": f"{type(exc).__name__}: {exc}"}, indent=2))
        return 2

    payload = report.to_dict()
    report_path = os.path.join(args.run_dir, "report.json")
    with open(report_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
    payload["report_path"] = report_path
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if report.status in ("completed", "stopped") else 1


if __name__ == "__main__":
    raise SystemExit(main())
