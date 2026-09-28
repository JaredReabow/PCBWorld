"""A synthetic four-copper-layer board that drives the copper-clearance reporter.

The design-rules cache in ``drc_test_provider_copper_clearance`` keys on a
``(BOARD_ITEM*, BOARD_ITEM*)`` pair and canonicalises it by pointer address,
because the key's equality and hash are order-sensitive. Two contracts meet in
that cache and this fixture exercises both, on one deterministic board:

* **multiplicity** - the filter that inserts and the visitor that records "this
  pair already produced a finding" must agree on the canonical order, or a pair
  is reported once per copper layer it shares instead of once;
* **order independence** - a same-logical-pad pair (one footprint, one pad
  number) with a netless member and a netted member must produce the same
  finding whichever member ``footprint->Pads()`` reaches first. A visit from the
  netless member returns early (``GetNetCode() == 0``) without reporting; if it
  leaves its layer claimed, the netted member's later visit is filtered out and
  the pair is never reported at all.

Everything is written from nothing but this file, and every UUID is a uuid5 of a
fixed token, so two runs write identical bytes. The board is a test input, not a
design: it exists to make the provider's cache contract observable. All of the
geometry is synthetic and none of it is a real supply or signal name.

Case coverage, one footprint group each:

``FM4`` / ``FR4``  multilayer multiplicity: a netted through-hole pad with
                   several netless partners inside its outline, sharing all four
                   copper layers. These are the *flip* families - the board-wide
                   ``netted_first`` argument reorders their pads, so the same
                   pair is visited from either side by construction;
``GS1``            single copper layer: a netless surface-mount partner inside
                   a netted surface-mount pad, both on ``F.Cu`` only, declared
                   netless first;
``MX1``            mixed: two netted pads on different nets and two netless
                   pads in one footprint, interleaved netless-first, so some of
                   its pairs are exempt (netless/netless) and the rest report;
``EQ1``            equal-net exemption: two pads on the *same* net;
``AL1``            all-netless: a footprint whose pads are all netless;
``DN1``            different real nets: two netted pads, no unconnected- name;
``UC1``            two-sided ``unconnected-(...)`` exemption: both pads carry an
                   ``unconnected-(...)`` short name and neither reports;
``GEN*``           ordinary collisions: pads of *different* footprints on
                   different nets, fully overlapping;
``CL1``/``CL2``    clean control: different footprints, one net, overlapping;
``NEAR*``          nearby non-overlap control: different footprints, different
                   nets, 0.10 mm apart - inside the query's reach, so it can be
                   a clearance row, but it must never be a shorting row;
``FAR*``           distant control: different footprints, different nets, far
                   beyond the query's reach.

The expected findings are computed here from the provider's own decision rule
(see :func:`_same_footprint_pair_reports`), not read back from the report, so a
change that substitutes or drops a pair at the same count fails.
"""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass
from pathlib import Path

#: Synthetic nets. Nothing here is a real supply or signal name. Nets 4 and 5
#: deliberately carry KiCad's own ``unconnected-(...)`` short-name convention.
NETS = (
    (1, "+REGTEST_A"),
    (2, "+REGTEST_B"),
    (3, "+REGTEST_C"),
    (4, "unconnected-(FU1-Pad1)"),
    (5, "unconnected-(FV1-Pad1)"),
)

#: The four copper layers of the fixture, in the engine's own layer numbering.
COPPER_LAYERS = (0, 4, 6, 2)

NAMESPACE = uuid.UUID("2b8f1a44-8f0e-4c6b-9b0a-51d1f0a0c001")


def net_name(code: int) -> str:
    return "" if not code else dict(NETS)[code]


def uid(token: str) -> str:
    return str(uuid.uuid5(NAMESPACE, token))


def ring(count: int, radius: float) -> list[tuple[float, float]]:
    return [(round(radius * math.cos(2 * math.pi * i / count), 4),
             round(radius * math.sin(2 * math.pi * i / count), 4))
            for i in range(count)]


@dataclass(frozen=True)
class Pad:
    """One pad, positioned relative to its footprint origin."""

    token: str
    net: int
    size: float
    drill: float = 0.0
    number: str = "1"
    x: float = 0.0
    y: float = 0.0
    layers: str = '"*.Cu" "*.Mask"'
    kind: str = "thru_hole"


def _thru(token: str, net: int, size: float, drill: float, *,
          x: float = 0.0, y: float = 0.0) -> Pad:
    return Pad(token=token, net=net, size=size, drill=drill, x=x, y=y)


def _smd(token: str, net: int, size: float, *, x: float = 0.0, y: float = 0.0) -> Pad:
    return Pad(token=token, net=net, size=size, x=x, y=y,
               layers='"F.Cu" "F.Mask"', kind="smd")


@dataclass(frozen=True)
class FlipFamily:
    """A footprint the board-wide ``netted_first`` argument may reorder."""

    reference: str
    x: float
    y: float
    netted: tuple[Pad, ...]
    netless: tuple[Pad, ...]

    def ordered(self, netted_first: bool) -> tuple[Pad, ...]:
        return (self.netted + self.netless) if netted_first else (self.netless + self.netted)

    def pads(self) -> tuple[Pad, ...]:
        return self.netted + self.netless


@dataclass(frozen=True)
class FixedFamily:
    """A footprint whose pads are emitted exactly as declared."""

    reference: str
    x: float
    y: float
    pads: tuple[Pad, ...]


def _family_thru(reference: str, x: float, y: float, netted_net: int,
                 netted_size: float, netted_drill: float, partner_size: float,
                 partner_drill: float, radius: float,
                 partner_count: int) -> FlipFamily:
    netted = (_thru(f"{reference}:big", netted_net, netted_size, netted_drill),)
    netless = tuple(
        _thru(f"{reference}:s{index}", 0, partner_size, partner_drill, x=px, y=py)
        for index, (px, py) in enumerate(ring(partner_count, radius)))
    return FlipFamily(reference, x, y, netted, netless)


#: Multilayer flip families: every same-number partner sits inside the netted
#: pad's outline, so every pair is inside the query's reach on all four layers.
FLIP_FAMILIES = (
    _family_thru("FM4", 58.0, 58.0, 1, 5.0, 3.2, 0.80, 0.50, 2.00, 4),
    _family_thru("FR4", 78.0, 58.0, 2, 3.0, 1.6, 0.55, 0.35, 1.20, 3),
)

#: Fixed-order families. Their declaration order is part of the case, so a
#: board-wide flip does not touch them; the default board therefore always
#: contains at least one netless-first family.
FIXED_FAMILIES = (
    # Single copper layer: surface mount only, netless partner declared first.
    FixedFamily("GS1", 96.0, 58.0, (
        _smd("GS1:s0", 0, 0.90),
        _smd("GS1:big", 3, 2.00),
    )),
    # Mixed: two netted pads on different nets and two netless pads, interleaved.
    FixedFamily("MX1", 58.0, 78.0, (
        _thru("MX1:s0", 0, 0.90, 0.50, x=-0.5),
        _thru("MX1:big1", 1, 4.00, 2.40),
        _thru("MX1:s1", 0, 0.90, 0.50, x=0.5),
        _thru("MX1:big2", 2, 3.00, 1.80),
    )),
    # Equal-net exemption: two pads, one net.
    FixedFamily("EQ1", 112.0, 58.0, (
        _thru("EQ1:a", 1, 2.40, 1.40),
        _thru("EQ1:b", 1, 2.40, 1.40),
    )),
    # All-netless: nothing in this family may report.
    FixedFamily("AL1", 128.0, 58.0, (
        _thru("AL1:a", 0, 1.60, 0.80, x=-0.5),
        _thru("AL1:b", 0, 1.60, 0.80),
        _thru("AL1:c", 0, 1.60, 0.80, x=0.5),
    )),
    # Different real nets, no unconnected- name: reports whatever the order.
    FixedFamily("DN1", 78.0, 78.0, (
        _thru("DN1:a", 1, 3.00, 1.60),
        _thru("DN1:b", 2, 3.00, 1.60),
    )),
    # Two-sided unconnected-(...) exemption: both names, so neither reports.
    FixedFamily("UC1", 96.0, 78.0, (
        _thru("UC1:a", 4, 2.40, 1.40),
        _thru("UC1:b", 5, 2.40, 1.40),
    )),
)

#: Ordinary collisions, and the controls. One footprint per row.
GENUINE = (
    ("GEN1", 112.0, 78.0, 2.0, 1.0, 1),
    ("GEN2", 112.0, 78.0, 2.0, 1.0, 2),
    ("GEN3", 125.0, 98.0, 1.6, 0.8, 1),
    ("GEN4", 125.0, 98.0, 1.6, 0.8, 2),
)

#: Same net, overlapping: must stay silent.
CLEAN = (
    ("CL1", 132.0, 78.0, 1.6, 0.8, 3),
    ("CL2", 132.0, 78.0, 1.6, 0.8, 3),
)

#: Different nets, 0.10 mm apart: inside the query's reach, never a short.
NEARBY = (
    ("NEAR1", 58.0, 98.0, 2.0, 1.0, 2),
    ("NEAR2", 60.10, 98.0, 2.0, 1.0, 3),
)

#: Different nets, far apart: outside the query's reach either way.
DISTANT = (
    ("FAR1", 85.0, 98.0, 1.6, 0.8, 1),
    ("FAR2", 105.0, 98.0, 1.6, 0.8, 2),
)

#: The families whose pads are through-hole and therefore share every copper
#: layer; ``GS1`` is the deliberate single-layer case.
MULTILAYER_FAMILIES = tuple(
    family.reference for family in FLIP_FAMILIES
) + tuple(family.reference for family in FIXED_FAMILIES if family.reference != "GS1")

SINGLE_LAYER_FAMILIES = ("GS1",)


def _same_footprint_pair_reports(first: Pad, second: Pad) -> bool:
    """The provider's ``SameLogicalPadAs`` decision for two pads of one footprint.

    The branch exempts a pair whose two members share a net *code* - which
    covers equal nets and the both-netless case - and a pair whose two members
    both carry an ``unconnected-(...)`` short name. Every other reached pair is
    a finding. The decision is a function of the pair, not of which member the
    loop happens to reach first; every same-number partner in this fixture
    overlaps its primary pad, so the query reaches each pair on every copper
    layer they share.
    """
    if first.net == second.net:
        return False
    if (net_name(first.net).startswith("unconnected-(")
            and net_name(second.net).startswith("unconnected-(")):
        return False
    return True


def _family_members(family: FlipFamily | FixedFamily) -> tuple[Pad, ...]:
    return family.pads() if isinstance(family, FlipFamily) else family.pads


def _family_pairs(family: FlipFamily | FixedFamily) -> tuple[tuple[Pad, Pad], ...]:
    members = _family_members(family)
    return tuple((members[i], members[j])
                 for i in range(len(members))
                 for j in range(i + 1, len(members)))


def reportable_pair_ids() -> frozenset[tuple[str, str]]:
    """The same-logical-pad pairs the provider must report, by pad UUID."""
    pairs = set()
    for family in FLIP_FAMILIES + FIXED_FAMILIES:
        for first, second in _family_pairs(family):
            if _same_footprint_pair_reports(first, second):
                pairs.add(tuple(sorted((uid(first.token), uid(second.token)))))
    return frozenset(pairs)


def exempt_pair_ids() -> frozenset[tuple[str, str]]:
    """The same-footprint pairs the provider must *not* report."""
    pairs = set()
    for family in FLIP_FAMILIES + FIXED_FAMILIES:
        for first, second in _family_pairs(family):
            if not _same_footprint_pair_reports(first, second):
                pairs.add(tuple(sorted((uid(first.token), uid(second.token)))))
    return frozenset(pairs)


def _control_pair_ids(rows: tuple) -> frozenset[tuple[str, str]]:
    """Adjacent rows of a table declared two footprints per pair."""
    pairs = set()
    for index in range(0, len(rows) - 1, 2):
        pairs.add(tuple(sorted((uid(f"{rows[index][0]}:p"),
                                uid(f"{rows[index + 1][0]}:p")))))
    return frozenset(pairs)


def genuine_pair_ids() -> frozenset[tuple[str, str]]:
    """The ordinary collisions between different footprints."""
    return _control_pair_ids(GENUINE)


def clean_pair_id() -> tuple[str, str]:
    """The same-net overlapping pair that must never be reported."""
    return tuple(sorted((uid(f"{CLEAN[0][0]}:p"), uid(f"{CLEAN[1][0]}:p"))))


def forbidden_pair_ids() -> frozenset[tuple[str, str]]:
    """Every pair that must never appear in the shorting class."""
    return frozenset(set(exempt_pair_ids()) | {clean_pair_id()}
                     | set(_control_pair_ids(NEARBY)) | set(_control_pair_ids(DISTANT)))


def expected_pair_ids() -> frozenset[tuple[str, str]]:
    """Exactly the pairs the provider must report on this fixture."""
    return frozenset(set(reportable_pair_ids()) | set(genuine_pair_ids()))


# --------------------------------------------------------------------------
# Board text
# --------------------------------------------------------------------------

def _pad_text(pad: Pad) -> str:
    drill_line = f"\t\t\t(drill {pad.drill})\n" if pad.drill else ""
    fill_line = "" if pad.kind == "smd" else "\t\t\t(remove_unused_layers no)\n"
    return (
        f'\t\t(pad "{pad.number}" {pad.kind} circle\n'
        f"\t\t\t(at {pad.x} {pad.y})\n"
        f"\t\t\t(size {pad.size} {pad.size})\n"
        f"{drill_line}"
        f"\t\t\t(layers {pad.layers})\n"
        f"{fill_line}"
        f'\t\t\t(net {pad.net} "{net_name(pad.net)}")\n'
        f'\t\t\t(uuid "{uid(pad.token)}")\n'
        "\t\t)\n")


def _footprint(reference: str, x: float, y: float, pads: list[Pad]) -> str:
    return (
        f'\t(footprint "pcbworld-regression:fixture"\n'
        '\t\t(layer "F.Cu")\n'
        f'\t\t(uuid "{uid(reference + ":fp")}")\n'
        f"\t\t(at {x} {y})\n"
        f'\t\t(property "Reference" "{reference}"\n'
        "\t\t\t(at 0 0 0)\n"
        '\t\t\t(layer "F.SilkS")\n'
        "\t\t\t(hide yes)\n"
        f'\t\t\t(uuid "{uid(reference + ":ref")}")\n'
        "\t\t)\n"
        f'\t\t(property "Value" "fixture"\n'
        "\t\t\t(at 0 0 0)\n"
        '\t\t\t(layer "F.Fab")\n'
        "\t\t\t(hide yes)\n"
        f'\t\t\t(uuid "{uid(reference + ":val")}")\n'
        "\t\t)\n"
        + "".join(_pad_text(pad) for pad in pads) +
        "\t\t(embedded_fonts no)\n"
        "\t)\n")


def _single_pad_footprint(reference: str, x: float, y: float, size: float,
                          drill: float, net: int) -> str:
    return _footprint(reference, x, y,
                      [_thru(f"{reference}:p", net, size, drill)])


def build_board(netted_first: bool = True) -> str:
    """The board text. ``netted_first=False`` reorders the *flip* families only."""
    parts: list[str] = []
    for family in FLIP_FAMILIES:
        parts.append(_footprint(family.reference, family.x, family.y,
                                list(family.ordered(netted_first))))
    for family in FIXED_FAMILIES:
        parts.append(_footprint(family.reference, family.x, family.y, list(family.pads)))
    for reference, x, y, size, drill, net in GENUINE:
        parts.append(_single_pad_footprint(reference, x, y, size, drill, net))
    for reference, x, y, size, drill, net in CLEAN:
        parts.append(_single_pad_footprint(reference, x, y, size, drill, net))
    for reference, x, y, size, drill, net in NEARBY:
        parts.append(_single_pad_footprint(reference, x, y, size, drill, net))
    for reference, x, y, size, drill, net in DISTANT:
        parts.append(_single_pad_footprint(reference, x, y, size, drill, net))

    net_lines = "\n".join(f'\t(net {code} "{name}")' for code, name in NETS)
    layers = "\n".join(
        f'\t\t({index} "{name}" signal)'
        for index, name in zip(COPPER_LAYERS,
                               ("F.Cu", "In1.Cu", "In2.Cu", "B.Cu")))
    return (
        "(kicad_pcb\n"
        "\t(version 20241229)\n"
        '\t(generator "pcbworld-regression")\n'
        '\t(generator_version "9.0")\n'
        "\t(general\n\t\t(thickness 1.6)\n\t\t(legacy_teardrops no)\n\t)\n"
        '\t(paper "A4")\n'
        "\t(layers\n"
        f"{layers}\n"
        '\t\t(1 "F.Mask" user)\n'
        '\t\t(3 "B.Mask" user)\n'
        '\t\t(5 "F.SilkS" user)\n'
        '\t\t(7 "B.SilkS" user)\n'
        '\t\t(25 "Edge.Cuts" user)\n'
        "\t)\n"
        "\t(setup\n\t\t(pad_to_mask_clearance 0)\n"
        "\t\t(allow_soldermask_bridges_in_footprints no)\n\t)\n"
        f"{net_lines}\n"
        + "".join(parts) +
        "\t(gr_rect\n\t\t(start 40 40)\n\t\t(end 140 115)\n"
        "\t\t(stroke\n\t\t\t(width 0.1)\n\t\t\t(type default)\n\t\t)\n"
        "\t\t(fill none)\n"
        '\t\t(layer "Edge.Cuts")\n'
        f'\t\t(uuid "{uid("edge")}")\n'
        "\t)\n"
        "\t(embedded_fonts no)\n"
        ")\n")


#: An empty design-rules file is a valid, loadable rule set: the fixture is about
#: the pair cache, not about clearance values, and the shorting test is
#: severity-driven rather than rule-driven.
DESIGN_RULES = ""

#: Minimal project sidecar. The engine wants a project path beside the board; the
#: fixture defines no netclasses and no DRC overrides of its own.
PROJECT = """{
  "board": {
    "design_settings": {}
  },
  "meta": {
    "filename": "board.kicad_pro",
    "version": 1
  },
  "net_settings": {
    "classes": []
  }
}
"""


def write_fixture(directory: Path, *, netted_first: bool = True) -> Path:
    """Write board + sidecars into *directory* and return the board path."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    board = directory / "board.kicad_pcb"
    board.write_text(build_board(netted_first=netted_first), encoding="utf-8")
    (directory / "board.kicad_pro").write_text(PROJECT, encoding="utf-8")
    (directory / "board.kicad_dru").write_text(DESIGN_RULES, encoding="utf-8")
    return board


# --------------------------------------------------------------------------
# Count helpers
# --------------------------------------------------------------------------

def expected_same_logical_pad_pairs() -> int:
    return len(reportable_pair_ids())


def expected_genuine_pairs() -> int:
    return len(genuine_pair_ids())


def expected_shorting_pairs() -> int:
    return len(expected_pair_ids())


def same_logical_pad_pair_ids() -> frozenset[tuple[str, str]]:
    """Alias for :func:`reportable_pair_ids`, kept for the earlier regression."""
    return reportable_pair_ids()
