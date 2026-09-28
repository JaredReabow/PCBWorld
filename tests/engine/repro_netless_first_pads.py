#!/usr/bin/env python3
"""Diagnostic: same-logical-pad pairs must not depend on declaration order.

    python3 tests/engine/repro_netless_first_pads.py

Not a test - it is deliberately not named ``test_*`` so the harness's groups do
not collect it - but it does make a verdict, and exits non-zero when the
invariant it measures is broken. It is the standalone view of what
``tests/engine/test_reporter_cache_canonical_order.py`` pins with full pair
identities on both front ends.

The provider reports a same-logical-pad pair (two pads of one footprint sharing a
pad number, one netted and one netless) from its ``testPadAgainstItem`` branch,
and the run's ``checkedPairs`` cache records the layer it visited. When the
netless pad is the one visited first, ``GetNetCode() == 0`` takes an early return
*without* reporting. Before T29's repair that early return still left the layer
claimed, so the later visit of the netted pad on that layer was filtered out and
the pair vanished: on a board whose families declare the netless partners before
the netted pad, every same-logical-pad pair disappeared from the report.

The fix releases that claim when - and only when - the visited pad is netless,
its partner is netted, and the two are the same logical pad. What this diagnostic
shows on the synthetic four-layer fixture (deterministic, no real project data):

* every pair the fixture defines is reported exactly once, in **both** orders;
* the two ordinary collisions survive both orders;
* the netless-first order loses nothing.

The gated coverage is ``test_reporter_cache_canonical_order.py``; this script is
the human-readable one-command view.
"""

from __future__ import annotations

import collections
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent.parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

os.environ.setdefault("KICAD_ENGINE_REUSE", "0")

import reporter_fixture as fixture                                          # noqa: E402

SHORTING_ERROR_CODE = 2


def observe(netted_first: bool) -> dict:
    from pcb_world.agent.drc_gate import take_violations
    from pcb_world.engine import KiCadEngine

    directory = Path(tempfile.mkdtemp(prefix="repro-netless-first-"))
    board = fixture.write_fixture(directory, netted_first=netted_first)
    engine = KiCadEngine(str(board), project_path=str(directory / "board.kicad_pro"))
    try:
        found = take_violations(engine, str(directory / "board.kicad_dru"))
        pairs = collections.Counter(
            tuple(sorted((str(v.item_a), str(v.item_b))))
            for v in found.violations if int(v.error_code) == SHORTING_ERROR_CODE)
    finally:
        engine.close()
    return {"netted_first": netted_first, "rows": sum(pairs.values()),
            "pairs": set(pairs), "multiplicities": sorted(set(pairs.values()))}


def main() -> int:
    expected = set(fixture.expected_pair_ids())
    forbidden = set(fixture.forbidden_pair_ids())
    forward = observe(True)
    reverse = observe(False)
    for label, observation in (("netted pad declared first", forward),
                               ("netless partners first   ", reverse)):
        print(f"{label}: rows={observation['rows']} "
              f"pairs={len(observation['pairs'])} "
              f"multiplicities={observation['multiplicities']}")
    print(f"expected                    : {fixture.expected_shorting_pairs()} pairs "
          f"({fixture.expected_same_logical_pad_pairs()} same-logical-pad + "
          f"{fixture.expected_genuine_pairs()} genuine)")

    problems = []
    for label, observation in (("forward", forward), ("reverse", reverse)):
        missing = sorted(expected - observation["pairs"])
        unexpected = sorted(observation["pairs"] - expected)
        forbidden_hits = sorted(observation["pairs"] & forbidden)
        if missing:
            problems.append(f"{label}: missing {len(missing)} pair(s)")
        if unexpected:
            problems.append(f"{label}: {len(unexpected)} unexpected pair(s)")
        if forbidden_hits:
            problems.append(f"{label}: {len(forbidden_hits)} exempt/control pair(s)")
        if observation["multiplicities"] not in ([], [1]):
            problems.append(f"{label}: multiplicity {observation['multiplicities']}")
    if forward["pairs"] != reverse["pairs"]:
        lost = sorted(forward["pairs"] - reverse["pairs"])
        problems.append(f"the netless-first order loses {len(lost)} pair(s)")

    if problems:
        for problem in problems:
            print("FAIL:", problem)
        return 1
    print("OK: both declaration orders report the same exact pair set, once each")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
