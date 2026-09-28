"""Engine-IPC wire schema — the interface definition between the two programs.

Plain mirror types for every pybind class the ``kicad_rl_router`` binding
returns, the authoritative field-order registry (``KRL_FIELDS``), the module
constants snapshotted into the handshake, and the ``to_wire``/``from_wire``
codec. The engine server serializes binding objects with ``to_wire``; the
environment reconstructs them with ``from_wire``. Field names mirror the
binding's declaration order exactly, so consumers (observation builders, DRC
helpers) never see a KiCad type.

This module is stdlib-only and carries no logic from either program: it is
the protocol both of them speak. An identical copy ships in the other
program's repository — the engine's ``engine_server/wire.py`` and the
environment's ``pcb_world/engine/wire.py`` — so neither program has to
import a module from the other at runtime. The two copies are kept byte for
byte identical (checked by the environment's ``tools/check_separation.py``),
which is why this file carries no per-file licence header: each copy is
covered by the LICENSE of the repository it sits in.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import NamedTuple


# ===========================================================================
# Engine-IPC wire schema: plain mirrors of the pybind binding types
# ===========================================================================

class TrackInfo(NamedTuple):
    x1_mm: float
    y1_mm: float
    x2_mm: float
    y2_mm: float
    width_mm: float
    layer: int
    net_code: int
    net_name: str
    uuid: str


class ViaInfo(NamedTuple):
    x_mm: float
    y_mm: float
    diameter_mm: float
    drill_mm: float
    top_layer: int
    bottom_layer: int
    net_code: int
    net_name: str
    uuid: str


class PadInfo(NamedTuple):
    x_mm: float
    y_mm: float
    width_mm: float
    height_mm: float
    layer: int
    net_code: int
    net_name: str
    pad_name: str
    footprint_ref: str
    uuid: str
    physical_id: str
    pad_type: str
    shape: str


class BoardItemInfo(NamedTuple):
    """One row of the complete identity-bearing item inventory.

    ``get_board_items()`` reads every container the board owns - copper lists,
    board drawings, zones and groups, and each footprint with its pads,
    graphical items, fields, zones and groups - so it can answer the identity
    question the copper accessors cannot: does this UUID name exactly one
    physical item? ``kind`` classifies the item (``source`` names the container
    it came from); items with no UUID are still emitted, because "this item
    exists and names no identifier" is itself identity evidence.
    """

    uuid: str
    kind: str
    source: str
    parent_kind: str
    parent_ref: str
    layer: int
    net_code: int
    physical_id: str


class ZonePointHit(NamedTuple):
    """One zone covering a queried point on a queried copper layer.

    A point/layer can be covered by several zones at once, so
    ``get_zone_point_hits()`` returns every covering zone - all nets - rather
    than a single one that would hide the others.

    ``distance_mm`` is the distance from the query point to this zone's *filled
    copper* on this layer: 0.0 on copper, the distance to the nearest fill edge
    or void wall otherwise, and -1.0 when the zone carries no fill polygons for
    the layer. It is the geometry a caller needs to test a copper object of
    radius R that needs clearance C (``distance_mm < R + C``).

    ``fill_provenance`` is not a currency claim: KiCad clears ``NeedRefill()``
    when a board is parsed, so the engine cannot prove the loaded fill matches
    the loaded rules. Values: ``loaded_unverified`` (fill came from the loaded
    board), ``no_fill`` (this zone has no fill polygons for this layer) or
    ``unknown`` (the zone could not be evaluated).
    """

    zone_uuid: str
    name: str
    source: str
    layer: int
    net_code: int
    is_rule_area: bool
    keepout_flags: int
    in_outline: bool
    in_fill: bool
    fill_is_island: bool
    distance_mm: float
    fill_provenance: str


class ZonePointResult(NamedTuple):
    """One ``get_zone_point_hits()`` answer, with every covering zone in ``hits``.

    ``status`` is ``resolved`` only when every zone on that layer was evaluated;
    otherwise it is ``unknown`` and ``reason`` says why - and a consumer must
    treat unknown as unknown, never as safe.

    ``classification`` resolves the copper question only: ``no_zone``,
    ``outline_only``, ``fill_single_net`` (``fill_net_code`` names the one net),
    ``fill_multi_net`` (several nets of pour copper cover the point - a conflict,
    so no single net is named) or ``unknown``.
    """

    query_index: int
    layer: int
    status: str
    reason: str
    classification: str
    fill_net_code: int
    in_keepout: bool
    keepout_flags: int
    fill_provenance: str
    zones_tested: int
    hits: list


class RatsnestEdge(NamedTuple):
    x1_mm: float
    y1_mm: float
    x2_mm: float
    y2_mm: float
    net_code: int
    layer1: int
    layer2: int


class ClusterPoint(NamedTuple):
    x_mm: float
    y_mm: float
    layer: int


class ZoneInfo(NamedTuple):
    pts: list          # [(x_mm, y_mm), ...]
    layer: int
    keepout_tracks: bool
    keepout_vias: bool
    keepout_pads: bool
    name: str


class BoardEdge(NamedTuple):
    x1_mm: float
    y1_mm: float
    x2_mm: float
    y2_mm: float
    width_mm: float


class BoardOutlineShape(NamedTuple):
    kind: str
    x1_mm: float
    y1_mm: float
    x2_mm: float
    y2_mm: float
    x3_mm: float
    y3_mm: float
    width_mm: float


class GraphicShape(NamedTuple):
    index: int
    kind: str
    x1_nm: int
    y1_nm: int
    xm_nm: int
    ym_nm: int
    x2_nm: int
    y2_nm: int
    width_nm: int


class DRCViolation(NamedTuple):
    error_code: int
    error_type: str
    message: str
    x_mm: float
    y_mm: float
    layer: int
    net_names: list
    severity: int
    item_a: str
    item_b: str


class BoundingBox(NamedTuple):
    x_mm: float
    y_mm: float
    width_mm: float
    height_mm: float


class FootprintInfo(NamedTuple):
    ref: str
    value: str
    fpid: str
    x_mm: float
    y_mm: float
    orientation_deg: float
    flipped: bool
    layer: int
    courtyard: list       # [[(x_mm, y_mm), ...], ...] — one closed contour each


class CleanupItem(NamedTuple):
    code: int
    code_name: str
    item_a: str
    item_b: str


# Mutable mirrors: consumers edit fields then pass back (set_design_rules).
@dataclass
class NetClassInfo:
    name: str = ""
    clearance_mm: float = -1.0
    track_width_mm: float = -1.0
    via_diameter_mm: float = -1.0
    via_drill_mm: float = -1.0
    uvia_diameter_mm: float = -1.0
    uvia_drill_mm: float = -1.0


@dataclass
class DesignRules:
    min_clearance_mm: float = -1.0
    min_track_width_mm: float = -1.0
    min_via_diameter_mm: float = -1.0
    min_through_hole_mm: float = -1.0
    min_via_annular_width_mm: float = -1.0
    min_hole_to_hole_mm: float = -1.0
    min_uvia_diameter_mm: float = -1.0
    min_uvia_drill_mm: float = -1.0
    copper_edge_clearance_mm: float = -1.0
    track_width_presets_mm: list = field(default_factory=list)
    via_presets_mm: list = field(default_factory=list)
    default_netclass: NetClassInfo = field(default_factory=NetClassInfo)
    netclasses: list = field(default_factory=list)  # [NetClassInfo, ...]


@dataclass
class CleanupResult:
    """Track-cleaner result: the wire mirror of the binding's ``CleanupResult``.

    Field declaration order = binding declaration order (registered below).

    ``ran`` is False when a precondition rejected the call — today only an open
    routing/drag session (``reject_reason``); nothing was inspected or changed.
    ``items`` holds the CleanupItem entries (pybind objects in-process, plain
    :class:`CleanupItem` mirrors over IPC — ``code_name``/``item_a``/``item_b``
    access is identical) in execution order; ``removed`` / ``modified`` are the
    affected item UUIDs as strings and stay empty on a dry run.
    """

    ran: bool = False
    reject_reason: str = ""
    items: list = field(default_factory=list)      # CleanupItem entries
    removed: list[str] = field(default_factory=list)
    modified: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        """True when the board was actually mutated (never true for a dry run)."""
        return bool(self.removed or self.modified)

    def counts(self) -> dict[str, int]:
        """Operation count per cleanup code name ("merge_tracks": 3, …)."""
        out: dict[str, int] = {}
        for item in self.items:
            out[item.code_name] = out.get(item.code_name, 0) + 1
        return out


# Authoritative field ORDER per wire type (= declaration order in
# pns_rl_bindings.cpp). The server validates this registry against the live
# binding at startup (constant handshake) — a binding field added/renamed
# without updating this registry fails loudly on both sides.
_WIRE_TYPES = {
    "TrackInfo": TrackInfo,
    "ViaInfo": ViaInfo,
    "PadInfo": PadInfo,
    "BoardItemInfo": BoardItemInfo,
    "ZonePointHit": ZonePointHit,
    "ZonePointResult": ZonePointResult,
    "RatsnestEdge": RatsnestEdge,
    "ClusterPoint": ClusterPoint,
    "ZoneInfo": ZoneInfo,
    "BoardEdge": BoardEdge,
    "BoardOutlineShape": BoardOutlineShape,
    "GraphicShape": GraphicShape,
    "DRCViolation": DRCViolation,
    "BoundingBox": BoundingBox,
    "FootprintInfo": FootprintInfo,
    "CleanupItem": CleanupItem,
    "NetClassInfo": NetClassInfo,
    "DesignRules": DesignRules,
    "CleanupResult": CleanupResult,
}

KRL_FIELDS: dict[str, tuple] = {
    name: (
        cls._fields if issubclass(cls, tuple)
        else tuple(cls.__dataclass_fields__)
    )
    for name, cls in _WIRE_TYPES.items()
}

# Module-level constants of kicad_rl_router snapshotted into the handshake
# (the BSD-3 client never imports the module, so these come over the wire).
KRL_CONSTANT_NAMES = (
    "LAYER_EDGE_CUTS", "LAYER_MARGIN",
    "MODE_MARK_OBSTACLES", "MODE_SHOVE", "MODE_WALKAROUND",
    "CORNER_MITERED_45", "CORNER_ROUNDED_45", "CORNER_MITERED_90",
    "CORNER_ROUNDED_90",
    "DM_CORNER", "DM_SEGMENT", "DM_VIA", "DM_FREE_ANGLE", "DM_ARC",
    "DM_ANY", "DM_COMPONENT",
    "STATE_IDLE", "STATE_DRAG_SEGMENT", "STATE_DRAG_COMPONENT",
    "STATE_ROUTE_TRACK",
    "F_Cu", "B_Cu",
)

_PRIMITIVES = (type(None), bool, int, float, str, bytes)
_WIRE_TAG = "__krl__"


def to_wire(value):
    """Encode a binding return value into primitives-only structures.

    Accepts primitives, lists/tuples/dicts (recursed), and any object
    whose type name is registered in ``KRL_FIELDS`` (pybind object or
    plain mirror alike — encoding is getattr-based). Unknown object types
    raise: the wire never silently degrades.
    """
    if isinstance(value, _PRIMITIVES):
        return value
    tname = type(value).__name__
    if tname in KRL_FIELDS:
        return (_WIRE_TAG, tname,
                tuple(to_wire(getattr(value, f)) for f in KRL_FIELDS[tname]))
    if isinstance(value, (list, tuple)):
        encoded = [to_wire(v) for v in value]
        # A plain tuple must not alias the tagged-tuple form.
        if isinstance(value, tuple):
            return ("__tuple__", encoded)
        return encoded
    if isinstance(value, dict):
        return {k: to_wire(v) for k, v in value.items()}
    raise TypeError(
        f"engine IPC: unserializable return type {type(value)!r} — register "
        "it in KRL_FIELDS (wire.py, both copies)")


def from_wire(value):
    """Decode ``to_wire`` output into plain mirror objects."""
    if isinstance(value, _PRIMITIVES):
        return value
    if isinstance(value, tuple):
        if value and value[0] == _WIRE_TAG:
            _, tname, fields = value
            return _WIRE_TYPES[tname](*(from_wire(f) for f in fields))
        if value and value[0] == "__tuple__":
            return tuple(from_wire(v) for v in value[1])
        return tuple(from_wire(v) for v in value)
    if isinstance(value, list):
        return [from_wire(v) for v in value]
    if isinstance(value, dict):
        return {k: from_wire(v) for k, v in value.items()}
    raise TypeError(f"engine IPC: cannot decode wire value {value!r}")
