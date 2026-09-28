"""Synthetic ``.kicad_pcb`` boards for the agent reliability tests.

Everything here is generated: no private or production board is used, copied or
referenced. The format follows the KiCad 9 s-expression the engine's own test
fixtures use (``tests/fixtures/simple_routing_board.kicad_pcb``), restricted to
the elements these tests need — an outline, nets, pads on either copper side (or
thru-hole) and optional track segments, locked or not.
"""

from __future__ import annotations

import itertools
import json
import uuid
from pathlib import Path
from typing import Iterable, NamedTuple

_FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
_PROJECT_TEMPLATE = _FIXTURES / "simple_routing_board.kicad_pro"


class Pad(NamedTuple):
    ref: str
    x: float
    y: float
    net: int
    kind: str = "smd_top"      # smd_top | smd_bottom | thru
    size: float = 1.0


class Segment(NamedTuple):
    x1: float
    y1: float
    x2: float
    y2: float
    net: int
    width: float = 0.25
    layer: str = "F.Cu"
    locked: bool = False


class Via(NamedTuple):
    x: float
    y: float
    net: int
    size: float = 0.6
    drill: float = 0.3


class Zone(NamedTuple):
    net: int
    x0: float
    y0: float
    x1: float
    y1: float
    layer: str = "F.Cu"
    clearance: float = 0.25
    fill_points: tuple[tuple[float, float], ...] = ()
    #: Extra copper layers (KiCad writes ``(layers ...)`` when a zone spans more
    #: than one) and the rule-area keepout flags. An empty ``keepout`` is a copper
    #: pour; a non-empty one makes the zone a rule area, which carries no fill.
    extra_layers: tuple[str, ...] = ()
    keepout: tuple[str, ...] = ()
    #: Whether the stored fill polygons are marked ``(island)``. Defaults True to
    #: keep existing fixtures byte-identical.
    island: bool = True

    @property
    def layers(self) -> tuple[str, ...]:
        return (self.layer,) + tuple(self.extra_layers)


_UUID_COUNTER = itertools.count(1)


def _uuid() -> str:
    # Deterministic per process: routing determinism depends on item order, and a
    # board that changes UUIDs between runs is not the same board.
    return str(uuid.UUID(int=next(_UUID_COUNTER), version=4))


_PAD_KINDS = {
    "smd_top": ('(layers "F.Cu" "F.Paste" "F.Mask")', "F.Cu", "smd roundrect"),
    "smd_bottom": ('(layers "B.Cu" "B.Paste" "B.Mask")', "B.Cu", "smd roundrect"),
    "thru": ('(layers "*.Cu" "*.Mask")', "F.Cu", "thru_hole circle"),
}


def _pad_block(pad: Pad) -> str:
    layers, fp_layer, pad_type = _PAD_KINDS[pad.kind]
    drill = '      (drill 0.4)\n' if pad.kind == "thru" else ""
    extra = "" if pad.kind == "thru" else "      (roundrect_rratio 0.25)\n"
    return f"""  (footprint "SyntheticPad:{fp_layer}"
    (layer "{fp_layer}")
    (at {pad.x} {pad.y})
    (uuid "{_uuid()}")
    (property "Reference" "{pad.ref}"
      (at 0 -1)
      (layer "F.SilkS")
      (effects (font (size 0.6 0.6) (thickness 0.1)))
    )
    (pad "1" {pad_type}
      (at 0 0)
      (size {pad.size} {pad.size})
{drill}{layers}
      (net {pad.net} "{net_name(pad.net)}")
      (uuid "{_uuid()}")
    )
  )
"""


_NET_NAMES = {0: "", 1: "NET1", 2: "NET2", 3: "NET3", 4: "NET4"}


def net_name(code: int) -> str:
    return _NET_NAMES.get(code, f"NET{code}")


def _segment_block(seg: Segment) -> str:
    locked = "\n    (locked yes)" if seg.locked else ""
    return (
        f"  (segment (start {seg.x1} {seg.y1}) (end {seg.x2} {seg.y2})"
        f" (width {seg.width}){locked}\n"
        f'    (layer "{seg.layer}")\n'
        f"    (net {seg.net})\n"
        f'    (uuid "{_uuid()}")\n'
        f"  )\n"
    )


def _via_block(via: Via) -> str:
    return (
        f"  (via (at {via.x} {via.y}) (size {via.size}) (drill {via.drill})\n"
        f'    (layers "F.Cu" "B.Cu")\n'
        f"    (net {via.net})\n"
        f'    (uuid "{_uuid()}")\n'
        f"  )\n"
    )


def _zone_block(zone: Zone) -> str:
    layers = list(zone.layers)
    if len(layers) > 1:
        layer_line = "(layers " + " ".join(f'"{name}"' for name in layers) + ")"
    else:
        layer_line = f'(layer "{layers[0]}")'

    filled = ""
    if zone.fill_points and not zone.keepout:
        vertices = " ".join(f"(xy {x} {y})" for x, y in zone.fill_points)
        island = "(island)" if zone.island else ""
        filled = "".join(
            f'''\n    (filled_polygon
      (layer "{name}")
      {island}
      (pts {vertices})
    )'''
            for name in layers
        )

    if zone.keepout:
        flags = {
            "tracks": "not_allowed" if "tracks" in zone.keepout else "allowed",
            "vias": "not_allowed" if "vias" in zone.keepout else "allowed",
            "pads": "not_allowed" if "pads" in zone.keepout else "allowed",
        }
        body = (
            "    (keepout"
            f' (tracks {flags["tracks"]}) (vias {flags["vias"]}) (pads {flags["pads"]})'
            " (copperpour allowed) (footprints allowed))\n"
        )
        net_line = "    (net 0)\n    (net_name \"\")\n"
        connect = ""
        fill_block = ""
    else:
        body = ""
        net_line = f'    (net {zone.net})\n    (net_name "{net_name(zone.net)}")\n'
        connect = f"    (connect_pads (clearance {zone.clearance}))\n"
        fill_block = "    (fill yes (thermal_gap 0.5) (thermal_bridge_width 0.5))\n"

    return f'''  (zone
{net_line}    {layer_line}
    (uuid "{_uuid()}")
    (name "SyntheticZone")
    (hatch edge 0.5)
{body}{connect}    (min_thickness 0.2)
    (filled_areas_thickness no)
{fill_block}    (polygon
      (pts (xy {zone.x0} {zone.y0}) (xy {zone.x1} {zone.y0})
           (xy {zone.x1} {zone.y1}) (xy {zone.x0} {zone.y1}))
    )
    {filled}
  )
'''


def board_text(
    pads: Iterable[Pad],
    segments: Iterable[Segment] = (),
    *,
    vias: Iterable[Via] = (),
    zones: Iterable[Zone] = (),
    nets: Iterable[int] = (1, 2),
    width: float = 50.0,
    height: float = 30.0,
    copper_layers: int = 2,
) -> str:
    net_lines = "\n".join(f'  (net {n} "{net_name(n)}")' for n in nets)
    # File-format layer numbers (not the PCB_LAYER_ID enum the engine reports for
    # items): F.Cu is 0, inner copper counts 1..N-2, and B.Cu is 31. A 4-layer
    # board therefore writes 0/1/2/31 while the engine reports 0/4/6/2.
    if copper_layers < 2:
        raise ValueError("a board needs at least two copper layers")
    copper_rows = ['    (0 "F.Cu" signal)']
    for index in range(copper_layers - 2):
        copper_rows.append(f'    ({index + 1} "In{index + 1}.Cu" signal)')
    copper_rows.append('    (31 "B.Cu" signal)')
    layers_block = "\n".join(copper_rows)
    return f"""(kicad_pcb
  (version 20241229)
  (generator "agent_reliability_tests")
  (generator_version "9.0.8")
  (general
    (thickness 1.6)
    (legacy_teardrops no)
  )
  (paper "A4")
  (layers
{layers_block}
    (36 "B.SilkS" user "B.Silkscreen")
    (37 "F.SilkS" user "F.Silkscreen")
    (44 "Edge.Cuts" user)
  )
  (setup
    (pad_to_mask_clearance 0)
  )

{net_lines}

  (net_class "Default" "Default net class"
    (clearance 0.2)
    (trace_width 0.25)
    (via_dia 0.6)
    (via_drill 0.3)
    (uvia_dia 0.3)
    (uvia_drill 0.1)
  )

{"".join(_pad_block(p) for p in pads)}
{"".join(_segment_block(s) for s in segments)}
{"".join(_via_block(v) for v in vias)}
{"".join(_zone_block(z) for z in zones)}
  (gr_rect
    (start 0.0 0.0)
    (end {width} {height})
    (stroke (width 0.15) (type solid))
    (fill none)
    (layer "Edge.Cuts")
    (uuid "{_uuid()}")
  )
)
"""


def write_board(
    path: str | Path,
    pads: Iterable[Pad],
    segments: Iterable[Segment] = (),
    *,
    vias: Iterable[Via] = (),
    zones: Iterable[Zone] = (),
    nets: Iterable[int] = (1, 2),
    width: float = 50.0,
    height: float = 30.0,
    clearance_mm: float = 0.2,
    track_width_mm: float = 0.25,
    min_through_hole_mm: float = 0.3,
    copper_layers: int = 2,
) -> str:
    """Write a synthetic board (and its ``.kicad_pro``) and return its path.

    The engine's strict load contract refuses a board without a project file, so
    the sibling ``.kicad_pro`` is written too — derived from the repository's own
    fixture template with the netclass fields overridden.
    """
    p = Path(path)
    p.write_text(
        board_text(pads, segments, vias=vias, zones=zones, nets=nets, width=width, height=height,
                   copper_layers=copper_layers),
        encoding="utf-8",
    )
    write_project(
        p, clearance_mm=clearance_mm, track_width_mm=track_width_mm,
        min_through_hole_mm=min_through_hole_mm,
    )
    return str(p)


def write_project(
    path: str | Path,
    *,
    clearance_mm: float = 0.2,
    track_width_mm: float = 0.25,
    min_through_hole_mm: float = 0.3,
) -> str:
    """Write a minimal-but-valid ``.kicad_pro`` next to a synthetic board.

    The template is the repository's own ``simple_routing_board.kicad_pro`` — a
    real project file KiCad loads without complaint — with the Default netclass
    numbers replaced. Nothing private is involved.
    """
    p = Path(path)
    project = json.loads(_PROJECT_TEMPLATE.read_text(encoding="utf-8"))
    project.setdefault("meta", {})["filename"] = p.with_suffix(".kicad_pro").name
    for netclass in project["net_settings"]["classes"]:
        if netclass.get("name") == "Default":
            netclass["clearance"] = float(clearance_mm)
            netclass["track_width"] = float(track_width_mm)
    rules = project["board"]["design_settings"].setdefault("rules", {})
    rules["min_through_hole_diameter"] = float(min_through_hole_mm)
    out = p.with_suffix(".kicad_pro")
    out.write_text(json.dumps(project, indent=2), encoding="utf-8")
    return str(out)


def write_rules(path: str | Path, clearance_mm: float) -> str:
    """Write a minimal ``.kicad_dru`` with one clearance rule."""
    p = Path(path)
    p.write_text(
        "(version 1)\n"
        f"(rule \"synthetic-clearance\" (constraint clearance (min {clearance_mm}mm))"
        " (condition \"A.Type == 'Track' && B.Type == 'Track'\"))\n",
        encoding="utf-8",
    )
    return str(p)


# ---------------------------------------------------------------------------
# Named scenarios
# ---------------------------------------------------------------------------


def direct_board(path) -> str:
    """Two pads of NET1 on F.Cu, 30 mm apart, no obstacles."""
    return write_board(
        path,
        pads=[Pad("PA1", 10.0, 10.0, 1), Pad("PB1", 40.0, 10.0, 1)],
    )


def obstacle_board(path, *, locked: bool = False) -> str:
    """NET1 pads with a NET2 segment crossing the straight line between them."""
    return write_board(
        path,
        pads=[Pad("PA1", 10.0, 10.0, 1), Pad("PB1", 40.0, 10.0, 1)],
        segments=[Segment(25.0, 6.0, 25.0, 14.0, 2, locked=locked)],
    )


def shove_board(path) -> str:
    """NET1 pads with an unlocked NET2 track parked in the corridor."""
    return write_board(
        path,
        pads=[Pad("PA1", 10.0, 10.0, 1), Pad("PB1", 40.0, 10.0, 1)],
        segments=[Segment(26.0, 8.0, 26.0, 12.0, 2, width=0.25)],
    )


def layer_board(path) -> str:
    """NET1 pad on F.Cu and a thru-hole partner, so a via is the only way over."""
    return write_board(
        path,
        pads=[
            Pad("PA1", 10.0, 10.0, 1, kind="smd_top"),
            Pad("PB1", 40.0, 20.0, 1, kind="thru"),
        ],
    )


def corridor_board(path, *, gap_mm: float) -> str:
    """Two parallel NET2 walls leaving a ``gap_mm`` slot for the NET1 route.

    Used for the custom-rule test: with the default 0.2 mm clearance the slot is
    passable, with a rule wider than the slot it is not.
    """
    half = gap_mm / 2.0
    return write_board(
        path,
        pads=[Pad("PA1", 10.0, 15.0, 1), Pad("PB1", 40.0, 15.0, 1)],
        segments=[
            Segment(25.0, 0.5, 25.0, 15.0 - half, 2),
            Segment(25.0, 15.0 + half, 25.0, 29.5, 2),
        ],
    )
