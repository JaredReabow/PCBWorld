#!/usr/bin/env python3
"""Hash-bound failure taxonomy for a board's outstanding connections.

Phase 1 of the V3 campaign answers one question before any routing strategy is
chosen: *for every connection the engine still draws, what is actually known
about why it is still open?* Guessing from a sample is the failure mode this
module exists to prevent - a refused plan on ten pairs says nothing about the
other hundred and twenty-five.

The source of truth is the engine's own ratsnest on one board generation. Every
edge it draws is classified exactly once, so the category totals sum to the
outstanding-edge count; nothing is folded into "other" and no sampled refusal is
promoted to "impossible".

Two counts are kept apart on purpose:

* the **edge count** - one entry per ratsnest edge, which is what the engine
  reports as unconnected;
* the **component-pair count** - for every net, the number of unordered pairs of
  its proved copper components. A net with four disconnected components has six
  pairs and may draw fewer (or more) ratsnest edges; the pair count is the amount
  of real work an exhaustive router faces.

Attempt evidence is joined only where it is *bound to this board*: a record is
used when its ``board_digest`` equals the generation being taxonomised, and it is
attributed to an edge either by the canonical pair key or by the native component
identities the offer stood for. A record measured on other copper is dropped, and
the taxonomy says how many were dropped rather than treating them as this
board's outcomes. An edge with no qualifying record is ``unattempted`` - never
"impossible".

The report has two shapes. :func:`build_taxonomy` returns the full private
record (net names, coordinates, refusal geometry). :func:`public_summary`
reduces it to counts that carry no net identity and no coordinates.

    python3 tools/reliability/failure_taxonomy.py \
        --board board.kicad_pcb --project board.kicad_pro --rules board.kicad_dru \
        --history run_state.json --out taxonomy.json --public-summary public.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pcb_world.agent.observations import (  # noqa: E402
    COINCIDENT_EPS_MM,
    CopperAnchorIndex,
    LayerConventionError,
    LayerResolver,
    NetPair,
    _anchor_layer,
    native_components,
)

TAXONOMY_VERSION = 1

#: Primary categories. Ordered by precedence: the first that applies wins, so
#: exactly one is assigned per edge and the totals sum to the edge count.
CATEGORY_ALREADY_CONNECTED = "already_connected_stale_edge"
CATEGORY_UNROUTABLE_DEGENERATE = "unroutable_degenerate_same_point"
CATEGORY_LAYER_UNRESOLVABLE = "layer_unresolvable"
CATEGORY_COPPER_ABSENT_ANCHOR = "copper_absent_at_offered_anchor"
CATEGORY_UNNAMED_ANCHOR = "unnamed_net_at_offered_anchor"
CATEGORY_INSUFFICIENT_COMPONENT_ANCHORS = "insufficient_component_anchors"
CATEGORY_DRC_REGRESSION = "drc_regression"
CATEGORY_VIA_NO_CONTINUATION = "via_no_continuation"
CATEGORY_CONNECTION_NOT_VERIFIED = "connection_not_verified"
CATEGORY_PLANS_EXHAUSTED = "plans_exhausted"
#: Unattempted, and a run that looked at this board reported its *bounded scan*
#: withheld pairs. The evidence is run-level, so the category asserts "an
#: unattempted edge in a run with an observed cap" - never that this particular
#: edge was the one withheld. The name says which of those two things it is.
CATEGORY_UNATTEMPTED_CAP_OBSERVED = "unattempted_cap_observed"
CATEGORY_UNATTEMPTED = "unattempted_no_record"

#: Bucket for a record whose ``failed_step_kind`` is empty. It is named for what
#: the evidence says rather than for a cause: an empty kind is written both by a
#: plan whose every step succeeded and by a checkpoint that predates the field,
#: and the record alone does not say which. Provenance (the run's own code and
#: board digest) separates them; this counter never guesses.
NO_FAILED_STEP_RECORDED = "no_failed_step_recorded"

CATEGORY_ORDER: tuple[str, ...] = (
    CATEGORY_ALREADY_CONNECTED,
    CATEGORY_UNROUTABLE_DEGENERATE,
    CATEGORY_LAYER_UNRESOLVABLE,
    CATEGORY_COPPER_ABSENT_ANCHOR,
    CATEGORY_UNNAMED_ANCHOR,
    CATEGORY_INSUFFICIENT_COMPONENT_ANCHORS,
    CATEGORY_DRC_REGRESSION,
    CATEGORY_VIA_NO_CONTINUATION,
    CATEGORY_CONNECTION_NOT_VERIFIED,
    CATEGORY_PLANS_EXHAUSTED,
    CATEGORY_UNATTEMPTED_CAP_OBSERVED,
    CATEGORY_UNATTEMPTED,
)

#: Categories that can only be assigned from a recorded attempt on this board.
ATTEMPTED_CATEGORIES: frozenset[str] = frozenset({
    CATEGORY_DRC_REGRESSION,
    CATEGORY_VIA_NO_CONTINUATION,
    CATEGORY_CONNECTION_NOT_VERIFIED,
    CATEGORY_PLANS_EXHAUSTED,
})

#: What a caller may conclude from a category. Nothing here says "impossible":
#: a refusal is evidence about the plans that were evaluated, not about the
#: connection.
CATEGORY_MEANING: dict[str, str] = {
    CATEGORY_ALREADY_CONNECTED:
        "the engine still draws the edge but the two anchors are one cluster; "
        "stale drawing, nothing to route",
    CATEGORY_UNROUTABLE_DEGENERATE:
        "both anchors are the same point on one layer; no copper can join them",
    CATEGORY_LAYER_UNRESOLVABLE:
        "an anchor's copper layer cannot be named; the pair was never offered",
    CATEGORY_COPPER_ABSENT_ANCHOR:
        "at least one ratsnest anchor carries no copper at all, so the offered "
        "edge cannot be routed from as drawn",
    CATEGORY_UNNAMED_ANCHOR:
        "copper is present under at least one ratsnest anchor but no pad or via "
        "in its cluster resolves the scheduled net, so the point-identity rule "
        "cannot name the endpoint",
    CATEGORY_INSUFFICIENT_COMPONENT_ANCHORS:
        "the net has two or more proved components but no anchor pair between "
        "them could be proved on the scheduled net",
    CATEGORY_DRC_REGRESSION:
        "a plan closed the connection and the native gate refused the copper it "
        "closed it with; the classes and positions below are the refusal",
    CATEGORY_VIA_NO_CONTINUATION:
        "the evaluated plans needed a via and every searched via spot had no "
        "proved continuation to the far terminal",
    CATEGORY_CONNECTION_NOT_VERIFIED:
        "plans were applied and rolled back because the connectivity probe did "
        "not show the connection closed; no DRC refusal was recorded",
    CATEGORY_PLANS_EXHAUSTED:
        "every deterministic plan for the pair was evaluated and no plan closed "
        "it; no planner was configured",
    CATEGORY_UNATTEMPTED_CAP_OBSERVED:
        "the edge was never attempted on this generation, and a run that looked "
        "at this board reported that its bounded scan withheld pairs somewhere "
        "(net cap / component window / pair cap). Run-level evidence: it does "
        "not prove this particular edge was the one withheld",
    CATEGORY_UNATTEMPTED:
        "no attempt for this edge on this board generation was recorded; its "
        "routability is unmeasured",
}


def _round_anchor(point: Sequence[float]) -> tuple[float, float, int]:
    return (round(float(point[0]), 4), round(float(point[1]), 4), int(point[2]))


def canonical_edge_key(pair_key: Sequence[Any]) -> tuple:
    """Order-independent identity of one connection.

    The engine may draw a ratsnest edge in either direction, and a history
    record may store the other one; sorting the two endpoints makes "the same
    connection" a comparison that cannot be fooled by drawing order.
    """
    values = list(pair_key)
    if len(values) < 7:
        return tuple(values)
    net = values[0]
    first = (round(float(values[1]), 3), round(float(values[2]), 3), int(values[3]))
    second = (round(float(values[4]), 3), round(float(values[5]), 3), int(values[6]))
    start, target = sorted((first, second))
    return (int(net), start[0], start[1], start[2], target[0], target[1], target[2])


def _canonical_components(left: str, right: str) -> frozenset[str]:
    return frozenset(item for item in (str(left or ""), str(right or "")) if item)


@dataclass(frozen=True)
class EdgeFact:
    """Everything observed about one ratsnest edge on one board generation."""

    index: int
    key: tuple
    net_code: int
    net_name: str
    start: tuple[float, float, int]
    target: tuple[float, float, int]
    gap_mm: float
    pair_kind: str
    pad_groups_on_net: int
    components_on_net: int
    component_pairs_on_net: int
    start_net_at: int | None
    target_net_at: int | None
    #: Anchor set size the engine reported under each endpoint (0 = no copper at
    #: all). A non-zero cluster with an unnamed net is a different fact from an
    #: empty point, and the runner's own verdict distinguishes them.
    start_cluster_items: int
    target_cluster_items: int
    anchor_absent: bool
    already_connected: bool
    component_start: str
    component_target: str
    layer_error: str | None = None
    obstacle_mm: float | None = None

    @property
    def layer_relation(self) -> str:
        return "same_layer" if self.start[2] == self.target[2] else "cross_layer"

    @property
    def component_pair(self) -> frozenset[str]:
        return _canonical_components(self.component_start, self.component_target)

    @property
    def anchor_state(self) -> str:
        """What the engine can say about the two offered anchors.

        ``proved`` - both anchors carry copper the point-identity rule names as
        the scheduled net. ``copper_absent`` - at least one anchor carries no
        copper at all. ``copper_unnamed`` - copper is present under an anchor but
        no pad or via in its cluster resolves the scheduled net, so the endpoint
        cannot be named from the point alone.
        """
        if self.layer_error:
            return "unresolved"
        unnamed = False
        for net, items in ((self.start_net_at, self.start_cluster_items),
                           (self.target_net_at, self.target_cluster_items)):
            if net is None:
                return "unreadable"
            if net != 0:
                continue
            if items == 0:
                return "copper_absent"
            unnamed = True
        return "copper_unnamed" if unnamed else "proved"

    def to_private(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "key": list(self.key),
            "net_code": self.net_code,
            "net_name": self.net_name,
            "start": [round(self.start[0], 4), round(self.start[1], 4), self.start[2]],
            "target": [round(self.target[0], 4), round(self.target[1], 4),
                       self.target[2]],
            "gap_mm": round(self.gap_mm, 4),
            "pair_kind": self.pair_kind,
            "layer_relation": self.layer_relation,
            "pad_groups_on_net": self.pad_groups_on_net,
            "components_on_net": self.components_on_net,
            "component_pairs_on_net": self.component_pairs_on_net,
            "start_net_at": self.start_net_at,
            "target_net_at": self.target_net_at,
            "start_cluster_items": self.start_cluster_items,
            "target_cluster_items": self.target_cluster_items,
            "anchor_state": self.anchor_state,
            "anchor_absent": self.anchor_absent,
            "already_connected": self.already_connected,
            "component_start": self.component_start,
            "component_target": self.component_target,
            "layer_error": self.layer_error,
            "obstacle_mm": (None if self.obstacle_mm is None
                            else round(self.obstacle_mm, 4)),
        }


@dataclass(frozen=True)
class EdgeAttempt:
    """The qualifying history for one edge, reduced to what the taxonomy needs."""

    records: int = 0
    runs: tuple[str, ...] = ()
    outcomes: tuple[tuple[str, int], ...] = ()
    reasons: tuple[tuple[str, int], ...] = ()
    refused_classes: tuple[tuple[str, int], ...] = ()
    refusal_geometry: tuple[tuple[str, float, float, int], ...] = ()
    closed_before_refusal: bool = False
    #: Records whose plan was a via hop (``via_free`` / ``via_jog`` candidate).
    via_plans: int = 0
    #: The driver's own via search for this exact pair: how many candidate spots
    #: it probed for a continuation and how many proved one. Per-connection
    #: evidence from ``metrics["via_search"]``, not a run-level aggregate.
    via_checks: int = 0
    via_proved: int = 0
    via_searches: int = 0
    #: Run-level verdicts the driver recorded for this pair's unprovable anchors.
    anchor_verdicts: tuple[str, ...] = ()
    #: Run-level scan omissions, so an unattempted edge can be separated from an
    #: edge a bounded scan simply never offered this generation.
    cap_evidence: tuple[tuple[str, int], ...] = ()
    #: Substitution offers the campaign made on this board. Board-level, not
    #: edge-level by construction: a substituted offer replaces the offered
    #: edge's geometry. It is attributed to an edge when the record carries the
    #: ``offered_pair_key`` the runner now writes (or the component identity the
    #: offer stood for); a record from a run that predates that field cannot be
    #: attributed and is counted instead of guessed at.
    substitution_records: int = 0
    #: Which plan step stopped each record's transaction, as ``(kind, count)``.
    #: Read straight off the record's ``failed_step_kind``. ``""`` is reported
    #: under ``no_failed_step_recorded`` and means exactly that: every step
    #: succeeded, or the checkpoint predates the field. The two are separated by
    #: the run's provenance, never by guessing from this counter — so this is a
    #: statement about the evidence, not about where plans fail in general.
    failed_step_kinds: tuple[tuple[str, int], ...] = ()

    @property
    def attempted(self) -> bool:
        return self.records > 0

    @property
    def drc_refused(self) -> bool:
        return bool(self.refused_classes) or self.closed_before_refusal

    @property
    def via_search_ran_without_continuation(self) -> bool:
        return self.via_checks > 0 and self.via_proved == 0

    @property
    def cap_evidence_present(self) -> bool:
        return any(count > 0 for _name, count in self.cap_evidence)

    def to_private(self) -> dict[str, Any]:
        return {
            "records": self.records,
            "runs": list(self.runs),
            "outcomes": [list(item) for item in self.outcomes],
            "reasons": [list(item) for item in self.reasons],
            "refused_classes": [list(item) for item in self.refused_classes],
            "refusal_geometry": [list(item) for item in self.refusal_geometry],
            "closed_before_refusal": self.closed_before_refusal,
            "via_plans": self.via_plans,
            "via_checks": self.via_checks,
            "via_proved": self.via_proved,
            "via_searches": self.via_searches,
            "anchor_verdicts": list(self.anchor_verdicts),
            "cap_evidence": [list(item) for item in self.cap_evidence],
            "substitution_records": self.substitution_records,
            "failed_step_kinds": [list(item) for item in self.failed_step_kinds],
        }


@dataclass(frozen=True)
class EdgeTaxonomy:
    fact: EdgeFact
    attempt: EdgeAttempt
    category: str
    detail: dict[str, Any] = field(default_factory=dict)

    def to_private(self) -> dict[str, Any]:
        return {
            **self.fact.to_private(),
            "attempt": self.attempt.to_private(),
            "attempted": self.attempt.attempted,
            "category": self.category,
            "category_meaning": CATEGORY_MEANING.get(self.category, ""),
            "detail": self.detail,
        }

    def to_public(self) -> dict[str, Any]:
        """Entry with no net identity, no coordinates and no refusal position."""
        return {
            "index": self.fact.index,
            "category": self.category,
            "attempted": self.attempt.attempted,
            "pair_kind": self.fact.pair_kind,
            "layer_relation": self.fact.layer_relation,
            "gap_class": gap_class(self.fact.gap_mm),
            "anchor_absent": self.fact.anchor_absent,
            "obstacle_present": self.fact.obstacle_mm is not None,
            "refused_classes": [item[0] for item in self.attempt.refused_classes],
        }


#: Coarse buckets for the public view. A class, not a measurement: publishing the
#: exact gap plus a net's identity is a layout disclosure, a bucket is not.
GAP_CLASS_EDGES: tuple[tuple[float, str], ...] = (
    (0.000001, "coincident"),
    (1.0, "under_1mm"),
    (5.0, "1_to_5mm"),
    (20.0, "5_to_20mm"),
    (math.inf, "over_20mm"),
)


def gap_class(gap_mm: float) -> str:
    for ceiling, name in GAP_CLASS_EDGES:
        if gap_mm <= ceiling:
            return name
    return "over_20mm"


class ForeignCopperIndex:
    """Bucketed foreign-net copper, for a bounded nearest-obstacle query.

    A per-edge scan of the whole board inventory is O(edges x items); on a board
    with thousands of tracks that is the slowest part of a taxonomy run by a wide
    margin. Items are bucketed into a square grid once and a query only visits the
    buckets its radius can reach. The result is the same number the full scan
    would return: the minimum distance to a foreign item within the radius, or
    ``None``.
    """

    def __init__(self, engine: Any, resolver: LayerResolver, *, bucket_mm: float = 2.0
                 ) -> None:
        self.bucket_mm = max(0.25, float(bucket_mm))
        self._segments: dict[tuple[int, int], list[tuple[int, float, float, float, float]]] = {}
        self._points: dict[tuple[int, int], list[tuple[int, float, float]]] = {}
        for track in engine.get_tracks():
            net = int(track.net_code)
            if net <= 0:
                continue
            self._add_segment(net, float(track.x1_mm), float(track.y1_mm),
                              float(track.x2_mm), float(track.y2_mm))
        for via in engine.get_vias():
            net = int(via.net_code)
            if net <= 0:
                continue
            self._add_point(net, float(via.x_mm), float(via.y_mm))
        for pad in engine.get_pads():
            net = int(pad.net_code)
            if net <= 0:
                continue
            self._add_point(net, float(pad.x_mm), float(pad.y_mm))

    def _buckets_for_segment(self, x0: float, y0: float, x1: float, y1: float
                             ) -> Iterable[tuple[int, int]]:
        lo_x = min(x0, x1); hi_x = max(x0, x1)
        lo_y = min(y0, y1); hi_y = max(y0, y1)
        for bx in range(int(math.floor(lo_x / self.bucket_mm)),
                        int(math.floor(hi_x / self.bucket_mm)) + 1):
            for by in range(int(math.floor(lo_y / self.bucket_mm)),
                            int(math.floor(hi_y / self.bucket_mm)) + 1):
                yield (bx, by)

    def _add_segment(self, net: int, x0: float, y0: float, x1: float, y1: float) -> None:
        for key in self._buckets_for_segment(x0, y0, x1, y1):
            self._segments.setdefault(key, []).append((net, x0, y0, x1, y1))

    def _add_point(self, net: int, x: float, y: float) -> None:
        key = (int(math.floor(x / self.bucket_mm)), int(math.floor(y / self.bucket_mm)))
        self._points.setdefault(key, []).append((net, x, y))

    def nearest_mm(self, x_mm: float, y_mm: float, net_code: int, radius_mm: float
                   ) -> float | None:
        """Distance to the nearest foreign-net item within ``radius_mm``, or None."""
        radius = float(radius_mm)
        if radius <= 0.0:
            return None
        span = int(math.ceil(radius / self.bucket_mm))
        base_x = int(math.floor(x_mm / self.bucket_mm))
        base_y = int(math.floor(y_mm / self.bucket_mm))
        best: float | None = None
        for bx in range(base_x - span, base_x + span + 1):
            for by in range(base_y - span, base_y + span + 1):
                for net, px, py in self._points.get((bx, by), ()):
                    if net == int(net_code):
                        continue
                    distance = math.hypot(x_mm - px, y_mm - py)
                    if distance <= radius and (best is None or distance < best):
                        best = distance
                for net, x0, y0, x1, y1 in self._segments.get((bx, by), ()):
                    if net == int(net_code):
                        continue
                    distance = _point_segment_distance(x_mm, y_mm, x0, y0, x1, y1)
                    if distance <= radius and (best is None or distance < best):
                        best = distance
        return best


def _point_segment_distance(px: float, py: float, x0: float, y0: float,
                            x1: float, y1: float) -> float:
    dx, dy = x1 - x0, y1 - y0
    if dx == 0.0 and dy == 0.0:
        return math.hypot(px - x0, py - y0)
    t = max(0.0, min(1.0, ((px - x0) * dx + (py - y0) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - (x0 + t * dx), py - (y0 + t * dy))


def load_history(history_paths: Sequence[str | Path]
                 ) -> tuple[list[dict[str, Any]], dict, dict]:
    """Raw attempt records, what each file contributed, and its run-level facts.

    The records are returned as they were serialised; filtering by board digest
    happens per edge so the report can say how many records were dropped as
    measured on other copper. Run-level facts (the driver's own via search and
    the last scan's omission counters) are kept separately because they are
    aggregate: they label the evidence, they do not prove a claim about one edge.
    """
    records: list[dict[str, Any]] = []
    provenance: dict[str, Any] = {"runs": []}
    run_meta: dict[str, dict[str, Any]] = {}
    for path in history_paths:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        run_records = payload.get("attempts") or []
        if not isinstance(run_records, list):
            run_records = []
        metrics = payload.get("metrics") or {}
        transplant = metrics.get("transplant") or {}
        digest = transplant.get("digest")
        via_search = metrics.get("via_search") or {}
        last_scan = metrics.get("last_scan") or {}
        coverage = last_scan.get("component_coverage") or {}
        cap = {
            "skipped_net_cap": int(last_scan.get("skipped_net_cap", 0) or 0),
            "dropped_by_max_pairs": int(
                last_scan.get("dropped_by_max_pairs", 0) or 0),
            "components_omitted_by_window": int(
                coverage.get("components_omitted_by_window", 0) or 0),
            "pairs_omitted_by_cap": int(coverage.get("pairs_omitted_by_cap", 0) or 0),
        }
        # The driver keys its via search by the pair it searched, so this is
        # per-connection evidence and not only a run-level aggregate.
        via_by_pair: dict[tuple, dict[str, int]] = {}
        for raw_key, entry in (via_search or {}).items():
            if not isinstance(entry, Mapping):
                continue
            try:
                parsed = json.loads(raw_key)
            except (TypeError, ValueError):
                continue
            if not isinstance(parsed, list) or len(parsed) < 7:
                continue
            via_by_pair[canonical_edge_key(parsed)] = {
                "checks": int(entry.get("continuation_checks", 0) or 0),
                "proved": int(entry.get("continuation_proved", 0) or 0),
            }
        unanchorable: dict[tuple, tuple[str, ...]] = {}
        for raw_key, rows in (metrics.get("unanchorable_anchors") or {}).items():
            try:
                parsed = json.loads(raw_key)
            except (TypeError, ValueError):
                continue
            if not isinstance(parsed, list) or len(parsed) < 7:
                continue
            verdicts = tuple(
                " ".join(str(row.get("verdict", "")).split())
                for row in (rows or ()) if isinstance(row, Mapping)
            )
            unanchorable[canonical_edge_key(parsed)] = verdicts
        # ``reanchored_pairs`` is keyed by the pair that was *attempted* and
        # carries the ``offered_pair_key`` it replaced. That is the only exact
        # link from an edge whose anchor carries no copper to the attempt made on
        # its behalf - proximity between the two geometries would be a guess.
        reanchored_by_offered: dict[tuple, dict[str, Any]] = {}
        reanchored_by_attempted: dict[tuple, tuple] = {}
        for raw_key, rows in (metrics.get("reanchored_pairs") or {}).items():
            if not isinstance(rows, Mapping):
                continue
            offered = rows.get("offered_pair_key")
            attempted = rows.get("attempted_pair_key")
            if not (isinstance(offered, list) and isinstance(attempted, list)):
                continue
            if len(offered) < 7 or len(attempted) < 7:
                continue
            offered_key = canonical_edge_key(offered)
            attempted_key = canonical_edge_key(attempted)
            evidence = {
                "attempted_key": attempted_key,
                "moved_mm": float(rows.get("moved_mm", 0.0) or 0.0),
                "proof": str(rows.get("proof", "")),
                "component_candidates": len(rows.get("component_candidates") or ()),
            }
            reanchored_by_offered[offered_key] = evidence
            reanchored_by_attempted[attempted_key] = (offered_key, evidence)
        meta = {
            "path": str(path),
            "records": len(run_records),
            "board_digest": digest,
            "stop_reason": payload.get("stop_reason"),
            "best_board_sha256": payload.get("best_board_sha256"),
            "via_search_pairs": len(via_by_pair),
            "via_by_pair": via_by_pair,
            "unanchorable": unanchorable,
            "reanchored_by_offered": reanchored_by_offered,
            "reanchored_by_attempted": reanchored_by_attempted,
            "scan_omissions": cap,
            "omit_total": sum(cap.values()),
            "component_window": metrics.get("component_window"),
        }
        # The per-pair lookup maps stay out of the report: they are indexed by a
        # tuple key and would leak a net name into the public view. Only their
        # shape is published.
        provenance["runs"].append({
            key: value for key, value in meta.items()
            if key not in ("via_by_pair", "unanchorable",
                           "reanchored_by_offered", "reanchored_by_attempted")
        } | {
            "via_search_pairs": len(via_by_pair),
            "unanchorable_pairs": len(unanchorable),
            "reanchored_pairs": len(reanchored_by_attempted),
        })
        run_meta[str(path)] = meta
        for record in run_records:
            if isinstance(record, Mapping):
                records.append({**record, "_history_run": str(path)})
    provenance["records_total"] = len(records)
    return records, provenance, run_meta


#: Candidate kinds that make the plan a *via hop* rather than a same-layer
#: corridor. Taken from ``generate_candidates``; a plan the runner labelled with
#: one of these is the only per-edge evidence that a via was even on the table.
VIA_PLAN_KINDS: frozenset[str] = frozenset({"via_free", "via_jog"})


def _record_is_via_plan(record: Mapping[str, Any]) -> bool:
    if str(record.get("kind") or "") in VIA_PLAN_KINDS:
        return True
    name = str(record.get("candidate") or "")
    return name.startswith("via_free_") or name.startswith("via_jog_")


def join_attempts(edge_keys: Sequence[tuple], edge_components: Sequence[frozenset[str]],
                  records: Sequence[Mapping[str, Any]], digest: str | None,
                  run_meta: Mapping[str, Mapping[str, Any]] | None = None,
                  ) -> tuple[list[EdgeAttempt], dict[str, int]]:
    """Attribute qualifying history to edges by offered key, pair key or components.

    A record qualifies only when its ``board_digest`` is the generation being
    taxonomised (or was not recorded at all, which is treated as "unknown board"
    and excluded from attribution - unknown is not evidence). A record is then
    attributed, in order of strength: its own pair key (the geometry attempted),
    the offered pair key it replaced (the exact link a substitution carries), or
    the native component identities the offer stood for. Records that match none
    are counted as unmatched rather than assigned to a nearby edge.
    """
    stats = {"records_total": len(records), "records_used": 0,
             "records_other_digest": 0, "records_unmatched": 0,
             "records_by_offered_key": 0,
             "substitution_records_bound": 0,
             "substitution_records_attributed": 0,
             "substitution_records_unattributed": 0}
    by_key: dict[tuple, list[dict[str, Any]]] = {}
    by_components: dict[frozenset[str], list[dict[str, Any]]] = {}
    for record in records:
        if digest is not None and record.get("board_digest") not in (None, digest):
            stats["records_other_digest"] += 1
            continue
        if record.get("board_digest") is None:
            stats["records_other_digest"] += 1
            continue
        target: list[dict[str, Any]] | None = None
        is_substitution = bool(
            record.get("offer_source") == "substitution"
            or record.get("substituted")
        )
        if is_substitution:
            stats["substitution_records_bound"] += 1
        raw_key = record.get("pair_key") or []
        if len(raw_key) >= 7:
            key = canonical_edge_key(raw_key)
            if key in edge_keys:
                target = by_key.setdefault(key, [])
        if target is None:
            # The record's own pair key did not name an outstanding edge: this is
            # the substitution case, where ``pair_key`` is the geometry actually
            # attempted and not the edge the scan offered. ``offered_pair_key`` is
            # the exact link back to the offered edge, so it is tried *before* the
            # component-identity fallback - a key is a stronger statement than
            # membership, which two different offered edges can share.
            offered_raw = record.get("offered_pair_key") or []
            if len(offered_raw) >= 7:
                offered_key = canonical_edge_key(offered_raw)
                if offered_key in edge_keys:
                    target = by_key.setdefault(offered_key, [])
                    stats["records_by_offered_key"] += 1
        if target is None:
            # No offered-key link (a run that predates the field, or an offer that
            # never had one). Fall back to the native component identities the
            # offer stood for, which do not move when the anchor does.
            components = _canonical_components(
                str(record.get("component_start") or ""),
                str(record.get("component_target") or ""),
            )
            if len(components) == 2 and components in edge_components:
                target = by_components.setdefault(components, [])
        if target is None:
            stats["records_unmatched"] += 1
            if is_substitution:
                stats["substitution_records_unattributed"] += 1
            continue
        if is_substitution:
            stats["substitution_records_attributed"] += 1
        target.append(record)
        stats["records_used"] += 1

    run_meta = run_meta or {}
    attempts: list[EdgeAttempt] = []
    # A substituted offer's record is filed under the substituted geometry. The
    # runner records the offered key separately when it re-anchors mid-attempt, so
    # that alias is used when it exists; otherwise the record cannot be attributed
    # to one edge and stays out of the per-edge counts.
    alias: dict[tuple, list[tuple]] = {}
    for meta in run_meta.values():
        if digest is not None and meta.get("board_digest") not in (None, digest):
            continue
        for offered_key, evidence in (meta.get("reanchored_by_offered") or {}).items():
            alias.setdefault(offered_key, []).append(
                (canonical_edge_key(evidence["attempted_key"]), evidence))
        for attempted_key, (offered_key, evidence) in (
                meta.get("reanchored_by_attempted") or {}).items():
            by_key.setdefault(offered_key, [])
            records_by_attempted = by_key.get(attempted_key, [])
            if records_by_attempted:
                by_key[offered_key].extend(records_by_attempted)
    global_cap: dict[str, int] = {}
    substituted = 0
    for meta in run_meta.values():
        if digest is not None and meta.get("board_digest") not in (None, digest):
            continue
        for name, value in (meta.get("scan_omissions") or {}).items():
            global_cap[name] = global_cap.get(name, 0) + int(value or 0)
    for index, key in enumerate(edge_keys):
        matched = list(by_key.get(key, ()))
        components = edge_components[index]
        if len(components) == 2:
            for record in by_components.get(components, ()):
                if record not in matched:
                    matched.append(record)
        attempts.append(_reduce_attempts(matched, run_meta, key, global_cap))
    for record in records:
        if record.get("offer_source") == "substitution" or record.get("substituted"):
            substituted += 1
    stats["substitution_records"] = substituted
    stats["cap_evidence"] = global_cap
    return attempts, stats


def _reduce_attempts(records: Sequence[Mapping[str, Any]],
                     run_meta: Mapping[str, Mapping[str, Any]] | None = None,
                     edge_key: tuple | None = None,
                     global_cap: Mapping[str, int] | None = None,
                     ) -> EdgeAttempt:
    run_meta = run_meta or {}
    global_cap = global_cap or {}
    outcomes: dict[str, int] = {}
    reasons: dict[str, int] = {}
    classes: dict[str, int] = {}
    geometry: list[tuple[str, float, float, int]] = []
    runs: list[str] = []
    closed = False
    via_plans = 0
    via_checks = via_proved = via_searches = 0
    anchor_verdicts: list[str] = []
    cap_evidence: dict[str, int] = {}
    substitution_records = 0
    failed_steps: dict[str, int] = {}
    seen_runs: dict[str, dict[str, Any]] = {}
    for record in records:
        outcome = str(record.get("outcome") or "")
        outcomes[outcome] = outcomes.get(outcome, 0) + 1
        reason = " ".join(str(record.get("reason") or "").split())
        if reason:
            reasons[reason] = reasons.get(reason, 0) + 1
        for item in record.get("drc_classes") or ():
            if len(item) == 2:
                classes[str(item[0])] = classes.get(str(item[0]), 0) + int(item[1])
        for row in record.get("drc_hints") or ():
            if len(row) == 4:
                geometry.append((str(row[0]), float(row[1]), float(row[2]),
                                 int(row[3])))
        closed = closed or bool(record.get("closed_before_refusal"))
        if _record_is_via_plan(record):
            via_plans += 1
        if record.get("offer_source") == "substitution" or record.get("substituted"):
            substitution_records += 1
        # A record that predates the field carries no key at all and is counted
        # with the ones whose every step succeeded; both are "no failed step
        # recorded", which is what the counter says.
        step = str(record.get("failed_step_kind") or "")
        name = step or NO_FAILED_STEP_RECORDED
        failed_steps[name] = failed_steps.get(name, 0) + 1
        run = str(record.get("_history_run") or "")
        if run and run not in runs:
            runs.append(run)
        if run:
            seen_runs.setdefault(run, run_meta.get(run) or {})
    # Run-level evidence is attributed per *run that measured this edge*, so a run
    # whose scan omitted pairs cannot lend its omission counter to a pair it did
    # attempt.
    for meta in seen_runs.values():
        search = (meta.get("via_by_pair") or {}).get(edge_key) or {}
        if int(search.get("checks", 0) or 0) > 0:
            via_searches += 1
            via_checks += int(search["checks"])
            via_proved += int(search.get("proved", 0) or 0)
        for verdict in (meta.get("unanchorable") or {}).get(edge_key, ()):
            if verdict and verdict not in anchor_verdicts:
                anchor_verdicts.append(verdict)
        for name, value in (meta.get("scan_omissions") or {}).items():
            cap_evidence[name] = cap_evidence.get(name, 0) + int(value or 0)
    if not records:
        # An edge nobody measured still inherits the *scan* evidence of the runs
        # that were looking at this board: whether their bounded scan withheld
        # pairs at all. This is run-level and is labelled as such in the report.
        cap_evidence = dict(global_cap)
        substitution_records = 0
    return EdgeAttempt(
        records=len(records),
        runs=tuple(runs),
        outcomes=tuple(sorted(outcomes.items())),
        reasons=tuple(sorted(reasons.items())),
        refused_classes=tuple(sorted(classes.items())),
        refusal_geometry=tuple(sorted(set(geometry))[:16]),
        closed_before_refusal=closed,
        via_plans=via_plans,
        via_checks=via_checks,
        via_proved=via_proved,
        via_searches=via_searches,
        anchor_verdicts=tuple(anchor_verdicts[:4]),
        cap_evidence=tuple(sorted(cap_evidence.items())),
        substitution_records=substitution_records,
        failed_step_kinds=tuple(sorted(failed_steps.items())),
    )


def _touches_via_plan(attempt: EdgeAttempt) -> bool:
    """True when the recorded plans were about a via hop rather than a corridor.

    Both halves are required: the pair's own plans must have offered a via hop
    (kind ``via_free`` / ``via_jog``), and a run that measured the pair must have
    searched for continuations without proving one. Either alone describes a
    different situation - a via plan is not a failure, and a run-level search
    failure is not this pair's.
    """
    return attempt.via_plans > 0 and attempt.via_search_ran_without_continuation


def classify_edge(fact: EdgeFact, attempt: EdgeAttempt) -> tuple[str, dict[str, Any]]:
    """Exactly one category for one edge, by precedence.

    The order is deliberate: a stale edge is stale whatever the history says, and
    an observable board fact outranks an inference from a refusal. Nothing here
    concludes that a connection cannot be routed; the strongest claim is that the
    plans evaluated for it did not close it.
    """
    if fact.already_connected:
        return CATEGORY_ALREADY_CONNECTED, {}
    if fact.pair_kind == "coincident_same_layer":
        return CATEGORY_UNROUTABLE_DEGENERATE, {"pair_kind": fact.pair_kind}
    if fact.layer_error:
        return CATEGORY_LAYER_UNRESOLVABLE, {"layer_error": fact.layer_error}
    if fact.anchor_state == "copper_absent":
        return CATEGORY_COPPER_ABSENT_ANCHOR, {
            "observed_nets": [fact.start_net_at, fact.target_net_at],
            "cluster_items": [fact.start_cluster_items, fact.target_cluster_items],
            "anchor_verdicts": list(attempt.anchor_verdicts),
            "substitution_records": attempt.substitution_records,
        }
    if fact.anchor_state == "copper_unnamed":
        return CATEGORY_UNNAMED_ANCHOR, {
            "observed_nets": [fact.start_net_at, fact.target_net_at],
            "cluster_items": [fact.start_cluster_items, fact.target_cluster_items],
            "anchor_verdicts": list(attempt.anchor_verdicts),
            "substitution_records": attempt.substitution_records,
        }
    if fact.components_on_net >= 2 and not fact.component_start \
            and not fact.component_target:
        return CATEGORY_INSUFFICIENT_COMPONENT_ANCHORS, {
            "components_on_net": fact.components_on_net,
            "component_pairs_on_net": fact.component_pairs_on_net,
        }
    if attempt.drc_refused:
        return CATEGORY_DRC_REGRESSION, {
            "classes": [list(item) for item in attempt.refused_classes],
            "geometry": [list(item) for item in attempt.refusal_geometry],
            "closed_before_refusal": attempt.closed_before_refusal,
        }
    if _touches_via_plan(attempt):
        return CATEGORY_VIA_NO_CONTINUATION, {
            "via_plans": attempt.via_plans,
            "via_checks": attempt.via_checks,
            "via_proved": attempt.via_proved,
            "via_searches": attempt.via_searches,
        }
    if attempt.attempted:
        not_verified = any(
            "connection_not_verified" in reason for reason, _count in attempt.reasons
        )
        if not_verified:
            # Where the plans stopped, as step-kind counts. This is the one piece
            # of "where did it fail" evidence a store-and-read analysis can have
            # without replaying the plan against a harness that may have changed.
            return CATEGORY_CONNECTION_NOT_VERIFIED, {
                "via_plans": attempt.via_plans,
                "failed_step_kinds": [list(item) for item in attempt.failed_step_kinds],
            }
        return CATEGORY_PLANS_EXHAUSTED, {
            "outcomes": [list(item) for item in attempt.outcomes],
            "reasons": [item[0] for item in attempt.reasons][:4],
        }
    if attempt.cap_evidence_present:
        return CATEGORY_UNATTEMPTED_CAP_OBSERVED, {
            "cap_evidence": [list(item) for item in attempt.cap_evidence],
        }
    return CATEGORY_UNATTEMPTED, {}


def observe_edges(session: Any, *, resolver: LayerResolver | None = None,
                  index: CopperAnchorIndex | None = None,
                  obstacle_radius_mm: float = 2.0,
                  include_obstacles: bool = True) -> tuple[list[EdgeFact], dict[str, Any]]:
    """One immutable observation pass over every ratsnest edge."""
    engine = session._engine
    resolver = resolver or LayerResolver.build(session)
    index = index or CopperAnchorIndex.build(session, resolver=resolver)
    net_names = engine.get_net_names()
    groups = engine.get_pad_groups()
    edges = list(engine.get_ratsnest())

    components = native_components(session, resolver=resolver,
                                   max_anchors_per_component=100000)
    component_count = {int(net): len(items) for net, items in components.items()}
    anchor_owner: dict[tuple[float, float, int], str] = {}
    for items in components.values():
        for component in items:
            for anchor in component.anchors:
                anchor_owner.setdefault(_round_anchor(anchor), component.component_id)

    obstacles = (ForeignCopperIndex(engine, resolver) if include_obstacles else None)

    def component_at(point: tuple[float, float, int]) -> tuple[str, frozenset]:
        try:
            cluster = session.cluster(point[0], point[1], int(point[2]))
        except Exception:               # noqa: BLE001 - unreadable is not proof
            return "", frozenset()
        for key in cluster:
            owner = anchor_owner.get(_round_anchor(key))
            if owner:
                return owner, cluster
        return "", cluster

    facts: list[EdgeFact] = []
    layer_errors: list[str] = []
    obstacle_queries = 0
    for order, edge in enumerate(edges):
        net = int(edge.net_code)
        gap = math.hypot(float(edge.x1_mm) - float(edge.x2_mm),
                         float(edge.y1_mm) - float(edge.y2_mm))
        layer_error: str | None = None
        start_layer = target_layer = 0
        try:
            start_layer = _anchor_layer(
                session, float(edge.x1_mm), float(edge.y1_mm), int(edge.layer1),
                resolver, other_layer=resolver.human(int(edge.layer2)), net_code=net)
            target_layer = _anchor_layer(
                session, float(edge.x2_mm), float(edge.y2_mm), int(edge.layer2),
                resolver, other_layer=start_layer, net_code=net)
        except LayerConventionError as exc:
            layer_error = str(exc)
            layer_errors.append(str(exc))

        start = (float(edge.x1_mm), float(edge.y1_mm), int(start_layer))
        target = (float(edge.x2_mm), float(edge.y2_mm), int(target_layer))
        if layer_error:
            pair_kind = "unresolvable"
        elif gap <= COINCIDENT_EPS_MM:
            pair_kind = ("coincident_same_layer" if start_layer == target_layer
                         else "coincident_cross_layer")
        else:
            pair_kind = "line"

        start_net = target_net = None
        component_start = component_target = ""
        start_cluster: frozenset = frozenset()
        target_cluster: frozenset = frozenset()
        if not layer_error:
            try:
                start_net = session.net_at(start[0], start[1], start[2])
                target_net = session.net_at(target[0], target[1], target[2])
            except Exception:           # noqa: BLE001
                start_net = target_net = None
            component_start, start_cluster = component_at(start)
            component_target, target_cluster = component_at(target)
        already = bool(start_cluster and target_cluster
                       and (start_cluster & target_cluster))
        anchor_absent = (
            not layer_error and (start_net == 0 or target_net == 0)
        )

        obstacle_mm: float | None = None
        if obstacles is not None and not layer_error and obstacle_radius_mm > 0:
            mid = ((start[0] + target[0]) / 2.0, (start[1] + target[1]) / 2.0)
            radius = max(float(obstacle_radius_mm), min(gap / 2.0, 5.0))
            obstacle_queries += 1
            try:
                obstacle_mm = obstacles.nearest_mm(mid[0], mid[1], net, radius)
            except Exception:           # noqa: BLE001
                obstacle_mm = None

        count = int(component_count.get(net, 0))
        facts.append(EdgeFact(
            index=order,
            key=canonical_edge_key((
                net, start[0], start[1], start[2], target[0], target[1], target[2],
            )),
            net_code=net,
            net_name=str(net_names.get(net, f"NET{net}")),
            start=start,
            target=target,
            gap_mm=gap,
            pair_kind=pair_kind,
            pad_groups_on_net=int(groups.get(net, 0)),
            components_on_net=count,
            component_pairs_on_net=max(0, count * (count - 1) // 2),
            start_net_at=start_net,
            target_net_at=target_net,
            start_cluster_items=len(start_cluster),
            target_cluster_items=len(target_cluster),
            anchor_absent=anchor_absent,
            already_connected=already,
            component_start=component_start,
            component_target=component_target,
            layer_error=layer_error,
            obstacle_mm=obstacle_mm,
        ))

    coverage = {
        "edges": len(edges),
        "obstacle_queries": obstacle_queries,
        "layer_errors": len(layer_errors),
        "components_by_net": {
            str(net): count for net, count in sorted(component_count.items()) if count > 1
        },
        "nets_with_two_or_more_components": sum(
            1 for count in component_count.values() if count > 1
        ),
        "component_pairs_total": sum(
            max(0, count * (count - 1) // 2) for count in component_count.values()
        ),
        "nets_with_pad_groups": len(groups),
        "pad_group_total": sum(int(value) for value in groups.values()),
        "nets_with_two_or_more_pad_groups": sum(
            1 for value in groups.values() if int(value) > 1
        ),
    }
    return facts, coverage


def _histogram(values: Iterable[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items()))


def _cross_tab(entries: Sequence[EdgeTaxonomy], key: Any) -> dict[str, int]:
    counts: dict[str, int] = {}
    for entry in entries:
        bucket = str(key(entry))
        counts[bucket] = counts.get(bucket, 0) + 1
    return dict(sorted(counts.items()))


def build_taxonomy(board: str | Path, *, history_paths: Sequence[str | Path] = (),
                   include_obstacles: bool = True,
                   obstacle_radius_mm: float = 2.0,
                   session_factory: Any = None) -> dict[str, Any]:
    """Full private taxonomy for one board generation.

    ``session_factory`` lets a test inject an engine without a native build; the
    default opens the pinned router over ``board``.
    """
    board_path = Path(board)
    close = None
    if session_factory is not None:
        session = session_factory(str(board_path))
    else:
        from pcb_world.agent.session import AgentSession
        from pcb_world.engine import KiCadEngine

        engine = KiCadEngine(str(board_path))
        close = engine.close
        session = AgentSession(engine, board_path=str(board_path))
        engine.build_connectivity()
    try:
        digest = session.board_digest()
        facts, coverage = observe_edges(
            session, include_obstacles=include_obstacles,
            obstacle_radius_mm=obstacle_radius_mm)
    finally:
        if close is not None:
            close()

    records, provenance, run_meta = load_history(history_paths)
    attempts, join_stats = join_attempts(
        [fact.key for fact in facts],
        [fact.component_pair for fact in facts],
        records,
        digest,
        run_meta,
    )
    entries = [
        EdgeTaxonomy(fact=fact, attempt=attempt,
                     category=category, detail=detail)
        for fact, attempt, (category, detail) in (
            (fact, attempt, classify_edge(fact, attempt))
            for fact, attempt in zip(facts, attempts)
        )
    ]
    board_sha = _sha256(board_path) if board_path.is_file() else None
    report = {
        "taxonomy_version": TAXONOMY_VERSION,
        "board_path": str(board_path),
        "board_sha256": board_sha,
        "board_digest": digest,
        "inputs": {
            "board_sha256": board_sha,
            "project_sha256": _sidecar_sha256(board_path, ".kicad_pro"),
            "rules_sha256": _sidecar_sha256(board_path, ".kicad_dru"),
        },
        "coverage": coverage,
        "history": {**provenance, **join_stats},
        "categories": _histogram(entry.category for entry in entries),
        "attempted": _cross_tab(entries, lambda item: item.attempt.attempted),
        "anchor_state": _cross_tab(entries, lambda item: item.fact.anchor_state),
        "category_by_anchor_state": _cross_tab(
            entries, lambda item: f"{item.category}|{item.fact.anchor_state}"),
        "layer_relation": _cross_tab(entries, lambda item: item.fact.layer_relation),
        "gap_class": _cross_tab(entries, lambda item: gap_class(item.fact.gap_mm)),
        "pair_kind": _cross_tab(entries, lambda item: item.fact.pair_kind),
        "obstacle_present": _cross_tab(
            entries, lambda item: item.fact.obstacle_mm is not None),
        # Board-wide: which plan step stopped each recorded attempt, summed over
        # every edge. Step kinds are harness vocabulary, so the aggregate is
        # publishable on its own the way the category totals are.
        "failed_step_kinds": _sum_counts(
            attempt.failed_step_kinds for attempt in attempts),
        "edges": [entry.to_private() for entry in entries],
    }
    # A category total that does not sum to the edge count is a taxonomy bug, not
    # a rounding artefact: fail loudly rather than publish a partial count.
    total = sum(report["categories"].values())
    if total != len(entries):
        raise AssertionError(
            f"taxonomy categories sum to {total}, not {len(entries)} edges"
        )
    return report


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sidecar_sha256(board_path: Path, suffix: str) -> str | None:
    sidecar = board_path.with_suffix(suffix)
    return _sha256(sidecar) if sidecar.is_file() else None


def public_summary(report: Mapping[str, Any]) -> dict[str, Any]:
    """Aggregate-only view: counts, classes and hashes; no net, no coordinates."""
    coverage = dict(report.get("coverage") or {})
    coverage.pop("components_by_net", None)
    history = dict(report.get("history") or {})
    history["runs"] = [
        {
            "stop_reason": run.get("stop_reason"),
            "records": run.get("records"),
        }
        for run in (history.get("runs") or [])
    ]
    categories = dict(report.get("categories") or {})
    return {
        "taxonomy_version": report.get("taxonomy_version"),
        "board_sha256": report.get("board_sha256"),
        "board_digest": report.get("board_digest"),
        "inputs": dict(report.get("inputs") or {}),
        "coverage": coverage,
        "history": history,
        "categories": categories,
        "category_meaning": {
            name: CATEGORY_MEANING.get(name, "") for name in categories
        },
        "attempted": dict(report.get("attempted") or {}),
        "anchor_state": dict(report.get("anchor_state") or {}),
        "category_by_anchor_state": dict(
            report.get("category_by_anchor_state") or {}),
        "layer_relation": dict(report.get("layer_relation") or {}),
        "gap_class": dict(report.get("gap_class") or {}),
        "pair_kind": dict(report.get("pair_kind") or {}),
        "obstacle_present": dict(report.get("obstacle_present") or {}),
        "failed_step_kinds": dict(report.get("failed_step_kinds") or {}),
        # Counts per category only - the refusal classes are a property of the
        # board's rules, and the positions that go with them stay private.
        "drc_refusal_classes": _category_class_counts(report),
    }


def _category_class_counts(report: Mapping[str, Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for entry in report.get("edges") or ():
        for name, value in entry.get("attempt", {}).get("refused_classes", ()):
            counts[str(name)] = counts.get(str(name), 0) + int(value)
    return dict(sorted(counts.items()))


def _sum_counts(groups: Iterable[Sequence[tuple[str, int]]]) -> dict[str, int]:
    """Total one ``(name, count)`` histogram per edge into one board histogram."""
    totals: dict[str, int] = {}
    for group in groups:
        for name, value in group:
            totals[str(name)] = totals.get(str(name), 0) + int(value)
    return dict(sorted(totals.items()))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--board", required=True)
    parser.add_argument("--history", action="append", default=[],
                        help="run_state.json whose attempt records may be joined")
    parser.add_argument("--out", required=True)
    parser.add_argument("--public-summary", default=None)
    parser.add_argument("--no-obstacles", action="store_true")
    parser.add_argument("--obstacle-radius-mm", type=float, default=2.0)
    args = parser.parse_args(argv)

    report = build_taxonomy(
        args.board, history_paths=args.history,
        include_obstacles=not args.no_obstacles,
        obstacle_radius_mm=args.obstacle_radius_mm,
    )
    Path(args.out).write_text(json.dumps(report, indent=2, sort_keys=True) + "\n",
                              encoding="utf-8")
    summary = public_summary(report)
    if args.public_summary:
        Path(args.public_summary).write_text(
            json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({
        "board_sha256": report["board_sha256"],
        "board_digest": report["board_digest"],
        "edges": report["coverage"]["edges"],
        "component_pairs_total": report["coverage"]["component_pairs_total"],
        "categories": report["categories"],
        "attempted": report["attempted"],
        "history": report["history"],
    }, indent=2))
    return 0


if __name__ == "__main__":
    os.environ.setdefault("KICAD_ENGINE_REUSE", "0")
    raise SystemExit(main())
