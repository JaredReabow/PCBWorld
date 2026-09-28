#!/usr/bin/env python3
"""Screen edges for measured via openings, read-only, before any DRC trial.

    screen_zone_openings.py --board B [--project P] --edges edges.json \
        [--max-edges N] [--time-budget S] [--out screen.json] [--public-summary]

``edges.json`` is either a list of ``{"edge": [x0, y0, l0, x1, y1, l1],
"net_code": N}`` objects or a list of ``[edge, net_code]`` pairs, where the edge
is the canonical key :func:`pcb_world.agent.observations.edge_key` produces.
Order is preserved and the answer is one row per edge, so a caller can tell a
screened edge that found nothing from an edge the budget never reached.

Read-only end to end: the only engine call is the zone point query, whose answers
are cached per point; no route is started, no checkpoint is taken, nothing is
written back to the board. That is what makes it the cheap pass ahead of any
transactional trial - and why its answer is a *measurement*, never a legality
claim.

``--public-summary`` prints counts only (no coordinates), which is the form that
belongs in a public report; the full per-edge rows stay with the caller.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
os.environ.setdefault("KICAD_ENGINE_REUSE", "0")

from pcb_world.agent import zone_coverage as zc          # noqa: E402
from pcb_world.agent.observations import edge_key         # noqa: E402
from pcb_world.agent.session import AgentSession          # noqa: E402
from pcb_world.engine import KiCadEngine                  # noqa: E402


def load_edges(path: str) -> list[tuple[tuple, int]]:
    raw = json.loads(Path(path).read_text())
    edges: list[tuple[tuple, int]] = []
    for item in raw:
        if isinstance(item, dict):
            key = item.get("edge") or item.get("key")
            net = item.get("net_code")
        elif isinstance(item, (list, tuple)) and len(item) == 2:
            key, net = item
        else:
            continue
        if not isinstance(key, (list, tuple)) or len(key) != 6 or net is None:
            continue
        edges.append((edge_key(key[:3], key[3:]), int(net)))
    return edges


def sha256_file(path: str | None) -> str | None:
    """Content hash of a file, or None when it does not exist.

    A screen is only evidence about *one* board generation, so the output has to
    name which one: a caller comparing two screens, or re-running an old one,
    needs the board (and the rules that produced it) by content rather than by
    path and mtime.
    """
    if not path:
        return None
    candidate = Path(path)
    if not candidate.is_file():
        return None
    digest = hashlib.sha256()
    with candidate.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--board", required=True)
    parser.add_argument("--project", default=None)
    parser.add_argument("--rules", default=None)
    parser.add_argument("--edges", required=True)
    parser.add_argument("--max-edges", type=int, default=None)
    parser.add_argument("--time-budget", type=float, default=None)
    parser.add_argument("--openings", type=int, default=3)
    parser.add_argument("--pitch-mm", type=float, default=0.5)
    parser.add_argument("--samples", type=int, default=96)
    parser.add_argument("--band-mm", type=float, default=0.5)
    parser.add_argument("--search-mm", type=float, default=3.0)
    parser.add_argument("--out", default=None)
    parser.add_argument("--public-summary", action="store_true",
                        help="print counts only, no coordinates")
    args = parser.parse_args(argv)

    edges = load_edges(args.edges)
    if not edges:
        print(json.dumps({"error": "no usable edges in the input"}))
        return 2

    engine = KiCadEngine(args.board, project_path=args.project)
    try:
        session = AgentSession(engine, board_path=args.board)
        coverage = zc.ZoneCoverage(session, enabled=True)
        margin, basis = zc.board_margin_mm(engine)
        rows = zc.screen_openings(
            coverage, edges, margin_mm=margin, max_edges=args.max_edges,
            time_budget_s=args.time_budget, pitch_mm=args.pitch_mm,
            max_samples=args.samples, max_openings=args.openings,
            band_mm=args.band_mm, search_mm=args.search_mm)
        payload = {
            "board_sha256": sha256_file(args.board),
            "project_sha256": sha256_file(args.project),
            "rules_sha256": sha256_file(args.rules),
            "margin_mm": round(float(margin), 4),
            "margin_basis": basis,
            "summary": zc.screen_summary(rows),
            "rows": [row.to_evidence() for row in rows],
        }
        if args.out:
            Path(args.out).write_text(json.dumps(payload, indent=1, sort_keys=True))
    finally:
        try:
            engine.close()
        except Exception:  # noqa: BLE001 - best effort
            pass

    if args.public_summary:
        print(json.dumps(payload["summary"], indent=1, sort_keys=True))
    else:
        print(json.dumps(payload, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
