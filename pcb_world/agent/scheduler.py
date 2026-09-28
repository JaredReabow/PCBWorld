"""Deterministic candidacy, ranking and run bookkeeping for the routing agent.

One module owns the *policy* questions the runner would otherwise scatter:

* which candidate plans are worth probing for an outstanding connection
  (`generate_candidates`),
* how probed candidates are ordered (`rank_candidates`) — verified closure under
  native DRC first, then quality (fewer vias, less added copper, less disturbance
  of foreign copper),
* what has already been tried and must not be retried (`AttemptHistory`),
* whether the run is making progress (`ProgressTracker`),
* what a resume checkpoint contains and how its provenance is verified
  (`RunState`).

The runner and the model-facing tool layer both consume this; neither re-implements
it.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import time
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from pcb_world.agent.observations import NetPair


RUN_STATE_VERSION = 1


class ProvenanceMismatchError(RuntimeError):
    """A resume checkpoint was produced from different inputs than this run."""

    def __init__(self, message: str, **detail: Any) -> None:
        super().__init__(message)
        self.detail: dict[str, Any] = {"reason": "provenance_mismatch", **detail}


def sha256_file(path: str | None) -> str | None:
    if not path or not os.path.isfile(path):
        return None
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def attempt_plan_key(mode: str, waypoints: Iterable[Sequence[float]] = ()) -> tuple:
    """Normalised identity of a plan: mode plus rounded waypoints.

    One definition shared by :meth:`AttemptHistory.tried_keys` and every writer
    of attempt records, so "the same plan again" cannot mean two different things.

    A waypoint may be ``(x, y)`` or ``(x, y, layer)`` — a planner is allowed to
    emit either, and ``(x, y)`` used to raise ``IndexError`` while recording the
    attempt, which turned a poor plan into a crash.
    """
    return (
        str(mode),
        tuple(
            (
                round(float(w[0]), 3), round(float(w[1]), 3),
                int(w[2]) if len(w) > 2 and w[2] is not None else None,
            )
            for w in (waypoints or ())
        ),
    )


def offered_edge_key(pair: Any) -> tuple:
    """The offered connection an attempt on ``pair`` stands for.

    ``pair.key`` is the geometry that was *attempted*. When the scan offered a
    ratsnest edge and a substitution replaced its anchors, the attempted geometry
    is not the offered edge, and ``pair.offered_key`` carries the link back. For
    the offered geometry itself, or a component offer, the two are the same
    statement and the pair's own key is returned.

    One definition, used by every writer of attempt records, so "which offered
    edge was this attempt made for" cannot mean two different things.
    """
    explicit = tuple(getattr(pair, "offered_key", ()) or ())
    return explicit or tuple(pair.key)


def engine_build_hash(engine_home_hint: str | None = None) -> str | None:
    """The C++ content hash stamped next to the loaded router, when discoverable."""
    try:
        from pcb_world.engine import router_lib_dir

        stamp = os.path.join(router_lib_dir(), "ENGINE_CPP_HASH")
        if os.path.isfile(stamp):
            with open(stamp, encoding="utf-8") as handle:
                return handle.read().strip() or None
    except Exception:  # noqa: BLE001 - provenance is best-effort evidence
        return None
    return None


# ---------------------------------------------------------------------------
# Candidates
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Candidate:
    """One deterministic plan to connect a pair."""

    name: str
    mode: str
    waypoints: tuple[tuple[float, float, int], ...] = ()
    kind: str = "detour"          # direct | shove | detour | layer
    rationale: str = ""

    def key(self) -> tuple:
        return (
            self.mode,
            tuple((round(w[0], 3), round(w[1], 3), int(w[2])) for w in self.waypoints),
        )

    def to_probe_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "mode": self.mode,
            "waypoints": [list(w) for w in self.waypoints],
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "mode": self.mode,
            "kind": self.kind,
            "waypoints": [list(w) for w in self.waypoints],
            "rationale": self.rationale,
        }


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _obstacle_interval(
    obstacle: Mapping[str, Any], mx: float, my: float, px: float, py: float,
) -> tuple[float, float] | None:
    """The obstacle's extent along the connection's perpendicular axis.

    Returns ``(lo, hi)``, both relative to the straight line through
    ``(mx, my)``: an obstacle crossing the line has ``lo <= 0 <= hi``, and a wall
    spanning several millimetres reports that span, so a waypoint can be placed
    past its far edge instead of a fixed distance from its centre.
    """
    kind = str(obstacle.get("kind") or "")

    def project(x: float, y: float) -> float:
        return (x - mx) * px + (y - my) * py

    try:
        if kind == "track":
            width = float(obstacle.get("width_mm") or 0.0)
            first = project(float(obstacle["x1_mm"]), float(obstacle["y1_mm"]))
            second = project(float(obstacle["x2_mm"]), float(obstacle["y2_mm"]))
            half = max(width / 2.0, 0.05)
            return min(first, second) - half, max(first, second) + half
        if kind == "via":
            centre = project(float(obstacle["x_mm"]), float(obstacle["y_mm"]))
            half = max(float(obstacle.get("diameter_mm") or 0.0) / 2.0, 0.05)
            return centre - half, centre + half
        if kind == "pad":
            size = obstacle.get("size_mm") or (0.0, 0.0)
            centre = project(float(obstacle["x_mm"]), float(obstacle["y_mm"]))
            half = max(float(size[0]) / 2.0, float(size[1]) / 2.0, 0.05)
            return centre - half, centre + half
    except (KeyError, TypeError, ValueError, IndexError):
        return None
    return None


def _obstacle_detours(
    pair: NetPair,
    obstacles: Sequence[Mapping[str, Any]],
    *,
    bounds: Sequence[float] | None,
    perpendicular: tuple[float, float],
    direction: tuple[float, float] = (1.0, 0.0),
    margin_mm: float,
    limit: int,
    clearance_mm: float = 0.25,
    max_offset_mm: float = 20.0,
) -> list[Candidate]:
    """Detours whose waypoint clears an observed obstacle.

    The straight connection is treated as the axis through its midpoint in the
    direction start->target; an obstacle blocks it when its own extent crosses
    that axis. The waypoint is then placed just past the obstacle's near edge on
    each side, nearer side first. Everything is derived from the observation, so
    the same board and the same connection always give the same plans.
    """
    if not obstacles or limit <= 0:
        return []
    x0, y0, l0 = pair.start
    x1, y1, _l1 = pair.target
    mx, my = (x0 + x1) / 2.0, (y0 + y1) / 2.0
    px, py = perpendicular

    plans: list[Candidate] = []
    seen: set[tuple] = set()
    first_blocking: tuple[Mapping[str, Any], float, float] | None = None
    for index, obstacle in enumerate(obstacles):
        interval = _obstacle_interval(obstacle, mx, my, px, py)
        if interval is None:
            continue
        low, high = interval
        reach = clearance_mm + margin_mm
        if low > reach or high < -reach:
            continue                      # the obstacle does not cross the axis
        for side in sorted((high + reach, low - reach),
                           key=lambda value: (abs(value), value)):
            if abs(side) > max_offset_mm:
                continue
            wx, wy = mx + px * side, my + py * side
            if bounds:
                wx = _clamp(wx, bounds[0] + margin_mm, bounds[2] - margin_mm)
                wy = _clamp(wy, bounds[1] + margin_mm, bounds[3] - margin_mm)
            key = (round(wx, 3), round(wy, 3), int(l0))
            if key in seen:
                continue
            seen.add(key)
            plans.append(Candidate(
                f"obstacle_{index}_{'pos' if side > 0 else 'neg'}_"
                f"{abs(side):.2f}mm",
                "walkaround",
                waypoints=((wx, wy, l0),),
                kind="obstacle",
                rationale=(
                    f"waypoint {abs(side):.2f} mm off the straight line, clearing "
                    f"the {obstacle.get('kind')} on {obstacle.get('net_name') or 'a foreign net'}"
                ),
            ))
            if len(plans) >= limit:
                return plans
        if first_blocking is None:
            first_blocking = (obstacle, low, high)

    # One two-waypoint dodge for the nearest blocking obstacle: enter and leave
    # the offset corridor beside the wall instead of approaching along the
    # straight line and only turning at the midpoint. Two probes at the very
    # most - the dodge is offered before the generic offsets can fill the list.
    if first_blocking is not None and len(plans) < limit:
        obstacle, low, high = first_blocking
        along = _obstacle_interval(obstacle, mx, my, *direction)
        if along is not None:
            reach = clearance_mm + margin_mm
            side = min((high + reach, low - reach), key=lambda value: abs(value))
            for offset, sign in ((0.0, "near"), (1.0, "far")):
                wx, wy = mx + px * side, my + py * side
                entry = (
                    wx + direction[0] * (along[0] - reach),
                    wy + direction[1] * (along[0] - reach),
                )
                exit_ = (
                    wx + direction[0] * (along[1] + reach),
                    wy + direction[1] * (along[1] + reach),
                )
                for point in (entry, exit_):
                    if bounds:
                        point = (
                            _clamp(point[0], bounds[0] + margin_mm, bounds[2] - margin_mm),
                            _clamp(point[1], bounds[1] + margin_mm, bounds[3] - margin_mm),
                        )
                key = (round(entry[0], 3), round(entry[1], 3),
                       round(exit_[0], 3), round(exit_[1], 3), int(l0))
                if key in seen:
                    break
                seen.add(key)
                plans.append(Candidate(
                    f"dodge_{sign}_{abs(side):.2f}mm",
                    "walkaround",
                    waypoints=((entry[0], entry[1], l0), (exit_[0], exit_[1], l0)),
                    kind="dodge",
                    rationale=(
                        f"enter and leave the {abs(side):.2f} mm offset corridor "
                        f"beside the {obstacle.get('kind')} on "
                        f"{obstacle.get('net_name') or 'a foreign net'}"
                    ),
                ))
                break
    return plans


#: Multiples of the caller's clearance that the violation-centred search probes,
#: on top of the single step ``drc_avoid`` already takes. The step the loop used
#: before is the 1x case; a violation that a *bigger* sideways move clears was
#: simply never offered, and one fixed distance is a guess about an obstruction
#: the gate has already measured.
CLEARANCE_SEARCH_STEPS: tuple[float, ...] = (2.0, 4.0, 8.0)


def clearance_search_probes(
    *,
    base_x: float,
    base_y: float,
    perpendicular: tuple[float, float],
    direction: tuple[float, float],
    side_sign: float,
    base_offset: float,
    clearance_mm: float,
    layer: int,
    steps: Sequence[float] = CLEARANCE_SEARCH_STEPS,
    bounds: Sequence[float] | None = None,
    margin_mm: float = 0.5,
) -> list[tuple[float, float, int]]:
    """Waypoints searched outward from a refusal, nearest-first, geometry-named.

    ``base_offset`` is the distance at which the plan was refused, measured from
    the straight line at the violation's own projection. The search steps further
    out along the same normal - the direction that got *away* from the
    obstruction - at fixed multiples of the caller's clearance, then once along
    the connection's own direction, which is the other way a clearance violation
    is escaped without changing layers.

    Deliberately not a general grid search: three outward steps plus one
    along-line step per refusal, bounded by the caller's candidate limit. The
    acceptance gate is unchanged; this only chooses *where* to try.
    """
    clearance = max(0.0, float(clearance_mm))
    px, py = float(perpendicular[0]), float(perpendicular[1])
    dxu, dyu = float(direction[0]), float(direction[1])
    sign = -1.0 if float(side_sign) <= 0 else 1.0
    probes: list[tuple[float, float, int]] = []
    for multiple in steps:
        distance = float(base_offset) + clearance * float(multiple)
        probes.append((base_x + px * distance * sign,
                       base_y + py * distance * sign, int(layer)))
    # One step along the line, offset sideways by the refusal distance, so a
    # violation caused by copper *behind* the waypoint is also addressed.
    probes.append((base_x + dxu * float(base_offset) + px * float(base_offset) * sign,
                   base_y + dyu * float(base_offset) + py * float(base_offset) * sign,
                   int(layer)))
    out: list[tuple[float, float, int]] = []
    seen: set[tuple] = set()
    for x_mm, y_mm, probe_layer in probes:
        if bounds:
            x_mm = _clamp(x_mm, bounds[0] + margin_mm, bounds[2] - margin_mm)
            y_mm = _clamp(y_mm, bounds[1] + margin_mm, bounds[3] - margin_mm)
        key = (round(x_mm, 4), round(y_mm, 4), int(probe_layer))
        if key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out


def clearance_extent_probes(
    *,
    base_x: float,
    base_y: float,
    perpendicular: tuple[float, float],
    obstacles: Sequence[Mapping[str, Any]],
    clearance_mm: float,
    margin_mm: float = 0.2,
    layer: int,
    limit: int = 2,
    max_offset_mm: float = 20.0,
    near_mm: float = 1.5,
    bounds: Sequence[float] | None = None,
    bound_margin_mm: float = 0.5,
) -> list[tuple[float, float, int]]:
    """Waypoints that clear an *observed* obstacle's own extent, nearest first.

    ``clearance_search_probes`` steps outward in multiples of the caller's
    clearance from the offset a plan was refused at - a bounded search of a
    distance the refusal did not measure. This family uses the obstruction
    instead: the obstacle records the caller already observed around the pair are
    projected onto the connection's perpendicular axis at the refusal's own
    position, and the waypoint is placed just past the obstacle's *far edge* on
    the obstacle's own side, plus the applicable clearance and a small margin. A
    wall 0.5 mm wide therefore gets a waypoint past the wall, not a second
    waypoint one measured step further away from it.

    Only obstacles near the refusal contribute (``near_mm``), because the family
    has to be *about this refusal*, not a general sweep of every item around the
    pair. The result is bounded by ``limit`` and named by its geometry, so a
    re-scan cannot mint a new plan key for the same copper. The acceptance gate
    is untouched: this only chooses where to try.
    """
    if limit <= 0 or not obstacles:
        return []
    px, py = float(perpendicular[0]), float(perpendicular[1])
    reach = max(0.0, float(clearance_mm)) + max(0.0, float(margin_mm))
    candidates: list[tuple[float, float]] = []
    seen_offsets: set[float] = set()
    for obstacle in obstacles:
        obstacle_layer = obstacle.get("layer")
        if isinstance(obstacle_layer, (int, float)) and int(obstacle_layer) != int(layer):
            # A track or pad on another copper layer cannot be the obstruction a
            # same-layer waypoint has to clear. A via spans layers (``None``).
            continue
        interval = _obstacle_interval(obstacle, base_x, base_y, px, py)
        if interval is None:
            continue
        low, high = interval
        if low >= 0.0:
            # The obstacle sits entirely on the positive side: pass it there.
            if low > near_mm:
                continue                  # the obstacle is not near the refusal
            offset = high + reach
        elif high <= 0.0:
            if high < -near_mm:
                continue
            offset = low - reach
        else:
            # The interval straddles the straight line (a wall across it): the
            # waypoint clears whichever far edge the shorter way out.
            offset = (high + reach) if high >= -low else (low - reach)
        if abs(offset) > max_offset_mm or round(offset, 4) in seen_offsets:
            continue
        seen_offsets.add(round(offset, 4))
        candidates.append((offset, abs(offset)))
    candidates.sort(key=lambda item: (item[1], item[0]))
    out: list[tuple[float, float, int]] = []
    for offset, _distance in candidates[:max(0, int(limit))]:
        x_mm = base_x + px * offset
        y_mm = base_y + py * offset
        if bounds:
            x_mm = _clamp(x_mm, bounds[0] + bound_margin_mm, bounds[2] - bound_margin_mm)
            y_mm = _clamp(y_mm, bounds[1] + bound_margin_mm, bounds[3] - bound_margin_mm)
        out.append((round(x_mm, 4), round(y_mm, 4), int(layer)))
    return out


def generate_candidates(
    pair: NetPair,
    *,
    board_bbox: Sequence[float] | None = None,
    copper_layers: int = 2,
    max_detours: int = 3,
    allow_layer_detour: bool = True,
    margin_mm: float = 0.5,
    obstacles: Sequence[Mapping[str, Any]] = (),
    max_obstacle_detours: int = 3,
    pour_waypoints: Sequence[Mapping[str, Any]] = (),
    drc_hints: Sequence[tuple[str, float, float, int]] = (),
    max_drc_candidates: int = 2,
    drc_clearance_mm: float = 0.4,
    max_via_jogs: int = 4,
    via_free_positions: Sequence[Mapping[str, Any]] = (),
    #: Measured via-placement openings for a cross-layer pair (see
    #: ``pcb_world.agent.zone_coverage.find_openings``): ``{x_mm, y_mm, layer,
    #: clearance_mm}`` rows. Each becomes one ``kind="zone_gap_via"`` candidate
    #: and the family is silent when the caller measured none - an empty list is
    #: "nothing was measured clear", never "the corridor is clear".
    zone_openings: Sequence[Mapping[str, Any]] = (),
    max_clearance_probes: int = 4,
    #: Waypoints that clear an observed obstacle's own extent, per refused hint,
    #: shared as one budget across the pair. 0 restores the pre-phase-10 set
    #: exactly. Needs ``obstacles``; without an observation nothing is emitted.
    max_extent_probes: int = 2,
    #: The board's own minimum clearance, used by the extent family. ``None``
    #: falls back to ``drc_clearance_mm`` so a caller without design-rule access
    #: still gets a bounded family instead of an unbounded one.
    rule_clearance_mm: float | None = None,
) -> list[Candidate]:
    """Deterministic candidate plans, cheapest/most-likely first.

    Order is meaningful: the runner probes in this order and stops early when a
    candidate is accepted, so routine connections never reach the planner.

    When the caller passes the observed ``obstacles`` around the connection (the
    records :func:`pcb_world.agent.observations.nearest_obstacles` produces), the
    plans that clear an actual obstacle come *before* the generic sideways
    detours: their waypoint is derived from where the obstacle is, so the first
    probe is the informed one.

    ``drc_hints`` are ``(error_type, x_mm, y_mm, layer)`` rows from violations
    the native gate *already refused* an earlier plan of this pair for. They are
    stronger evidence than proximity: they name the obstruction that actually
    stopped the router. Each hint contributes at most one sideways avoidance at
    the violation's own position and one via escape to another copper layer
    there, bounded by ``max_drc_candidates`` hints.

    A **via jog** is offered when the pair's own layers differ or a hint is
    hole-class: a via has to sit somewhere, and a hole-dense spot cannot be fixed
    by moving sideways *within* a layer. The jog candidates place the via at small
    offsets around the anchor (``max_via_jogs`` of them, nearest first) so the
    acceptance gate can pick a spot with room - the alternative is not a wider
    clearance, it is a different position.

    ``via_free_positions`` are the spots a caller's own search found free (see
    ``RoutingRunner._via_free_positions``). They are offered before the fixed jog
    ring because they are the result of looking, not of guessing.

    ``max_clearance_probes`` bounds the violation-centred clearance search: how
    many extra waypoints (``kind="drc_clear"``) each refused hint contributes
    around the position it was refused at. 0 restores the single fixed
    ``drc_avoid`` step exactly. The gate is not touched - these are just other
    places to try, at distances the refusal itself suggested.

    ``max_extent_probes`` bounds the second refusal-derived family
    (``kind="drc_extent"``): a waypoint that clears the *observed* obstacle's own
    extent on the pair's layer instead of a fixed multiple of clearance. It is
    one shared budget across the pair and it needs the ``obstacles`` observation;
    with none, no extent candidate is produced. ``rule_clearance_mm`` is the
    board's own minimum clearance for that family (``None`` falls back to
    ``drc_clearance_mm``), so the offset is measured with the rule the DRC will
    enforce rather than an invented one.
    """
    x0, y0, l0 = pair.start
    x1, y1, l1 = pair.target
    candidates: list[Candidate] = [
        Candidate("direct_walkaround", "walkaround", kind="direct",
                  rationale="straight connection, walk around obstacles"),
        Candidate("direct_shove", "shove", kind="shove",
                  rationale="straight connection, push movable copper"),
    ]

    #: Waypoints already offered for this pair, so a family cannot repeat
    #: another family's geometry and a re-scan cannot mint a second plan key
    #: for the same copper.
    seen_waypoints: set[tuple] = set()

    # A **measured via opening**: for a pair whose layers differ, a caller that
    # measured the corridor can hand over the spots where a via's own copper plus
    # the board's clearance still clear every foreign pour. Offering them beside
    # the direct plans is the point of the family - the direct plan leaves the
    # via position to the router, and when that choice lands in someone else's
    # pour the acceptance gate refuses the copper. These are other positions,
    # named by their own geometry, and they are *candidates*: an opening is a
    # measurement, not a legality claim, and the DRC still decides.
    for opening_index, opening in enumerate(zone_openings or ()):
        try:
            wx = round(float(opening["x_mm"]), 4)
            wy = round(float(opening["y_mm"]), 4)
            wl = int(opening["layer"])
            clearance = float(opening.get("clearance_mm") or 0.0)
        except (KeyError, TypeError, ValueError):
            continue
        waypoint = (wx, wy, wl)
        if waypoint in seen_waypoints:
            continue
        seen_waypoints.add(waypoint)
        candidates.append(Candidate(
            f"zone_gap_via_{int(round(clearance * 1000))}_{opening_index}",
            "walkaround",
            waypoints=(waypoint,),
            kind="zone_gap_via",
            rationale=(
                f"place the layer transition at a measured opening "
                f"({clearance:.3f} mm of clear pour copper, layer {l0} -> {wl}); "
                "the opening is measured, not proved legal - the DRC gate decides"
            ),
        ))

    mx, my = (x0 + x1) / 2.0, (y0 + y1) / 2.0
    dx, dy = x1 - x0, y1 - y0
    length = math.hypot(dx, dy) or 1.0
    # Perpendicular unit vector: offsets move the waypoint sideways off the
    # straight line, which is where a wall usually is.
    px, py = -dy / length, dx / length
    base_offset = 0.5 if pair.gap_mm <= 2.0 else min(2.0, max(1.0, pair.gap_mm * 0.25))

    bounds = list(board_bbox) if board_bbox else None
    direction = (dx / length, dy / length)
    # Hints are ordered canonically, not by history order, and the alternatives
    # they produce are named by their *geometry*. Indexing or naming them by the
    # position of a hint in the pair's growing history made every re-scan mint a
    # "new" plan key for the same copper, so ``tried_keys`` could never retire it
    # and the sweep re-evaluated the same offsets until the budget ran out.
    ordered_hints = sorted(
        (
            (str(hint[0]), float(hint[1]), float(hint[2]), int(hint[3]))
            for hint in drc_hints
            if len(hint) == 4
        ),
        key=lambda item: (item[0], round(item[1], 3), round(item[2], 3), item[3]),
    )
    hint_index = 0
    # One shared budget for the clearance search, so a pair with many recorded
    # hints cannot turn the candidate list into a grid sweep.
    clearance_budget = max(0, int(max_clearance_probes))
    # The extent family's budget is separate but equally shared, so a pair with
    # many hints cannot become an obstacle-by-obstacle sweep either.
    extent_budget = max(0, int(max_extent_probes))
    extent_clearance = (
        float(rule_clearance_mm) if rule_clearance_mm is not None
        else float(drc_clearance_mm)
    )
    for hint in ordered_hints[:max(0, int(max_drc_candidates))]:
        try:
            error_type, hx, hy, hint_layer = (
                str(hint[0]), float(hint[1]), float(hint[2]), int(hint[3]),
            )
        except (IndexError, TypeError, ValueError):
            continue
        hint_index += 1
        # Project the violation onto the straight line: the offset is measured
        # from where the plan actually passes, not from the board's origin.
        t = ((hx - x0) * dx + (hy - y0) * dy) / (length * length)
        t = min(max(t, 0.0), 1.0)
        base_x, base_y = x0 + dx * t, y0 + dy * t
        side = (hx - base_x) * px + (hy - base_y) * py
        # Step to the *other* side of the obstruction: the plan went past it on
        # that side, which is where it was refused.
        sign = -1.0 if side > 0 else 1.0
        offset = max(0.5, abs(side) + float(drc_clearance_mm))
        wx, wy = base_x + px * offset * sign, base_y + py * offset * sign
        if bounds:
            wx = _clamp(wx, bounds[0] + margin_mm, bounds[2] - margin_mm)
            wy = _clamp(wy, bounds[1] + margin_mm, bounds[3] - margin_mm)
        avoid_waypoint = (round(wx, 4), round(wy, 4), l0)
        if avoid_waypoint not in seen_waypoints:
            seen_waypoints.add(avoid_waypoint)
            candidates.append(Candidate(
                f"drc_avoid_{wx:.2f}_{wy:.2f}",
                "walkaround",
                waypoints=(avoid_waypoint,),
                kind="drc_avoid",
                rationale=(
                    f"step {offset:.2f} mm to the far side of a {error_type} the "
                    f"native gate refused at ({hx:.3f}, {hy:.3f})"
                ),
            ))
        # The obstruction the gate measured is in the observation: place a
        # waypoint past its actual extent on the pair's own layer, at the rule's
        # clearance, rather than at a multiple of a clearance the refusal did not
        # measure. Refusals that name a *hole* are the via family's business, not
        # this one - a wider sideways step does not move a hole.
        if (extent_budget > 0 and obstacles
                and "hole" not in str(error_type).lower()):
            for probe in clearance_extent_probes(
                base_x=base_x, base_y=base_y, perpendicular=(px, py),
                obstacles=obstacles, clearance_mm=extent_clearance, layer=l0,
                limit=extent_budget, bounds=bounds, bound_margin_mm=margin_mm,
            ):
                if extent_budget <= 0:
                    break
                if probe in seen_waypoints:
                    continue
                seen_waypoints.add(probe)
                extent_budget -= 1
                ex_mm, ey_mm, _extent_layer = probe
                candidates.append(Candidate(
                    f"drc_extent_{ex_mm:.2f}_{ey_mm:.2f}",
                    "walkaround",
                    waypoints=(probe,),
                    kind="drc_extent",
                    rationale=(
                        f"clear the observed obstacle beside the {error_type} the "
                        f"native gate refused at ({hx:.3f}, {hy:.3f}) by its own "
                        f"extent plus {extent_clearance:.2f} mm"
                    ),
                ))
        # The refusal measured an obstruction; one fixed distance is a guess about
        # it. Search outward from the same projection, bounded by the caller, and
        # name each probe by its own geometry so a re-scan cannot mint a new key
        # for the same copper.
        if clearance_budget > 0:
            for probe in clearance_search_probes(
                base_x=base_x, base_y=base_y, perpendicular=(px, py),
                direction=direction, side_sign=sign, base_offset=offset,
                clearance_mm=float(drc_clearance_mm), layer=l0,
                bounds=bounds, margin_mm=margin_mm,
            ):
                if clearance_budget <= 0:
                    break
                if probe in seen_waypoints:
                    continue
                seen_waypoints.add(probe)
                clearance_budget -= 1
                px_mm, py_mm, _probe_layer = probe
                candidates.append(Candidate(
                    f"drc_clear_{px_mm:.2f}_{py_mm:.2f}",
                    "walkaround",
                    waypoints=(probe,),
                    kind="drc_clear",
                    rationale=(
                        f"search {math.hypot(px_mm - base_x, py_mm - base_y):.2f} mm "
                        f"outward from a {error_type} the native gate refused at "
                        f"({hx:.3f}, {hy:.3f})"
                    ),
                ))
        if copper_layers >= 2:
            other = l0 + 1 if l0 < copper_layers else l0 - 1
            if 1 <= other <= copper_layers and other != l0:
                escape_waypoint = (round(hx, 4), round(hy, 4), other)
                if escape_waypoint not in seen_waypoints:
                    seen_waypoints.add(escape_waypoint)
                    candidates.append(Candidate(
                        f"drc_escape_{hx:.2f}_{hy:.2f}_L{other}",
                        "walkaround",
                        waypoints=(escape_waypoint,),
                        kind="drc_escape",
                        rationale=(
                            f"leave layer {l0} for layer {other} where the native "
                            f"gate refused a {error_type}"
                        ),
                    ))
                    # The same escape in shove mode: the refusal may be foreign
                    # copper the router is allowed to displace, and walkaround
                    # would never try. Shove is a legal mode here (locked copper
                    # is still refused by the router), not a rule change.
                    candidates.append(Candidate(
                        f"drc_escape_shove_{hx:.2f}_{hy:.2f}_L{other}",
                        "shove",
                        waypoints=(escape_waypoint,),
                        kind="drc_escape",
                        rationale=(
                            f"leave layer {l0} for layer {other} where the native "
                            f"gate refused a {error_type}, shoving movable copper"
                        ),
                    ))

    # Only now the *contextual* families. A recorded refusal outranks proximity:
    # it is a measurement of this pair's own obstruction, where a pour waypoint or
    # an obstacle detour is a heuristic about where the blockage is. Ordering the
    # heuristic families first put five to six candidates in front of the refusal
    # alternatives, and since the runner truncates to its candidate limit the
    # refusal-derived plans were never evaluated at all - the 36 pairs that had
    # recorded violations produced zero refusal-derived evaluations in a 1 318 s
    # segment. The families and their internal order are unchanged; they simply
    # follow the evidence now.
    #
    # Pour-aware plans come first among the contextual ones: every waypoint here
    # is copper the engine has already proved is in the *far terminal's* component,
    # so closing pad -> waypoint closes the connection. A plain detour only hopes
    # the offset clears the wall.
    for index, point in enumerate(pour_waypoints):
        waypoint = (float(point["x_mm"]), float(point["y_mm"]),
                    int(point.get("layer") or l0))
        candidates.append(Candidate(
            f"pour_{index}_{point.get('source', 'component')}",
            "walkaround",
            waypoints=(waypoint,),
            kind="pour",
            rationale=(
                "waypoint inside copper already connected to the far terminal "
                f"({point.get('shared_anchors', 0)} shared anchors, "
                f"{point.get('source', 'component')})"
            ),
        ))
    candidates.extend(_obstacle_detours(
        pair, obstacles, bounds=bounds, perpendicular=(px, py),
        direction=direction,
        margin_mm=margin_mm, limit=max_obstacle_detours,
    ))

    hole_refused = any("hole" in str(hint[0]).lower() for hint in drc_hints)
    via_strategy_needed = copper_layers >= 2 and (l1 != l0 or hole_refused)
    if via_strategy_needed:
        escape_layer = l1 if l1 != l0 else (l0 + 1 if l0 < copper_layers else l0 - 1)
        if 1 <= escape_layer <= copper_layers and escape_layer != l0:
            for index, spot in enumerate(via_free_positions):
                try:
                    wx, wy = float(spot["x_mm"]), float(spot["y_mm"])
                    layer = int(spot.get("layer") or escape_layer)
                except (KeyError, TypeError, ValueError):
                    continue
                waypoint = (round(wx, 4), round(wy, 4), layer)
                if waypoint in seen_waypoints:
                    continue
                seen_waypoints.add(waypoint)
                candidates.append(Candidate(
                    f"via_free_{index}_{wx:.2f}_{wy:.2f}_L{layer}",
                    "walkaround",
                    waypoints=(waypoint,),
                    kind="via_free",
                    rationale=(
                        f"a via {math.hypot(wx - x0, wy - y0):.2f} mm from the "
                        "anchor that the engine's via prefilter did not refuse "
                        f"(layer {l0} -> {layer}); the DRC gate still decides"
                    ),
                ))
    if via_strategy_needed and max_via_jogs > 0:
        escape_layer = l1 if l1 != l0 else (l0 + 1 if l0 < copper_layers else l0 - 1)
        if 1 <= escape_layer <= copper_layers and escape_layer != l0:
            for index, (dx, dy) in enumerate(
                _via_jog_offsets(max_via_jogs, base=base_offset)
            ):
                wx, wy = x0 + dx, y0 + dy
                if bounds:
                    wx = _clamp(wx, bounds[0] + margin_mm, bounds[2] - margin_mm)
                    wy = _clamp(wy, bounds[1] + margin_mm, bounds[3] - margin_mm)
                candidates.append(Candidate(
                    f"via_jog_{index}_{dx:+.2f}_{dy:+.2f}",
                    "walkaround",
                    waypoints=((wx, wy, escape_layer),),
                    kind="via_jog",
                    rationale=(
                        f"place the layer change {math.hypot(dx, dy):.2f} mm off "
                        f"the anchor (layer {l0} -> {escape_layer})"
                        + (", refused for a hole-class rule at the anchor"
                           if hole_refused else "")
                    ),
                ))

    # The layer change is a *different* strategy from moving sideways, so it is
    # offered before the generic sideways offsets: a run that truncates the list
    # to its candidate limit must not lose the only plan that leaves the layer.
    if allow_layer_detour and copper_layers >= 2:
        other = l1 if l1 != l0 else (2 if l0 == 1 else 1)
        if 1 <= other <= copper_layers and other != l0:
            candidates.append(Candidate(
                f"layer_detour_L{other}",
                "walkaround",
                waypoints=((mx, my, other),),
                kind="layer",
                rationale=(
                    f"change to layer {other} at the midpoint and back at the "
                    "target, to leave a blocked layer"
                ),
            ))

    offsets: list[float] = []
    for step in range(max_detours):
        magnitude = base_offset * (1.0 + 0.5 * step)
        offsets.extend([magnitude, -magnitude])
    offsets = offsets[:max_detours]

    for index, offset in enumerate(offsets):
        wx, wy = mx + px * offset, my + py * offset
        if bounds:
            wx = _clamp(wx, bounds[0] + margin_mm, bounds[2] - margin_mm)
            wy = _clamp(wy, bounds[1] + margin_mm, bounds[3] - margin_mm)
        candidates.append(Candidate(
            f"detour_{index}_{'n' if offset > 0 else 's'}_{abs(offset):.2f}mm",
            "walkaround",
            waypoints=((wx, wy, l0),),
            kind="detour",
            rationale=(
                f"waypoint {abs(offset):.2f} mm off the straight line at the "
                "midpoint, walk around"
            ),
        ))

    return candidates


def _via_jog_offsets(count: int, *, base: float) -> list[tuple[float, float]]:
    """Small offsets around an anchor, nearest-first, for placing a via elsewhere.

    Axis-first because a jog along the axis the track already runs costs the least
    copper; the diagonal pair follows so a spot that is blocked on both axes is
    still reachable. Sizes are the caller's ``base_offset`` (the same scale the
    detour family uses) and half of it, so the search adapts to the pair's gap
    instead of assuming a fixed pitch.
    """
    near = max(0.2, float(base) * 0.5)
    far = max(0.4, float(base))
    ring = [
        (far, 0.0), (-far, 0.0), (0.0, far), (0.0, -far),
        (near, near), (-near, -near), (near, -near), (-near, near),
    ]
    out: list[tuple[float, float]] = []
    for dx, dy in ring:
        key = (round(dx, 4), round(dy, 4))
        if key not in out:
            out.append(key)
        if len(out) >= max(1, int(count)):
            break
    return out


def rank_candidates(
    probes: Iterable[tuple[Candidate, Mapping[str, Any]]],
) -> list[tuple[Candidate, Mapping[str, Any]]]:
    """Order probed candidates by acceptance, then by quality.

    ``probes`` are ``(candidate, probe_result_dict)`` pairs. The sort key is
    deliberately explicit so the policy is auditable:

    1. accepted (verified closure and no new relevant DRC violation),
    2. connected (closed the connection at all),
    3. fewest added relevant violations,
    4. fewest added vias,
    5. least added copper length,
    6. least disturbance of *other* nets' geometry,
    7. fewest plan steps.
    """
    def key(item: tuple[Candidate, Mapping[str, Any]]) -> tuple:
        candidate, result = item
        evidence = result.get("evidence", {}) if isinstance(result, Mapping) else {}
        pre = evidence.get("pre_transaction", {}) or {}
        final = evidence.get("final", {}) or {}
        delta = evidence.get("drc_delta", {}) or {}
        changed = list(evidence.get("changed_nets", []) or [])
        steps = result.get("steps", []) if isinstance(result, Mapping) else []
        vias_added = int(
            evidence.get("vias_added")
            if evidence.get("vias_added") is not None
            else max(0, int(final.get("via_count") or 0) - int(pre.get("via_count") or 0))
        )
        added_length = float(evidence.get("added_length_mm") or 0.0)
        return (
            0 if result.get("accepted") else 1,
            0 if result.get("connected") else 1,
            int(delta.get("added_relevant_count") or 0),
            vias_added,
            round(added_length, 3),
            len(changed),
            len(steps),
            candidate.name,
        )

    return sorted(probes, key=key)


# ---------------------------------------------------------------------------
# Attempt history and progress
# ---------------------------------------------------------------------------


#: Upper bound on the verbatim failure text one attempt record keeps. An engine
#: crash message carries a stderr tail; 4 000 characters is enough for the
#: ownership/signal lines and keeps a 5 000-attempt checkpoint bounded.
EXCEPTION_TEXT_LIMIT = 4000

#: Rollback-detail keys kept on an attempt record. The full detail is a dozen
#: fields per attempt, most of them session-state echoes; these are the ones that
#: answer "was the copper proved back, and if not, what refused it".
ROLLBACK_DETAIL_KEYS = (
    "restored",
    "copper_digest_matches",
    "session_state_matches",
    "target_matches",
    "target_advisory",
    "expected_digest",
    "actual_digest",
    "restore_exception",
    "probe_error",
    "unverifiable",
    "expected_state",
    "actual_state",
)


def compact_exception_text(text: Any) -> str | None:
    """Bounded, single-field form of an exception message for the run state."""
    if text is None:
        return None
    value = str(text)
    if len(value) <= EXCEPTION_TEXT_LIMIT:
        return value
    return (
        value[:EXCEPTION_TEXT_LIMIT]
        + f"\n... truncated at {EXCEPTION_TEXT_LIMIT} of {len(value)} characters"
    )


def compact_rollback_detail(detail: Any) -> dict[str, Any] | None:
    """Keep only the rollback fields that decide whether a restore was proved."""
    if not isinstance(detail, Mapping):
        return None
    kept = {key: detail.get(key) for key in ROLLBACK_DETAIL_KEYS if key in detail}
    if "restore_exception" in kept:
        kept["restore_exception"] = compact_exception_text(kept["restore_exception"])
    return kept or None


@dataclass
class AttemptRecord:
    """What was tried for one pair, and what happened."""

    pair_key: tuple
    plan_key: tuple
    candidate: str
    mode: str
    kind: str
    source: str                  # deterministic | planner
    outcome: str
    accepted: bool
    connected: bool
    reason: str = ""
    added_relevant: int = 0
    vias_added: int = 0
    added_length_mm: float = 0.0
    changed_nets: tuple[int, ...] = ()
    duration_s: float = 0.0
    timestamp: str = ""
    probe_only: bool = False
    #: True when the record is one *plan inside a pair's deterministic candidate
    #: sweep* rather than an attempt on the pair itself. A sweep executes its
    #: candidates as real transactions (see ``RoutingRunner._apply_candidates``),
    #: so they are not ``probe_only``; but the per-pair budget bounds sweeps, not
    #: plans, or a six-candidate pair would spend six attempts and starve the
    #: queue. ``attempts_for_pair`` is the one place that distinction is consumed.
    sweep_member: bool = False
    committed: bool = False
    #: Where the offered connection came from: ``ratsnest`` (the engine drew the
    #: edge between these anchors), ``component`` (native terminal membership
    #: proved two copper components are still disconnected) or ``substitution``
    #: (an offered edge whose anchor carried no copper, replaced by proved copper
    #: of the same net). A substitution is not the offered edge being resolved.
    offer_source: str = "ratsnest"
    #: Native component identities the offer stands for, when membership was
    #: available. Together with ``board_digest`` this is what makes "the same
    #: plan on the same copper" a checkable statement: a duplicate plan is not
    #: suppressed across a different component membership.
    component_start: str = ""
    component_target: str = ""
    substituted: bool = False
    #: The offered connection this attempt's pair stands for, when the pair is a
    #: substitution. ``pair_key`` stays the *actual attempted geometry* (the
    #: substituted anchors); this is the stable identity of the edge the scan
    #: offered, so a substituted attempt can be attributed to the connection it
    #: was made for. Empty when the attempted geometry *is* the offered geometry.
    offered_pair_key: tuple = ()
    #: Added relevant violation classes of a refused candidate, as
    #: ``(error_type, count)``, and positions of some of them as
    #: ``(error_type, x_mm, y_mm, layer)``. Recorded so a later attempt can aim an
    #: alternative at the obstruction that actually refused the plan.
    drc_classes: tuple[tuple[str, int], ...] = ()
    drc_hints: tuple[tuple[str, float, float, int], ...] = ()
    #: True when the plan closed the connection and the native gate refused the
    #: copper it closed it with. ``connected`` cannot say this: the copper is
    #: rolled back, so the recorded board no longer shows the connection.
    closed_before_refusal: bool = False
    #: Geometry digest of the board the attempt was measured on, so a failure can
    #: be re-tried once after the copper around it changes (see ``stale_on``).
    board_digest: str | None = None
    #: Copper state the result reported (``kept`` / ``restored`` /
    #: ``retained_unknown``); kept so a quarantine is never silently dropped.
    copper_state: str | None = None
    #: Plan steps that reported success (copper actually applied), so a record
    #: never claims an unmodified board while steps were applied.
    steps_applied: int = 0
    #: Which plan step stopped the transaction, and its position in the plan.
    #: ``steps_applied`` alone cannot say where a plan died: a route that never
    #: opened and a via that never landed leave the same count, so a failure
    #: analysis had to replay the stored plan — and a replay is only faithful
    #: while the harness is unchanged. Naming the step lets a run state answer
    #: "where do plans fail" on its own. The session stops at the first failure,
    #: so a failed step's index equals ``steps_applied``. An empty kind with an
    #: index of ``-1`` means every step succeeded (or the checkpoint predates
    #: the field), never a failure at step 0, which is a real step.
    failed_step_kind: str = ""
    failed_step_index: int = -1
    #: Run-level disposition when a later saved-artifact gate rejects a
    #: transaction that passed its local connection gate.
    final_disposition: str | None = None
    #: The exception a failure path caught, verbatim but truncated. A
    #: ``drc_unavailable`` or an unverifiable rollback used to be recorded as its
    #: category alone, which left the cause — a reaped engine child, a rule-load
    #: failure, a native signal — out of the run state and only recoverable by
    #: reproducing the run. Bounded so one runaway message cannot bloat the
    #: checkpoint (see ``EXCEPTION_TEXT_LIMIT``).
    failure_exception: str | None = None
    #: Rollback detail of a failure that had to undo copper, compacted to the
    #: fields that decide whether the restore was *proved* (see
    #: ``compact_rollback_detail``). ``None`` when the attempt never rolled back.
    rollback_detail: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        # ``committed`` is the only thing that makes a plan "already done": a
        # probe that was evaluated (and restored) must still block a retry of the
        # same plan.
        if self.probe_only:
            self.committed = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "pair_key": list(self.pair_key),
            "plan_key": [self.plan_key[0], [list(w) for w in (self.plan_key[1] or ())]]
            if self.plan_key else [],
            "candidate": self.candidate,
            "mode": self.mode,
            "kind": self.kind,
            "source": self.source,
            "outcome": self.outcome,
            "accepted": self.accepted,
            "connected": self.connected,
            "reason": self.reason,
            "added_relevant": self.added_relevant,
            "vias_added": self.vias_added,
            "added_length_mm": round(self.added_length_mm, 4),
            "changed_nets": list(self.changed_nets),
            "duration_s": round(self.duration_s, 3),
            "timestamp": self.timestamp,
            "probe_only": self.probe_only,
            "sweep_member": self.sweep_member,
            "committed": self.committed,
            "offer_source": self.offer_source,
            "component_start": self.component_start,
            "component_target": self.component_target,
            "substituted": bool(self.substituted),
            "offered_pair_key": list(self.offered_pair_key),
            "drc_classes": [list(item) for item in self.drc_classes],
            "drc_hints": [list(item) for item in self.drc_hints],
            "closed_before_refusal": bool(self.closed_before_refusal),
            "board_digest": self.board_digest,
            "copper_state": self.copper_state,
            "steps_applied": self.steps_applied,
            "failed_step_kind": self.failed_step_kind,
            "failed_step_index": self.failed_step_index,
            "final_disposition": self.final_disposition,
            "failure_exception": self.failure_exception,
            "rollback_detail": self.rollback_detail,
        }


class AttemptHistory:
    """Per-pair attempt log that refuses to retry an identical failed plan."""

    def __init__(self, records: Iterable[AttemptRecord] = ()) -> None:
        self._records: list[AttemptRecord] = list(records)

    def note(self, record: AttemptRecord) -> None:
        if not record.timestamp:
            record.timestamp = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        self._records.append(record)

    @property
    def records(self) -> list[AttemptRecord]:
        return list(self._records)

    def for_pair(self, pair: NetPair) -> list[AttemptRecord]:
        return [item for item in self._records if tuple(item.pair_key) == pair.key]

    def tried_keys(
        self, pair: NetPair, board_digest: str | None = None
    ) -> set[tuple]:
        """Plan keys already tried for this pair without committing copper."""
        # Last record per plan wins: a plan that was evaluated and later
        # committed is done, not "tried and failed".
        latest: dict[tuple, bool] = {}
        for item in self.for_pair(pair):
            # Failed candidates belong to the copper generation on which they
            # were measured. Keep lifetime records as evidence, but allow a
            # fresh evaluation after neighboring copper changes.
            if board_digest is not None and item.board_digest not in (None, board_digest):
                continue
            if not item.plan_key:
                continue
            mode, waypoints = item.plan_key[0], item.plan_key[1]
            latest[attempt_plan_key(mode, waypoints)] = bool(item.committed)
        return {key for key, committed in latest.items() if not committed}

    def count_for_pair(self, pair: NetPair) -> int:
        """Every record for this pair, including probe evaluations (evidence)."""
        return len(self.for_pair(pair))

    def attempts_for_pair(self, pair: NetPair) -> int:
        """Real attempts on this pair: applied plans and planner calls.

        Evaluating a candidate set tries several plans without spending an
        attempt, so counting those records against the per-net budget made one
        real attempt look like six and starved every other pair on the board.
        Two shapes mean "one plan of a sweep": a probed candidate
        (``probe_only``) and a candidate the sweep applied and rolled back
        (``sweep_member``).
        """
        return sum(
            1 for record in self.for_pair(pair)
            if not record.probe_only
            and (not record.sweep_member or record.committed)
        )

    def attempts_on(self, pair: NetPair, board_digest: str | None) -> int:
        """Real attempts on this pair *on this board generation*.

        ``attempts_for_pair`` is the lifetime budget and stays exactly as it was.
        This is the coverage view: how much of the budget for this pair has
        already been spent against the copper the caller is looking at. A record
        measured on a different generation does not count, because a plan that
        failed there says nothing about this board.
        """
        return sum(
            1 for record in self.for_pair(pair)
            if not record.probe_only
            and (not record.sweep_member or record.committed)
            and (board_digest is None or record.board_digest in (None, board_digest))
        )

    def records_on(self, pair: NetPair, board_digest: str | None) -> int:
        """Every record for this pair bound to this generation.

        This is the *coverage* measure, and it is deliberately wider than
        ``attempts_on``: a candidate that a sweep evaluated and rolled back is
        real work spent on the pair, so the pair is no longer waiting for its
        first look. Using the narrower measure here made a pair with a long sweep
        history look untouched and put it back at the front of the queue.
        """
        return sum(
            1 for record in self.for_pair(pair)
            if board_digest is None
            or record.board_digest in (None, board_digest)
        )

    def worked_on(self, pair: NetPair, board_digest: str | None) -> bool:
        """True when any record for this pair is bound to this generation."""
        return self.records_on(pair, board_digest) > 0

    def stale_on(self, pair: NetPair, board_digest: str | None) -> bool:
        """True when this pair's failures were measured on different copper.

        A candidate that could not be routed (or that added a violation) on one
        board state may be perfectly good after neighbouring copper changed, so a
        failure is filed against the geometry it was measured on. One retry per
        changed state is allowed; ``count_for_pair`` still bounds the total, so
        this can never become an unbounded retry loop.
        """
        if board_digest is None:
            return False
        records = self.for_pair(pair)
        if not records:
            return False
        return any(
            record.board_digest and record.board_digest != board_digest
            for record in records
        ) and all(
            record.board_digest != board_digest for record in records
        )

    #: Outcome marking a pair whose every deterministic plan has been evaluated
    #: and which has no planner available to ask.
    EXHAUSTED_OUTCOME = "plans_exhausted"

    def exhausted_on(self, pair: NetPair, board_digest: str | None) -> bool:
        """True when this pair was already run out of plans on this same board.

        Without this the loop re-selects the pair every iteration, spends nothing
        on it, and stalls the whole run while routable pairs further down the
        queue are never reached.
        """
        for record in self.for_pair(pair):
            if record.outcome != self.EXHAUSTED_OUTCOME or record.probe_only:
                continue
            if board_digest is None or record.board_digest in (None, board_digest):
                return True
        return False

    def as_dicts(self) -> list[dict[str, Any]]:
        return [item.to_dict() for item in self._records]

    @classmethod
    def from_dicts(cls, items: Iterable[Mapping[str, Any]]) -> "AttemptHistory":
        records = []
        for item in items:
            raw_key = item.get("plan_key") or []
            if len(raw_key) == 2:
                plan_key = (
                    str(raw_key[0]),
                    tuple(tuple(w) for w in raw_key[1]),
                )
            else:
                plan_key = ()
            # "Absent" and "a value this field never should have held" are the
            # same answer: not recorded. ``bool`` is excluded on purpose — it is
            # an ``int`` in Python and ``True`` would read as step 1.
            raw_index = item.get("failed_step_index")
            failed_step_index = (
                raw_index
                if isinstance(raw_index, int) and not isinstance(raw_index, bool)
                else -1
            )
            records.append(AttemptRecord(
                pair_key=tuple(item.get("pair_key", [])),
                plan_key=plan_key,
                candidate=str(item.get("candidate", "")),
                mode=str(item.get("mode", "")),
                kind=str(item.get("kind", "")),
                source=str(item.get("source", "")),
                outcome=str(item.get("outcome", "")),
                accepted=bool(item.get("accepted")),
                connected=bool(item.get("connected")),
                reason=str(item.get("reason", "")),
                added_relevant=int(item.get("added_relevant", 0)),
                vias_added=int(item.get("vias_added", 0)),
                added_length_mm=float(item.get("added_length_mm", 0.0)),
                changed_nets=tuple(int(n) for n in item.get("changed_nets", [])),
                duration_s=float(item.get("duration_s", 0.0)),
                timestamp=str(item.get("timestamp", "")),
                probe_only=bool(item.get("probe_only", False)),
                sweep_member=bool(item.get("sweep_member", False)),
                committed=bool(item.get("committed", item.get("accepted", False))),
                offer_source=str(item.get("offer_source", "ratsnest")),
                component_start=str(item.get("component_start", "")),
                component_target=str(item.get("component_target", "")),
                substituted=bool(item.get("substituted", False)),
                # Absent on checkpoints written before this field existed: an
                # empty key means "the attempted geometry was the offered
                # geometry or the link was not recorded", never an error.
                offered_pair_key=tuple(item.get("offered_pair_key") or ()),
                drc_classes=tuple(
                    (str(pair[0]), int(pair[1]))
                    for pair in (item.get("drc_classes") or [])
                    if len(pair) == 2
                ),
                drc_hints=tuple(
                    (str(row[0]), float(row[1]), float(row[2]), int(row[3]))
                    for row in (item.get("drc_hints") or [])
                    if len(row) == 4
                ),
                closed_before_refusal=bool(item.get("closed_before_refusal", False)),
                board_digest=item.get("board_digest"),
                copper_state=item.get("copper_state"),
                steps_applied=int(item.get("steps_applied", 0)),
                # Absent on checkpoints written before these fields existed; an
                # empty kind with index -1 is "not recorded", never a failure at
                # step 0, which is a real step.
                failed_step_kind=str(item.get("failed_step_kind") or ""),
                failed_step_index=failed_step_index,
                final_disposition=item.get("final_disposition"),
                # Absent on checkpoints written before these fields existed;
                # a missing key is "not recorded", never an error.
                failure_exception=item.get("failure_exception"),
                rollback_detail=(
                    dict(item["rollback_detail"])
                    if isinstance(item.get("rollback_detail"), Mapping)
                    else None
                ),
            ))
        return cls(records)


@dataclass
class ProgressTracker:
    """Board-level progress with a bounded no-progress stop."""

    patience: int = 6
    timeline: list[dict[str, Any]] = field(default_factory=list)
    best: dict[str, Any] | None = None
    stagnation: int = 0

    def update(self, progress: Mapping[str, Any]) -> bool:
        """Record progress; return True when it improved on the best seen."""
        snapshot = dict(progress)
        snapshot["at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        self.timeline.append(snapshot)
        improved = self.best is None or self._better(snapshot, self.best)
        if improved:
            self.best = snapshot
            self.stagnation = 0
        else:
            self.stagnation += 1
        return improved

    @staticmethod
    def _better(candidate: Mapping[str, Any], best: Mapping[str, Any]) -> bool:
        return (
            int(candidate.get("unrouted_edges", 0)),
            int(candidate.get("pad_group_total", 0)),
        ) < (
            int(best.get("unrouted_edges", 0)),
            int(best.get("pad_group_total", 0)),
        )

    @property
    def stalled(self) -> bool:
        return self.stagnation >= self.patience

    def to_dict(self) -> dict[str, Any]:
        return {
            "patience": self.patience,
            "stagnation": self.stagnation,
            "best": self.best,
            "timeline": self.timeline,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ProgressTracker":
        tracker = cls(patience=int(payload.get("patience", 6)))
        tracker.stagnation = int(payload.get("stagnation", 0))
        tracker.best = dict(payload["best"]) if payload.get("best") else None
        tracker.timeline = [dict(item) for item in payload.get("timeline", [])]
        return tracker


# ---------------------------------------------------------------------------
# Run state (resume)
# ---------------------------------------------------------------------------


def build_provenance(
    *, board_path: str, project_path: str | None, rules_path: str | None
) -> dict[str, Any]:
    """Everything a resume must match before it continues a run."""
    return {
        "board_sha256": sha256_file(board_path),
        "project_sha256": sha256_file(project_path),
        "rules_sha256": sha256_file(rules_path),
        "engine_cpp_hash": engine_build_hash(),
        "python": f"{os.sys.version_info.major}.{os.sys.version_info.minor}.{os.sys.version_info.micro}",
    }


@dataclass
class RunState:
    """Serialisable run checkpoint: provenance, attempts, best board, usage."""

    board_path: str
    project_path: str | None
    rules_path: str | None
    provenance: dict[str, Any]
    run_dir: str
    status: str = "running"          # running | stopped | completed | blocked
    stop_reason: str = ""
    created_at: str = ""
    updated_at: str = ""
    attempts: AttemptHistory = field(default_factory=AttemptHistory)
    progress: ProgressTracker = field(default_factory=ProgressTracker)
    best_board_path: str | None = None
    best_board_sha256: str | None = None
    best_progress: dict[str, Any] | None = None
    model_usage: dict[str, Any] = field(default_factory=lambda: {
        "requests": 0, "prompt_tokens": 0, "completion_tokens": 0,
        "categories": {},
    })
    metrics: dict[str, Any] = field(default_factory=dict)
    version: int = RUN_STATE_VERSION

    def touch(self) -> None:
        now = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        if not self.created_at:
            self.created_at = now
        self.updated_at = now

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "board_path": self.board_path,
            "project_path": self.project_path,
            "rules_path": self.rules_path,
            "run_dir": self.run_dir,
            "provenance": self.provenance,
            "status": self.status,
            "stop_reason": self.stop_reason,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "attempts": self.attempts.as_dicts(),
            "progress": self.progress.to_dict(),
            "best_board_path": self.best_board_path,
            "best_board_sha256": self.best_board_sha256,
            "best_progress": self.best_progress,
            "model_usage": self.model_usage,
            "metrics": self.metrics,
        }

    def save(self, path: str | None = None) -> str:
        """Write the checkpoint atomically; returns the path written."""
        self.touch()
        target = path or os.path.join(self.run_dir, "run_state.json")
        os.makedirs(os.path.dirname(target), exist_ok=True)
        tmp = f"{target}.tmp"
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(self.to_dict(), handle, indent=2, sort_keys=True)
        os.replace(tmp, target)
        return target

    @classmethod
    def load(
        cls,
        path: str,
        *,
        expected_provenance: Mapping[str, Any] | None = None,
        allow_mismatch: bool = False,
    ) -> "RunState":
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
        if int(payload.get("version", 0)) != RUN_STATE_VERSION:
            raise ProvenanceMismatchError(
                f"run state version {payload.get('version')} is not "
                f"{RUN_STATE_VERSION}",
                found=payload.get("version"), expected=RUN_STATE_VERSION,
            )
        provenance = dict(payload.get("provenance", {}))
        if expected_provenance is not None and not allow_mismatch:
            differing = {
                key: {"checkpoint": provenance.get(key), "current": value}
                for key, value in expected_provenance.items()
                if provenance.get(key) != value
            }
            if differing:
                raise ProvenanceMismatchError(
                    "resume refused: the checkpoint was produced from different "
                    f"inputs/build than this run ({sorted(differing)})",
                    differences=differing,
                )
        state = cls(
            board_path=str(payload.get("board_path", "")),
            project_path=payload.get("project_path"),
            rules_path=payload.get("rules_path"),
            provenance=provenance,
            run_dir=str(payload.get("run_dir", os.path.dirname(path))),
            status=str(payload.get("status", "running")),
            stop_reason=str(payload.get("stop_reason", "")),
            created_at=str(payload.get("created_at", "")),
            updated_at=str(payload.get("updated_at", "")),
            attempts=AttemptHistory.from_dicts(payload.get("attempts", [])),
            progress=ProgressTracker.from_dict(payload.get("progress", {})),
            best_board_path=payload.get("best_board_path"),
            best_board_sha256=payload.get("best_board_sha256"),
            best_progress=payload.get("best_progress"),
            model_usage=dict(payload.get("model_usage", {})),
            metrics=dict(payload.get("metrics", {})),
        )
        return state
