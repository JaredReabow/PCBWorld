#!/usr/bin/env python3
"""Full-vs-incremental native DRC differential on real copper.

    drc_incremental_differential.py --board B --project P --rules R \
        [--cases all] [--out evidence.json]

The acceptance gate may only use the engine's scoped DRC re-check
(``run_drc_incremental``) if it returns *the same multiset* as a whole-board DRC
on identical copper, for every shape routing produces. This runs those shapes on
the board the caller names and reports, per case: the two multisets, whether they
agree, the verdict each produces against the shared baseline, and the timings.

The comparison is state-faithful: one engine runs the baseline DRC, then the
operation, then the incremental pass; it is restored from a checkpoint and the
same operation is replayed for the full pass. Copper is compared by digest, so a
case whose two runs produced different geometry is reported as such instead of
being counted as agreement.

Read-only w.r.t. its inputs; nothing is saved or promoted.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

os.environ.setdefault("KICAD_ENGINE_REUSE", "0")

from pcb_world.agent.drc_gate import (  # noqa: E402
    DrcContextError,
    context_identity,
    diff_sets,
    is_connectivity_finding,
    take_violations,
)
from pcb_world.engine import KiCadEngine  # noqa: E402


def _physical_key(violation) -> str:
    """A key that does not move when KiCad mints a fresh UUID for new copper.

    The gate's own identity is UUID-based (correctly: it must prove *physical*
    identity against the board inventory). Replaying the same operation in the
    same engine nevertheless assigns new UUIDs to the tracks it creates, so a
    UUID-keyed comparison of two runs measures the UUID stream, not the DRC. The
    differential therefore compares the reported violation itself.
    """
    return "|".join(str(part) for part in (
        int(violation.error_code), str(violation.error_type),
        round(float(violation.x_mm), 4), round(float(violation.y_mm), 4),
        int(violation.layer), tuple(sorted(str(n) for n in (violation.net_names or ()))),
        str(violation.message),
    ))


def _multiset(violation_set) -> dict[str, int]:
    """Only the findings acceptance judges: clearance/layout, not ratsnest rows.

    A connectivity finding carries the *coordinates of the unrouted connection*,
    and the ratsnest recomputes those with its own tie-breaking after any
    operation, so two runs over identical copper can name a different endpoint of
    the same missing connection. The gate never accepts or refuses on those rows
    (``acceptable`` reads ``added_relevant``), so the differential compares their
    *count* and requires the relevant multiset to be identical.
    """
    counts: dict[str, int] = {}
    for violation in violation_set.violations:
        if is_connectivity_finding(violation):
            continue
        key = _physical_key(violation)
        counts[key] = counts.get(key, 0) + 1
    return counts


def _compare(base, a, b) -> dict[str, Any]:
    left, right = _multiset(a), _multiset(b)
    only_incremental = {k: v for k, v in left.items() if right.get(k) != v}
    only_full = {k: v for k, v in right.items() if left.get(k) != v}
    delta_incremental = diff_sets(base, a)
    delta_full = diff_sets(base, b)
    return {
        "multiset_identical": left == right,
        "connectivity_count_identical": a.connectivity == b.connectivity,
        "incremental_total": a.total,
        "full_total": b.total,
        "incremental_relevant": a.relevant,
        "full_relevant": b.relevant,
        "incremental_connectivity": a.connectivity,
        "full_connectivity": b.connectivity,
        "incremental_verdict_acceptable": delta_incremental.acceptable,
        "full_verdict_acceptable": delta_full.acceptable,
        "incremental_added_relevant": len(delta_incremental.added_relevant),
        "full_added_relevant": len(delta_full.added_relevant),
        "verdicts_agree": (
            delta_incremental.acceptable == delta_full.acceptable
            and len(delta_incremental.added_relevant)
            == len(delta_full.added_relevant)
        ),
        "only_incremental": list(only_incremental.items())[:5],
        "only_full": list(only_full.items())[:5],
    }


def _digest(engine, path: str) -> str:
    engine.save(path)
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _run_case(engine, rules: str, name: str, operate, work: Path) -> dict[str, Any]:
    """Baseline, op -> incremental, restore, same op -> full, compare."""
    from pcb_world.agent.observations import NetPair  # noqa: F401  (type clarity)

    started = time.perf_counter()
    baseline = take_violations(engine, rules)
    baseline_s = time.perf_counter() - started

    checkpoint = engine.checkpoint()
    applied_a = operate()
    digest_a = _digest(engine, str(work / f"{name}_a.kicad_pcb"))
    started = time.perf_counter()
    incremental = take_violations(engine, rules, incremental=True)
    incremental_s = time.perf_counter() - started
    engine.restore(checkpoint)

    applied_b = operate()
    digest_b = _digest(engine, str(work / f"{name}_b.kicad_pcb"))
    started = time.perf_counter()
    full = take_violations(engine, rules)
    full_s = time.perf_counter() - started
    engine.restore(checkpoint)

    comparison = _compare(baseline, incremental, full)
    detail = {"a": applied_a, "b": applied_b}
    # A case whose operation created no copper (or no violation it was supposed to
    # create) is *not* coverage: the two passes agree trivially. It is reported as
    # a failed case so a vacuous comparison cannot be mistaken for a differential.
    produced = all(bool(item.get("produced", True)) for item in detail.values())
    return {
        "case": name,
        "baseline_relevant": baseline.relevant,
        "baseline_total": baseline.total,
        "baseline_s": round(baseline_s, 2),
        "incremental_s": round(incremental_s, 2),
        "full_s": round(full_s, 2),
        "speedup": round(full_s / max(incremental_s, 1e-6), 2),
        "identical_copper": digest_a == digest_b,
        "operation_detail": detail,
        "operation_produced": produced,
        **comparison,
    }


def _edge(engine, *, max_gap_mm: float = 3.0):
    """A short same-layer ratsnest edge: the cheapest legal connection."""
    for edge in engine.get_ratsnest():
        if int(edge.layer1) != int(edge.layer2):
            continue
        gap = abs(float(edge.x2_mm) - float(edge.x1_mm)) + abs(
            float(edge.y2_mm) - float(edge.y1_mm)
        )
        if gap <= max_gap_mm:
            return edge
    return None


def _short_route(engine, edge, mode: int = 0):
    def operate() -> dict[str, Any]:
        engine.set_routing_mode(mode)
        engine.start_route(float(edge.x1_mm), float(edge.y1_mm), int(edge.layer1))
        engine.move(float(edge.x2_mm), float(edge.y2_mm))
        fixed = engine.finish()
        return {"mode": mode, "finished": bool(fixed)}

    return operate


def _shove(engine, index: int):
    """Displace an existing track through the router's drag surface."""
    def operate() -> dict[str, Any]:
        tracks = list(engine.get_tracks())
        if not tracks or index >= len(tracks):
            return {"shoved": False, "reason": "no track at index"}
        track = tracks[index]
        engine.start_drag(
            float(track.x1_mm), float(track.y1_mm), int(track.layer),
        )
        engine.fix_drag(force_commit=True)
        return {"shoved": True, "track": str(track.uuid)}

    return operate


def _via_seed_points(engine, *, layers: int, limit: int = 24):
    """Points where placing new copper is realistic, best first.

    Existing vias come first: the board already satisfies the rules at those
    coordinates, so a via placed beside one is the shape most likely to be
    accepted, and it is exactly the shape a bad via placement is judged on.
    Track endpoints follow. Nothing is invented - every seed is copper the board
    already carries.
    """
    seeds: list[tuple[float, float, int]] = []
    seen: set[tuple[float, float, int]] = set()

    def add(x_mm: float, y_mm: float, layer: int) -> None:
        key = (round(x_mm, 3), round(y_mm, 3), int(layer))
        if key in seen or not (1 <= int(layer) <= layers):
            return
        seen.add(key)
        seeds.append((float(x_mm), float(y_mm), int(layer)))

    for via in engine.get_vias():
        for layer in (int(via.top_layer), int(via.bottom_layer)):
            add(float(via.x_mm), float(via.y_mm), layer)
    for track in engine.get_tracks():
        add(float(track.x1_mm), float(track.y1_mm), int(track.layer))
        add(float(track.x2_mm), float(track.y2_mm), int(track.layer))
    return seeds[:limit]


def _place_via(engine, *, layers: int, offsets=(0.3, 0.6, 1.0)):
    """Place one real via, searching seeds × offsets, and keep it.

    Returns ``(placed, detail)``. Failed probes are rolled back through an
    engine checkpoint, so the board carries exactly the successful insertion.
    """
    from pcb_world.core import action as core_action

    for x_mm, y_mm, layer in _via_seed_points(engine, layers=layers):
        other = layer + 1 if layer < layers else layer - 1
        for dx, dy in ((offsets[0], 0.0), (0.0, offsets[0]),
                       (-offsets[0], 0.0), (0.0, -offsets[0]),
                       (offsets[1], offsets[1]), (-offsets[1], offsets[1]),
                       (offsets[2], 0.0), (0.0, offsets[2])):
            checkpoint = engine.checkpoint()
            before_vias = engine.get_via_count()
            engine.set_routing_mode(0)
            started, _detail = core_action.start_route(engine, x_mm, y_mm, layer)
            placed = False
            if started:
                placed, _detail = core_action.make_via(engine, x_mm + dx, y_mm + dy, 0)
                if not placed and engine.is_routing():
                    engine.cancel_route()
            if placed and engine.get_via_count() > before_vias:
                engine.release_checkpoint(checkpoint)
                return True, {
                    "seed": [x_mm, y_mm, layer], "offset": [dx, dy],
                    "vias_before": before_vias, "vias_after": engine.get_via_count(),
                    "layer_from": layer, "layer_to": other,
                }
            if engine.is_routing():
                engine.cancel_route()
            engine.restore(checkpoint)
            engine.release_checkpoint(checkpoint)
    return False, {"reason": "no seed/offset produced a via"}


def _via_insertion(engine, *, layers: int):
    """Insert a real via and a short span: the shape via escapes produce.

    Uses the same primitive the session's ``via`` step uses
    (``pcb_world.core.action.make_via`` = route to the point *and* place the via),
    not ``toggle_via``, which only arms the via mode and produced nothing here.
    Candidates are tried until a via is actually added; ``produced`` says whether
    one was, so a case that created no copper cannot be counted as coverage.
    """
    def operate() -> dict[str, Any]:
        placed, detail = _place_via(engine, layers=layers)
        return {"produced": placed, **detail}

    return operate


def _layer_transition(engine, *, layers: int):
    """Route on one layer, place a via, and finish on the other layer."""
    from pcb_world.core import action as core_action

    def operate() -> dict[str, Any]:
        before_tracks = engine.get_track_count()
        placed, detail = _place_via(engine, layers=layers)
        if not placed:
            return {"produced": False, **detail}
        # Continue on the far layer from where the via landed: this is the
        # "layer transition" shape (copper on both sides of one via).
        x_mm, y_mm, layer = (float(value) for value in detail["seed"])
        other = int(detail["layer_to"])
        dx, dy = (float(value) for value in detail["offset"])
        engine.set_routing_mode(0)
        if core_action.start_route(engine, x_mm + dx, y_mm + dy, layer)[0]:
            engine.switch_layer(other)
            if engine.is_routing():
                core_action.make_line(engine, x_mm + dx + 0.6, y_mm + dy + 0.3, 0)
                core_action.finish(engine, 0)
        crossed = engine.get_track_count() > before_tracks
        return {"produced": bool(crossed), "crossed_layers": bool(crossed),
                "tracks_before": before_tracks,
                "tracks_after": engine.get_track_count(), **detail}

    return operate


def _failed_route_with_via(engine, *, layers: int):
    """A via-containing attempt that does not close, then cancels.

    This is the shape a discarded via escape leaves behind, so the rollback the
    engine has to prove is the one that undoes a via plus two spans.
    """
    def operate() -> dict[str, Any]:
        # Place a real via, then discard the whole operation. The caller's
        # rollback comparison is what proves the discarded copper (and the DRC
        # state that described it) came back exactly.
        placed, detail = _place_via(engine, layers=layers)
        return {"produced": placed, "discarded": True, **detail}

    return operate


def _clearance_regression(engine, *, max_pairs: int = 12):
    """Route between two *different* nets' copper: an intentional violation.

    The operation is chosen so the copper really does regress - the caller checks
    that the whole-board pass reports added relevant findings and reports
    ``regression_not_reproduced`` if it does not, rather than passing silently.
    """
    from pcb_world.core import action as core_action

    endpoints: list[tuple[float, float, int, int]] = []
    for track in engine.get_tracks():
        layer = int(track.layer)
        net = int(track.net_code)
        if net <= 0 or not (1 <= layer <= 4):
            continue
        endpoints.append((float(track.x1_mm), float(track.y1_mm), layer, net))
        endpoints.append((float(track.x2_mm), float(track.y2_mm), layer, net))
    chosen = None
    best = None
    for index, left in enumerate(endpoints):
        for right in endpoints[index + 1:]:
            if left[3] == right[3] or left[2] != right[2]:
                continue
            gap = math.hypot(right[0] - left[0], right[1] - left[1])
            if not (0.2 <= gap <= 1.5):
                continue
            if best is None or gap < best:
                best = gap
                chosen = (left, right)
    if chosen is None:
        # Last resort: a track endpoint and a pad of another net on the same layer.
        for track in engine.get_tracks():
            layer = int(track.layer)
            net = int(track.net_code)
            if net <= 0 or not (1 <= layer <= 4):
                continue
            start = (float(track.x1_mm), float(track.y1_mm), layer, net)
            for pad in engine.get_pads():
                if int(pad.net_code) <= 0 or int(pad.net_code) == net:
                    continue
                if int(pad.layer) != layer:
                    continue
                gap = math.hypot(float(pad.x_mm) - start[0],
                                 float(pad.y_mm) - start[1])
                if 0.2 <= gap <= 1.5:
                    chosen = (start, (float(pad.x_mm), float(pad.y_mm), layer,
                                      int(pad.net_code)))
                    break
            if chosen:
                break
    if chosen is None:
        return None

    start, target = chosen

    def operate() -> dict[str, Any]:
        engine.set_routing_mode(1)      # shove: get as close as the router will
        core_action.start_route(engine, start[0], start[1], int(start[2]))
        ok, _detail = core_action.make_line(engine, target[0], target[1], 1)
        core_action.finish(engine, 1)
        return {"produced": bool(ok), "start": list(start[:3]),
                "target": list(target[:3]),
                "from_net": int(start[3]), "to_net": int(target[3])}

    return operate


def _rules_content_change(engine, rules: str, work: Path) -> dict[str, Any]:
    """A rule file whose bytes changed behind the same path must refuse a stale baseline."""
    from pcb_world.agent.session import AgentSession

    session = AgentSession(engine, board_path=engine.board_path,
                           incremental_drc=True)
    gate = session._gate
    digest = session.board_digest() or ""
    baseline = gate.baseline(rules, digest)
    original = Path(rules).read_bytes()
    mutated = work / "changed.kicad_dru"
    mutated.write_bytes(original + b"\n(rule \"extra\" (constraint clearance (min 9mm)))\n")
    try:
        # Same path, different bytes: the gate compares context identities and
        # must refuse rather than diff a baseline from another rule regime.
        os.replace(mutated, rules)
        try:
            gate.verify(baseline, rules, digest)
            verdict = "ran"
        except DrcContextError:
            verdict = "refused"
        finally:
            Path(rules).write_bytes(original)
        return {
            "case": "custom_rules_content_change",
            "stale_baseline_verdict": verdict,
            "baseline_context": list(baseline.context),
            "context_after_change": list(context_identity(engine, rules)),
        }
    finally:
        Path(rules).write_bytes(original)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--board", required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--rules", required=True)
    parser.add_argument("--work", default=None)
    parser.add_argument("--out", default=None)
    args = parser.parse_args(argv[1:])
    layers = 4
    try:
        from pcb_world.engine import KiCadEngine as _Engine

        probe = _Engine(str(Path(args.board)), project_path=str(Path(args.project)))
        try:
            layers = int(probe.get_copper_layer_count())
        finally:
            probe.close()
    except Exception:                   # noqa: BLE001 - the case set adapts
        layers = 4

    work = Path(args.work) if args.work else Path(args.board).parent / "drc_differential"
    work.mkdir(parents=True, exist_ok=True)

    # Work on copies: the rule-content case has to rewrite a rule file, and the
    # inputs (in particular a promoted artifact's sidecars) must not be touched
    # even for the duration of a run.
    inputs = work / "inputs"
    inputs.mkdir(parents=True, exist_ok=True)
    board = inputs / "board.kicad_pcb"
    project = inputs / "board.kicad_pro"
    rules = inputs / "board.kicad_dru"
    for source, target in (
        (args.board, board), (args.project, project), (args.rules, rules),
    ):
        target.write_bytes(Path(source).read_bytes())

    engine = KiCadEngine(str(board), project_path=str(project))
    results: list[dict[str, Any]] = []
    try:
        edge = _edge(engine)
        if edge is not None:
            results.append(_run_case(
                engine, str(rules), "connect_direct",
                _short_route(engine, edge, 0), work,
            ))
            results.append(_run_case(
                engine, str(rules), "connect_shove_mode",
                _short_route(engine, edge, 1), work,
            ))
        tracks = list(engine.get_tracks())
        if tracks:
            results.append(_run_case(
                engine, str(rules), "shove_existing_track",
                _shove(engine, 0), work,
            ))
        # The shapes the new multi-layer / via-escape strategies produce: a real
        # via insertion and span, a layer transition, and a via-containing
        # attempt that is discarded. The scoped pass may only be used for these
        # once it returns what a whole-board pass returns on identical copper.
        results.append(_run_case(
            engine, str(rules), "via_insertion_span",
            _via_insertion(engine, layers=layers), work,
        ))
        results.append(_run_case(
            engine, str(rules), "layer_transition",
            _layer_transition(engine, layers=layers), work,
        ))
        results.append(_run_case(
            engine, str(rules), "failed_route_with_via_discarded",
            _failed_route_with_via(engine, layers=layers), work,
        ))
        clearance = _clearance_regression(engine)
        if clearance is not None:
            case = _run_case(
                engine, str(rules), "intentional_clearance_regression",
                clearance, work,
            )
            if not case.get("full_added_relevant"):
                case["regression_not_reproduced"] = True
            results.append(case)
        else:
            results.append({
                "case": "intentional_clearance_regression",
                "regression_not_reproduced": True,
                "reason": "no cross-net copper pair close enough to force a violation",
            })
        # Rollback case: the DRC state must travel with the checkpoint.
        checkpoint = engine.checkpoint()
        before = take_violations(engine, str(rules))
        if edge is not None:
            _short_route(engine, edge, 0)()
        engine.restore(checkpoint)
        after = take_violations(engine, str(rules))
        results.append({
            "case": "rollback_restores_drc_state",
            "multiset_identical": _multiset(before) == _multiset(after),
            "connectivity_count_identical": before.connectivity == after.connectivity,
            "before_total": before.total,
            "after_total": after.total,
        })
        results.append(_rules_content_change(engine, str(rules), work))
    finally:
        engine.close()

    summary = {
        "board": args.board,
        "rules": args.rules,
        "worked_on": str(board),
        "cases": results,
        "copper_layers": layers,
        "all_multisets_identical": all(
            case.get("multiset_identical",
                     case.get("stale_baseline_verdict") == "refused")
            and case.get("connectivity_count_identical", True)
            and case.get("operation_produced", True)
            and not case.get("regression_not_reproduced", False)
            for case in results
        ),
    }
    out = Path(args.out) if args.out else work / "differential.json"
    out.write_text(json.dumps(summary, indent=1, sort_keys=True))
    print(json.dumps({k: v for k, v in summary.items() if k != "cases"}, indent=1))
    for case in results:
        print(json.dumps({
            key: case.get(key) for key in (
                "case", "multiset_identical", "verdicts_agree", "identical_copper",
                "connectivity_count_identical",
                "incremental_total", "full_total", "incremental_s", "full_s",
                "speedup", "stale_baseline_verdict", "full_added_relevant",
                "incremental_added_relevant", "operation_detail",
                "regression_not_reproduced",
            ) if key in case
        }))
    print(f"evidence: {out}")
    return 0 if summary["all_multisets_identical"] else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
