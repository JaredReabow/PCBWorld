"""Compact, planner-sized observations of one outstanding connection.

The planner (model or scripted) never sees a whole board. It sees one net pair,
the few obstacles nearest to it, the choices that are actually legal on this
board, and the outcome of the attempts already made. That keeps a request inside
a few thousand tokens and keeps the decision in the planner's hands while the
mechanics stay deterministic.

Layer identity comes from the **engine's own layer map**, not from a guess about
what the item mirrors mean: the engine's item mirrors label copper layers with an
internal board id (0, 2, 4, 6 … for a four-layer board, ``-2`` for an item that
spans copper), while the routing API takes human layers (1..N).
``KiCadEngine.layer_map`` is that mapping, so :class:`LayerResolver` reads it and
then *verifies* it against the engine's connectivity query on a bounded sample of
each layer (a contradiction raises rather than routing on an invented number).
Reading the map instead of sampling every track/via/pad took the V3 build from
~12 s of board-wide probing to a handful of queries.
"""

from __future__ import annotations

import json
import hashlib
import math
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Iterable, Mapping, Sequence

from pcb_world.agent.actions import MODE_NAMES
from pcb_world.agent.session import AgentSession
from pcb_world.agent.state import _round_anchor, anchor_set


class LayerConventionError(RuntimeError):
    """The engine's layer ids could not be mapped to human layers on this board.

    Raised rather than guessed: routing with the wrong layer would be a silent
    copper mistake.
    """


@dataclass(frozen=True)
class LayerResolver:
    """Mirror copper-layer id -> human layer, from the engine and verified here."""

    SPANS_COPPER = -2

    copper_layers: int
    mapping: Mapping[int, int]
    samples: Mapping[int, int] = field(default_factory=dict)
    unmapped: Mapping[int, str] = field(default_factory=dict)
    source: str = "engine.layer_map"

    @classmethod
    def build(cls, session: AgentSession, *, verify_samples: int = 2) -> "LayerResolver":
        """Read the engine's layer map for this board and verify it on samples.

        The engine owns the mirror-id convention (``KiCadEngine.layer_map``:
        ``board_layer_order`` + ``board_to_human``), so that is the *source* of
        the mapping. Majority-voting over every track/via/pad was both slow
        (board-wide sampling: ~12 s on the V3 board) and weaker — on a board
        where most copper sits on one layer it would happily map an unrelated id
        onto it.

        What remains is a deliberately small verification: up to
        ``verify_samples`` single-layer pads per mirror id are looked up through
        the engine's own connectivity query, and the mapped human layer must be
        among the layers that query returns. A contradiction is refused rather
        than routed on.
        """
        engine = session._engine
        copper = int(engine.get_copper_layer_count())
        if copper < 1:
            raise LayerConventionError(f"board reports {copper} copper layers")

        layer_map = getattr(engine, "layer_map", None)
        if layer_map is None:
            raise LayerConventionError(
                "the engine exposes no layer map; refusing to guess the mirror "
                "copper-layer convention from board contents"
            )
        order = [int(layer) for layer in layer_map.board_layer_order]
        if len(order) != copper:
            raise LayerConventionError(
                f"engine layer map lists {len(order)} copper layers but the board "
                f"reports {copper}"
            )
        mapping = {
            board_layer: int(layer_map.board_to_human(board_layer))
            for board_layer in order
        }
        if len(set(mapping.values())) != len(mapping):
            raise LayerConventionError(f"engine layer map is not one-to-one: {mapping}")

        samples: dict[int, int] = {}
        unmapped: dict[int, str] = {}
        pads = list(engine.get_pads())
        for mirror_id, human in mapping.items():
            checked = 0
            for pad in pads:
                if checked >= max(0, int(verify_samples)):
                    break
                if int(pad.layer) != mirror_id:
                    continue
                layers = human_layers_at(session, float(pad.x_mm), float(pad.y_mm))
                checked += 1
                if layers and human not in layers:
                    raise LayerConventionError(
                        f"engine layer map says mirror id {mirror_id} is human layer "
                        f"{human}, but the connectivity query at "
                        f"({pad.x_mm}, {pad.y_mm}) reports {sorted(layers)}; refusing "
                        "to route on a contradictory mapping"
                    )
            if checked:
                samples[mirror_id] = checked
            else:
                unmapped[mirror_id] = "no single-layer pad sample on this board"

        return cls(
            copper_layers=copper, mapping=mapping, samples=samples, unmapped=unmapped,
        )

    def human(self, mirror_layer: int) -> int | None:
        """Human layer for a mirror id; ``None`` for the spans-copper sentinel."""
        if int(mirror_layer) == self.SPANS_COPPER:
            return None
        return self.mapping.get(int(mirror_layer))

    def require_human(self, mirror_layer: int) -> int:
        human = self.human(mirror_layer)
        if human is None:
            raise LayerConventionError(
                f"mirror layer {mirror_layer} is not a single copper layer"
            )
        return human

    def to_evidence(self) -> dict[str, Any]:
        evidence = {
            "copper_layers": self.copper_layers,
            "source": self.source,
            "mirror_to_human": {str(k): v for k, v in sorted(self.mapping.items())},
            "verified_samples": {str(k): v for k, v in sorted(self.samples.items())},
            "validated": bool(self.samples),
            "note": (
                "mirror ids read from the engine's own board layer map and verified "
                "against the connectivity query on a sample per layer"
                if self.samples else
                "the engine's layer map was read but no single-layer pad sample was "
                "available to verify it on this board"
            ),
            "spans_copper_sentinel": self.SPANS_COPPER,
        }
        if self.unmapped:
            evidence["unmapped_mirror_ids"] = {
                str(k): v for k, v in sorted(self.unmapped.items())
            }
        return evidence


def human_layers_at(session: AgentSession, x_mm: float, y_mm: float) -> tuple[int, ...]:
    """Human layers with copper at a point, straight from the connectivity query."""
    engine = session._engine
    layers = int(engine.get_copper_layer_count())
    return tuple(
        human for human in range(1, layers + 1) if session.cluster(x_mm, y_mm, human)
    )


def edge_key(
    start: Sequence[float], target: Sequence[float],
) -> tuple[float, float, int, float, float, int]:
    """A canonical, direction-tolerant key for one connection.

    The two endpoints are rounded to 3 decimals (the scan's anchors carry float
    noise) and sorted, so ``A -> B`` and ``B -> A`` are the same key. This is the
    identifier an operator uses to pin an exact edge, and the same one the
    selector compares against, so a pinned edge is matched by *identity* rather
    than by the net it happens to belong to.
    """
    first = (round(float(start[0]), 3), round(float(start[1]), 3), int(start[2]))
    second = (round(float(target[0]), 3), round(float(target[1]), 3), int(target[2]))
    low, high = (first, second) if first <= second else (second, first)
    return (low[0], low[1], low[2], high[0], high[1], high[2])


@dataclass(frozen=True)
class NetPair:
    """One outstanding same-net connection: two copper groups to join."""

    net_code: int
    net_name: str
    start: tuple[float, float, int]
    target: tuple[float, float, int]
    gap_mm: float
    start_layers: tuple[int, ...] = ()
    target_layers: tuple[int, ...] = ()
    pad_groups: int = 0
    # line          — two distinct anchors to join with copper (the common case,
    #   including a short cross-layer one: the plan builder inserts a via and a
    #   layer switch, which the engine can and does execute),
    # coincident_same_layer — anchors on the same point and layer (nothing to
    #   route: the two ends are already the same point),
    # coincident_cross_layer — anchors on the same point on different layers:
    #   a via bridge, executed by the plan builder's via + layer switch.
    # Only ``coincident_same_layer`` is unroutable. The threshold below says
    # "these anchors are the same point"; it is not a claim about engine
    # capability (an earlier 0.25 mm threshold did exactly that and hid real
    # connections — see docs/agent-work/reliability/phase3/RESULT.md).
    pair_kind: str = "line"
    #: ``ratsnest`` — the engine drew this edge between these two anchors.
    #: ``component`` — the native terminal membership proved these two copper
    #: components of one net are still disconnected, and these anchors stand for
    #: them. A component offer is a *candidate substitution* for a connection the
    #: ratsnest could not name; it is never a claim that the offered edge resolved.
    source: str = "ratsnest"
    #: Native identities of the two components an offer stands for (empty for a
    #: ratsnest edge). Recorded with every attempt, so duplicate suppression is
    #: bound to component membership as well as to geometry.
    component_start: str = ""
    component_target: str = ""
    #: True when at least one anchor was substituted off the offered edge.
    substituted: bool = False
    #: The offered connection this pair was derived from, when it is not the
    #: offered geometry itself. A substitution replaces the offered edge's
    #: anchors, so ``key`` no longer names the edge the scan offered; this is the
    #: stable link back to it, and it is what lets an attempt record be
    #: attributed to the offered edge instead of the substituted geometry.
    #: Empty for the offered geometry itself (``key`` already names it).
    offered_key: tuple = ()

    @property
    def key(self) -> tuple:
        return (
            self.net_code,
            round(self.start[0], 3), round(self.start[1], 3), self.start[2],
            round(self.target[0], 3), round(self.target[1], 3), self.target[2],
        )

    def label(self) -> str:
        return (
            f"net {self.net_code} ({self.net_name}) "
            f"{self.start[:2]} L{self.start[2]} -> {self.target[:2]} L{self.target[2]}"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "net_code": self.net_code,
            "net_name": self.net_name,
            "start": list(self.start),
            "target": list(self.target),
            "gap_mm": round(self.gap_mm, 3),
            "start_layers": list(self.start_layers),
            "target_layers": list(self.target_layers),
            "pad_groups_on_net": self.pad_groups,
            "pair_kind": self.pair_kind,
            "source": self.source,
            "component_start": self.component_start,
            "component_target": self.component_target,
            "substituted": bool(self.substituted),
            "offered_key": list(self.offered_key),
        }

    @property
    def routable(self) -> bool:
        return self.pair_kind != "coincident_same_layer"


#: Numerical coincidence tolerance only (1 nm). Small but distinct anchors remain
#: line-routing work; a tolerance must not turn physically separate points into
#: an unroutable same-layer pair.
COINCIDENT_EPS_MM = 0.000001


def _anchor_layer(
    session: AgentSession, x_mm: float, y_mm: float, mirror_layer: int,
    resolver: LayerResolver, *, other_layer: int | None = None,
    net_code: int | None = None,
) -> int:
    """Authoritative human layer for one ratsnest anchor.

    The engine's own layer map answers this for a single-layer anchor. The one
    genuinely ambiguous case is an anchor that *spans copper* (``-2``, a
    through-hole item): it exists on several layers, so no mirror id names one.
    For those the caller's ``other_layer`` is used **only** when the engine's
    connectivity query confirms copper on that layer at this exact point. If that
    layer has no copper (a via anchor whose span excludes the other end's layer),
    the fallback is the first layer with copper there whose *net* the engine
    reports as this pair's net — never "some layer that happens to have copper",
    which may belong to another net.
    """
    human = resolver.human(mirror_layer)
    if human is not None:
        return human
    if int(mirror_layer) != LayerResolver.SPANS_COPPER:
        raise LayerConventionError(
            f"anchor layer {mirror_layer} is not in the engine's layer map "
            f"({sorted(resolver.mapping)})"
        )
    if other_layer is not None and session.cluster(x_mm, y_mm, int(other_layer)):
        try:
            if net_code is not None and session.net_at(
                x_mm, y_mm, int(other_layer)
            ) == int(net_code):
                return int(other_layer)
        except Exception:  # noqa: BLE001 - unresolved identity is not an answer
            pass
    if net_code is not None:
        for human in sorted(set(resolver.mapping.values())):
            if not session.cluster(x_mm, y_mm, human):
                continue
            try:
                if session.net_at(x_mm, y_mm, human) == int(net_code):
                    return int(human)
            except Exception:  # noqa: BLE001 - an unreadable net is not an answer
                continue
    raise LayerConventionError(
        f"through-hole anchor at ({x_mm}, {y_mm}) spans copper and the other "
        "anchor's layer has no copper there, and no layer with copper carries "
        "this pair's net; no authoritative layer to route on"
    )


@dataclass(frozen=True)
class PairScan:
    """What one look at the board's outstanding connections produced.

    ``pairs`` is what the caller may work on; everything else explains what was
    left out and why, so "the queue is empty" is never confused with "the board
    is routed".
    """

    pairs: tuple[NetPair, ...]
    edges: int
    skipped_min_gap: int = 0
    skipped_layer: int = 0
    skipped_by_caller: int = 0
    skipped_unroutable: int = 0
    skipped_net_cap: int = 0
    dropped_by_max_pairs: int = 0
    #: Offers that came from native component membership rather than from a
    #: ratsnest edge (candidate substitutions, not rescued edges).
    components_offered: int = 0
    #: What the component windows examined and what they left out (see
    #: :class:`ComponentPairScan`). Empty when the source is disabled.
    component_coverage: dict[str, Any] = field(default_factory=dict)
    #: Offered edges whose unprovable anchor was replaced by a proved substitute.
    substitutions_offered: int = 0
    #: Pairs admitted past the per-net cap by the coverage pass, because their
    #: net's capped representative had already been attempted on this board and
    #: nothing else on that net had ever been offered (see ``fresh``).
    coverage_promoted: int = 0
    #: Offered edges whose anchor carries no copper of the scheduled net and for
    #: which no substitute could be proved. They stay in the queue exactly as the
    #: ratsnest drew them, so the runner records the refusal with its geometry
    #: rather than the pair vanishing from the count.
    unresolved_offers: int = 0
    layer_errors: tuple[str, ...] = ()

    def to_evidence(self) -> dict[str, Any]:
        return {
            "edges": self.edges,
            "offered": len(self.pairs),
            "skipped_min_gap": self.skipped_min_gap,
            "skipped_layer": self.skipped_layer,
            "skipped_by_caller": self.skipped_by_caller,
            "skipped_unroutable": self.skipped_unroutable,
            "skipped_net_cap": self.skipped_net_cap,
            "dropped_by_max_pairs": self.dropped_by_max_pairs,
            "components_offered": self.components_offered,
            "component_coverage": {
                key: value for key, value in (self.component_coverage or {}).items()
                if key != "per_net"
            },
            "substitutions_offered": self.substitutions_offered,
            "coverage_promoted": self.coverage_promoted,
            "unresolved_offers": self.unresolved_offers,
            "layer_errors": list(self.layer_errors[:8]),
        }


def _edge_gap(edge: Any) -> float:
    return math.hypot(
        float(edge.x1_mm) - float(edge.x2_mm), float(edge.y1_mm) - float(edge.y2_mm)
    )


def _pair_rank_key(pair: "NetPair") -> tuple:
    """Queue order for one pair: ordinary connections before degenerate bridges.

    A cross-layer pair whose anchors are literally the same point is a genuine
    connection (a via bridge), but it is the narrowest case there is: the router
    has to drop a via exactly where copper already exists on both layers. Measured
    on the V3 board, leading the queue with those spent an entire attempt budget
    on ten degenerate pairs while ordinary short connections - which do close -
    were never reached. They are still attempted, just after the pairs that
    describe a corridor.
    """
    degenerate = 1 if pair.pair_kind == "coincident_cross_layer" else 0
    return (degenerate, pair.gap_mm, pair.start[0], pair.start[1],
            pair.target[0], pair.target[1])


def scan_net_pairs(
    session: AgentSession,
    *,
    resolver: LayerResolver | None = None,
    max_pairs: int | None = None,
    max_per_net: int = 4,
    min_gap_mm: float = 0.0,
    skip: Callable[[NetPair], bool] | None = None,
    include_components: bool = True,
    max_components_per_net: int = 12,
    max_component_pairs_per_net: int = 4,
    component_window: int = 0,
    include_substitutions: bool = True,
    max_substitutions_per_pair: int = 3,
    substitution_radius_mm: float | None = None,
    fresh: Callable[[NetPair], bool] | None = None,
) -> PairScan:
    """Every outstanding same-net connection, offered fairly.

    Source of truth is the engine's ratsnest: one edge is one pair of anchors on
    the same net that are not yet connected.

    Selection rules, in order:

    * layers come from the engine's layer map (a pair whose anchors cannot be
      resolved is counted in ``skipped_layer`` with its reason, never dropped
      silently);
    * ``skip`` lets the caller filter pairs it has already exhausted — this is
      applied *before* the per-net cap so a net whose shortest pair is exhausted
      still offers its next one;
    * the queue is **round-robin across nets** (each net's pairs shortest-first,
      nets ordered by their shortest pair) so a large net cannot monopolise the
      attempt budget and every net's best pair is offered before any net's
      second.

    With ``include_components`` the engine's *native component membership* adds
    offers the ratsnest could not name: for every net with more than one proved
    copper component, the nearest distinct component pairs are enumerated
    (bounded by ``max_components_per_net`` / ``max_component_pairs_per_net``) and
    merged into the same fair queue. Those offers carry ``source="component"``
    and their component identities, and they are deduplicated against the
    ratsnest offers by geometry, so one physical connection is one offer.

    With ``include_substitutions`` an offered edge whose anchor carries no copper
    of the scheduled net is replaced by the substitutions
    :func:`reanchor_pair_variants` finds for it (``source="substitution"``,
    ``substituted=True``, component identities attached): the edge cannot be
    routed from as drawn, and retiring the whole connection because of that drew
    conclusions from a point. ``substitution_radius_mm=None`` leaves the search
    for the substitute component unbounded, because distance is not evidence
    about which connection is missing. An edge with no substitution at all is
    counted in ``skipped_unprovable`` and left out - it is exactly as unroutable
    as before, and the count says so instead of hiding it.

    A substitute carries ``offered_key`` - the key of the edge it stands for - so
    an attempt on it can be attributed back to the offered connection rather than
    to geometry the ratsnest never drew. It remains a candidate offer: it is
    never a claim that the offered edge resolved.

    ``max_per_net`` bounds what one net contributes in a single scan; the next
    scan picks up that net's remaining pairs once the earlier ones are skipped.

    ``fresh`` is the coverage pass. A per-net cap decides which of a net's pairs
    is offered, and when that representative is a pair the caller has *already
    attempted* on this board, the net's other pairs are invisible: the capped slot
    is spent on a pair with a full history and the never-tried connection behind
    it is never offered. With ``fresh`` supplied, a net whose capped
    representative is not fresh additionally contributes its first fresh pair,
    bounded to one extra pair per net. Nothing is dropped to make room, so this
    only widens the queue; ``coverage_promoted`` reports how many pairs came in
    this way. ``fresh=None`` restores the plain capped queue exactly.
    """
    engine = session._engine
    resolver = resolver or LayerResolver.build(session)
    net_names = engine.get_net_names()
    groups = engine.get_pad_groups()
    edges = list(engine.get_ratsnest())

    skipped_min_gap = 0
    skipped_layer = 0
    skipped_by_caller = 0
    skipped_unroutable = 0
    layer_errors: list[str] = []
    by_net: dict[int, list[NetPair]] = {}

    for edge in edges:
        net = int(edge.net_code)
        gap = _edge_gap(edge)
        if gap < min_gap_mm:
            skipped_min_gap += 1
            continue
        try:
            start_layer = _anchor_layer(
                session, float(edge.x1_mm), float(edge.y1_mm), int(edge.layer1),
                resolver, other_layer=resolver.human(int(edge.layer2)),
                net_code=net,
            )
            target_layer = _anchor_layer(
                session, float(edge.x2_mm), float(edge.y2_mm), int(edge.layer2),
                resolver, other_layer=start_layer, net_code=net,
            )
        except LayerConventionError as exc:
            skipped_layer += 1
            layer_errors.append(str(exc))
            continue

        if gap <= COINCIDENT_EPS_MM:
            kind = ("coincident_same_layer" if start_layer == target_layer
                    else "coincident_cross_layer")
        else:
            kind = "line"
        pair = NetPair(
            net_code=net,
            net_name=str(net_names.get(net, f"NET{net}")),
            start=(float(edge.x1_mm), float(edge.y1_mm), start_layer),
            target=(float(edge.x2_mm), float(edge.y2_mm), target_layer),
            gap_mm=gap,
            start_layers=(),
            target_layers=(),
            pad_groups=int(groups.get(net, 0)),
            pair_kind=kind,
        )
        if not pair.routable:
            skipped_unroutable += 1
            continue
        if skip is not None and skip(pair):
            skipped_by_caller += 1
            continue
        by_net.setdefault(net, []).append(pair)

    components_offered = 0
    component_coverage: dict[str, Any] = {}
    if include_components:
        existing = {
            net: {pair.key for pair in pairs} for net, pairs in by_net.items()
        }
        component_scan = component_pairs(
            session, resolver=resolver, min_gap_mm=min_gap_mm,
            max_components_per_net=max_components_per_net,
            max_pairs_per_net=max_component_pairs_per_net,
            window=component_window,
        )
        component_coverage = dict(component_scan.coverage)
        for net, pairs in component_scan.items():
            known = existing.setdefault(net, set())
            for pair in pairs:
                if pair.key in known:
                    continue
                if skip is not None and skip(pair):
                    skipped_by_caller += 1
                    continue
                known.add(pair.key)
                by_net.setdefault(net, []).append(pair)
                components_offered += 1

    for pairs in by_net.values():
        pairs.sort(key=_pair_rank_key)
    # Nets ordered by their own best pair, so every net's best connection is
    # considered before any net's second while still guaranteeing a turn each.
    net_order = sorted(by_net, key=lambda net: (_pair_rank_key(by_net[net][0]), net))

    offered: list[NetPair] = []
    skipped_net_cap = 0
    dropped_by_max_pairs = 0
    depth = 0
    while True:
        any_at_depth = False
        for net in net_order:
            bucket = by_net[net]
            if depth >= len(bucket):
                continue
            any_at_depth = True
            if depth >= max_per_net:
                skipped_net_cap += 1
                continue
            if max_pairs is not None and len(offered) >= max_pairs:
                dropped_by_max_pairs += 1
                continue
            offered.append(bucket[depth])
        if not any_at_depth:
            break
        depth += 1

    # Coverage pass: give each net one *fresh* pair when its capped
    # representative carries no reason to be preferred - it has already been
    # attempted on this board, and the connection behind it has not. Bounded to
    # one extra pair per net so widening the queue can never become a sweep of
    # one net's whole pair list.
    coverage_promoted = 0
    if fresh is not None:
        admitted = {pair.key for pair in offered}
        for net in net_order:
            if any(fresh(pair) for pair in offered
                   if int(pair.net_code) == int(net)):
                continue
            for pair in by_net[net]:
                if pair.key in admitted or not fresh(pair):
                    continue
                if max_pairs is not None and len(offered) >= max_pairs:
                    dropped_by_max_pairs += 1
                    break
                admitted.add(pair.key)
                offered.append(pair)
                coverage_promoted += 1
                break

    substitutions_offered = 0
    unresolved_offers = 0
    if include_substitutions:
        substituted: list[NetPair] = []
        known_keys: set[tuple] = set()
        for pair in offered:
            if pair.source != "ratsnest":
                substituted.append(pair)
                known_keys.add(pair.key)
                continue
            try:
                provable = (
                    session.net_at(*pair.start) == int(pair.net_code)
                    and session.net_at(*pair.target) == int(pair.net_code)
                )
            except Exception:           # noqa: BLE001 - unreadable is not proof
                provable = False
            if provable:
                substituted.append(pair)
                known_keys.add(pair.key)
                continue
            variants = reanchor_pair_variants(
                session, pair, resolver=resolver,
                component_radius_mm=substitution_radius_mm,
                limit=max_substitutions_per_pair,
            )
            if not variants:
                # No substitute could be *proved*. The offer is left exactly as
                # the ratsnest drew it so the runner records the refusal with its
                # geometry - a pair is named, never silently skipped because a
                # point was empty.
                unresolved_offers += 1
                substituted.append(pair)
                known_keys.add(pair.key)
                continue
            for variant, evidence in variants:
                relation = str(evidence.get("offer_relation") or OFFER_ORIGINAL)
                components = dict(evidence.get("components") or {})
                offer = replace(
                    variant,
                    source=("substitution" if relation == OFFER_SUBSTITUTION
                            else "ratsnest"),
                    substituted=(relation == OFFER_SUBSTITUTION),
                    component_start=str(components.get("start") or ""),
                    component_target=str(components.get("target") or ""),
                    # The offered edge this variant stands for. A substitution
                    # moved the anchors, so the variant's own key cannot name the
                    # edge the scan drew; carrying it here is what makes the
                    # attempt attributable to the offered edge.
                    offered_key=tuple(pair.key),
                )
                if offer.key in known_keys:
                    continue
                if skip is not None and skip(offer):
                    skipped_by_caller += 1
                    continue
                known_keys.add(offer.key)
                substituted.append(offer)
                substitutions_offered += 1
        offered = substituted

    # Anchor-layer and pad-group detail is only needed for what is offered, so it
    # costs a bounded number of connectivity queries rather than one per edge.
    anchor_owner: dict[tuple[float, float, int], str] = {}
    if include_components:
        for items in native_components(session, resolver=resolver).values():
            for component in items:
                for anchor in component.anchors:
                    anchor_owner.setdefault(_round_anchor(anchor),
                                            component.component_id)

    def component_id_at(point: tuple[float, float, int]) -> str:
        """Component identity of the copper under a point, or ``""``.

        Proved through the engine's own cluster: the cluster's anchors are matched
        back to the anchors the terminal partition named, so an attempt record can
        say which component it was measured against instead of leaving the
        membership to be inferred from coordinates.
        """
        if not anchor_owner:
            return ""
        try:
            cluster = session.cluster(point[0], point[1], point[2])
        except Exception:               # noqa: BLE001 - unreadable is not proof
            return ""
        for key in cluster:
            owner = anchor_owner.get(_round_anchor(key))
            if owner:
                return owner
        return ""

    decorated = tuple(
        replace(
            pair,
            start_layers=human_layers_at(session, pair.start[0], pair.start[1]),
            target_layers=human_layers_at(session, pair.target[0], pair.target[1]),
            component_start=(pair.component_start
                             or component_id_at(pair.start)),
            component_target=(pair.component_target
                              or component_id_at(pair.target)),
        )
        for pair in offered
    )
    return PairScan(
        pairs=decorated,
        edges=len(edges),
        skipped_min_gap=skipped_min_gap,
        skipped_layer=skipped_layer,
        skipped_by_caller=skipped_by_caller,
        skipped_unroutable=skipped_unroutable,
        skipped_net_cap=skipped_net_cap,
        dropped_by_max_pairs=dropped_by_max_pairs,
        components_offered=components_offered,
        component_coverage=component_coverage,
        substitutions_offered=substitutions_offered,
        coverage_promoted=coverage_promoted,
        unresolved_offers=unresolved_offers,
        layer_errors=tuple(layer_errors),
    )


def enumerate_net_pairs(
    session: AgentSession,
    *,
    resolver: LayerResolver | None = None,
    max_pairs: int | None = None,
    max_per_net: int = 4,
    min_gap_mm: float = 0.0,
    skip: Callable[[NetPair], bool] | None = None,
) -> list[NetPair]:
    """The offered pairs from :func:`scan_net_pairs` (see it for the rules)."""
    return list(scan_net_pairs(
        session, resolver=resolver, max_pairs=max_pairs, max_per_net=max_per_net,
        min_gap_mm=min_gap_mm, skip=skip,
    ).pairs)


def _point_segment_distance(
    px: float, py: float, x1: float, y1: float, x2: float, y2: float
) -> float:
    dx, dy = x2 - x1, y2 - y1
    if dx == 0.0 and dy == 0.0:
        return math.hypot(px - x1, py - y1)
    t = max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - (x1 + t * dx), py - (y1 + t * dy))


def nearest_obstacles(
    session: AgentSession,
    x_mm: float,
    y_mm: float,
    net_code: int,
    *,
    resolver: LayerResolver | None = None,
    radius_mm: float = 3.0,
    limit: int = 8,
) -> list[dict[str, Any]]:
    """The nearest foreign-net copper to a point, as compact records.

    ``net_code`` is the connection being planned: anything on a different net is
    an obstacle. Protected copper (locked items, power nets) is not distinguished
    here — the gate refuses a plan that displaces it — but the planner is told the
    net name so a human-readable prompt makes the risk visible.
    """
    engine = session._engine
    resolver = resolver or LayerResolver.build(session)
    net_names = engine.get_net_names()
    found: list[tuple[float, dict[str, Any]]] = []

    for track in engine.get_tracks():
        if int(track.net_code) == int(net_code):
            continue
        distance = _point_segment_distance(
            x_mm, y_mm, float(track.x1_mm), float(track.y1_mm),
            float(track.x2_mm), float(track.y2_mm),
        )
        if distance > radius_mm:
            continue
        found.append((distance, {
            "kind": "track",
            "net_code": int(track.net_code),
            "net_name": str(net_names.get(int(track.net_code), "")),
            "layer": resolver.human(track.layer),
            "distance_mm": round(distance, 3),
            "width_mm": round(float(track.width_mm), 3),
            # Endpoints, so a caller can place a waypoint that clears the
            # segment rather than only knowing how far away it is.
            "x1_mm": round(float(track.x1_mm), 3),
            "y1_mm": round(float(track.y1_mm), 3),
            "x2_mm": round(float(track.x2_mm), 3),
            "y2_mm": round(float(track.y2_mm), 3),
            "uuid": str(track.uuid)[:8],
        }))
    for via in engine.get_vias():
        if int(via.net_code) == int(net_code):
            continue
        distance = math.hypot(x_mm - float(via.x_mm), y_mm - float(via.y_mm))
        if distance > radius_mm:
            continue
        found.append((distance, {
            "kind": "via",
            "net_code": int(via.net_code),
            "net_name": str(net_names.get(int(via.net_code), "")),
            "layer": None,
            "layers": [
                resolver.human(via.top_layer), resolver.human(via.bottom_layer)
            ],
            "distance_mm": round(distance, 3),
            "x_mm": round(float(via.x_mm), 3),
            "y_mm": round(float(via.y_mm), 3),
            "diameter_mm": round(float(via.diameter_mm), 3),
            "drill_mm": round(float(via.drill_mm), 3),
            "uuid": str(via.uuid)[:8],
        }))
    for pad in engine.get_pads():
        if int(pad.net_code) in (int(net_code), -1, 0):
            continue
        distance = math.hypot(x_mm - float(pad.x_mm), y_mm - float(pad.y_mm))
        if distance > radius_mm:
            continue
        found.append((distance, {
            "kind": "pad",
            "net_code": int(pad.net_code),
            "net_name": str(pad.net_name),
            "layer": resolver.human(pad.layer),
            "distance_mm": round(distance, 3),
            "x_mm": round(float(pad.x_mm), 3),
            "y_mm": round(float(pad.y_mm), 3),
            "size_mm": [round(float(pad.width_mm), 3), round(float(pad.height_mm), 3)],
            "pad": f"{pad.footprint_ref}.{pad.pad_name}",
        }))
    found.sort(key=lambda item: item[0])
    return [record for _distance, record in found[:limit]]


@dataclass(frozen=True)
class AnchorResolution:
    """A copper anchor whose net the engine can prove, and *how* it proved it.

    ``pad`` — a pad on this layer sits in the cluster (the strongest proof).
    ``copper`` — the cluster carries no pad, but every native copper item anchored
    in it (tracks, arcs, vias) reports the same non-zero net, so the cluster's net
    is the engine's own attribution rather than a guess.
    ``pour_sample`` — a sample inside a same-net zone that covers the point; the
    engine proves the copper under *the sample*, not that the zone is the one the
    ratsnest meant.
    ``component_membership`` — an item of the scheduled net's own inventory,
    proved by the same cluster rule; the nearest copper of that net, which is not
    the same claim as "the anchor the ratsnest pointed at".

    ``provenance`` says which of those claims this is. ``original`` means the
    resolution is *the ratsnest anchor itself* (only its net needed naming), so
    the scheduler is still working on the connection the scan offered.
    ``candidate`` means the endpoint was **moved** to other copper of the same
    net: an honest new same-net candidate, never a claim about the original
    endpoint. Every consumer that reports a re-anchor must carry this through.
    """

    point: tuple[float, float, int]
    net_code: int
    proof: str
    cluster_items: int = 0
    provenance: str = "original"
    #: Distance in mm the endpoint was moved, for ``candidate`` provenance.
    moved_mm: float = 0.0
    #: Native component identity this anchor belongs to, when membership was
    #: available. Empty when the anchor was proved through the cluster rule only.
    component_id: str = ""
    #: True when this component is already connected to the *other* endpoint of
    #: the connection being substituted for: offering it would route nothing.
    already_connected: bool = False

    def to_evidence(self) -> dict[str, Any]:
        return {
            "x_mm": round(float(self.point[0]), 4),
            "y_mm": round(float(self.point[1]), 4),
            "layer": int(self.point[2]),
            "net_code": int(self.net_code),
            "proof": self.proof,
            "cluster_items": int(self.cluster_items),
            "provenance": self.provenance,
            "moved_mm": round(float(self.moved_mm), 4),
            "component_id": self.component_id,
            "already_connected": bool(self.already_connected),
        }


class CopperAnchorIndex:
    """Native copper items indexed by the connectivity anchors the engine reports.

    ``get_connected_points`` returns *anchors* (pad/via centres and track
    endpoints), not item identity, so attributing a cluster's net means matching
    those anchors back to the items the observation layer already reads. That scan
    is over every track/via/pad, so it is built once per board read and reused.
    """

    def __init__(self) -> None:
        self._by_key: dict[tuple[float, float, int], list[tuple[str, int]]] = {}

    @classmethod
    def build(cls, session: AgentSession, *, resolver: LayerResolver | None = None
              ) -> "CopperAnchorIndex":
        engine = session._engine
        resolver = resolver or LayerResolver.build(session)
        index = cls()

        def add(point: tuple[float, float], layer: int, kind: str, net: int) -> None:
            key = _round_anchor((point[0], point[1], layer))
            index._by_key.setdefault(key, []).append((kind, int(net)))

        for track in _items(engine, "get_tracks"):
            layer = _human_layer(resolver, int(track.layer))
            if layer is None:
                continue                # a layer this board does not map: no anchor
            for point in ((track.x1_mm, track.y1_mm), (track.x2_mm, track.y2_mm)):
                add((float(point[0]), float(point[1])), layer, "track",
                    int(track.net_code))
        for via in _items(engine, "get_vias"):
            for layer in _via_human_layers(engine, via, resolver):
                add((float(via.x_mm), float(via.y_mm)), layer, "via",
                    int(via.net_code))
        for pad in _items(engine, "get_pads"):
            for layer in _pad_human_layers(engine, pad, resolver):
                add((float(pad.x_mm), float(pad.y_mm)), layer, "pad",
                    int(pad.net_code))
        return index

    def entries(self, key: tuple[float, float, int]) -> tuple[tuple[str, int], ...]:
        return tuple(self._by_key.get(key, ()))

    def anchors_with_net(self, net_code: int) -> list[tuple[float, float, int]]:
        """Every anchor whose items all carry ``net_code`` and nothing else.

        This is the scheduled net's *own copper*, taken from the board inventory
        rather than from geometry: an anchor that also carries a foreign net (a
        track touching two nets, a hole) is excluded because its net would be
        ambiguous. Callers still have to prove each anchor with the engine's
        connectivity before using it - this only narrows the search space.
        """
        wanted = int(net_code)
        found: list[tuple[float, float, int]] = []
        for key, entries in self._by_key.items():
            nets = {net for _kind, net in entries if net > 0}
            if nets == {wanted}:
                found.append(key)
        return found

    def nets_in(self, anchors: Iterable[tuple[float, float, int]]
                ) -> tuple[set[int], int]:
        """``(non-zero nets, items carrying no net)`` for one cluster's anchors."""
        nets: set[int] = set()
        netless = 0
        for key in anchors:
            for _kind, net in self.entries(key):
                if net <= 0:
                    netless += 1
                else:
                    nets.add(net)
        return nets, netless


def _via_human_layers(engine: Any, via: Any, resolver: LayerResolver) -> tuple[int, ...]:
    """Human layers a via spans, from its own top/bottom board layers."""
    layer_map = getattr(engine, "layer_map", None)
    order = list(getattr(layer_map, "board_layer_order", ()) or ())
    try:
        top, bottom = int(via.top_layer), int(via.bottom_layer)
    except (TypeError, ValueError):
        return ()
    if order and top in order and bottom in order:
        low, high = sorted((order.index(top), order.index(bottom)))
        return tuple(
            human for human in (
                _human_layer(resolver, board_id) for board_id in order[low:high + 1])
            if human is not None)
    human = _human_layer(resolver, top)
    return () if human is None else (human,)


def _human_layer(resolver: LayerResolver, board_layer: int) -> int | None:
    """``resolver.human`` that reports an unmapped layer instead of raising."""
    try:
        return int(resolver.human(int(board_layer)))
    except Exception:                   # noqa: BLE001 - unmapped is not an anchor
        return None


def _items(engine: Any, accessor: str) -> list:
    """One of the engine's item getters, or an empty list when it has none."""
    getter = getattr(engine, accessor, None)
    if not callable(getter):
        return []
    try:
        return list(getter())
    except Exception:                   # noqa: BLE001 - unreadable is not proof
        return []


def _pad_human_layers(engine: Any, pad: Any, resolver: LayerResolver) -> tuple[int, ...]:
    """Human layers a pad occupies; a spans-copper pad covers every copper layer."""
    layer = int(pad.layer)
    if layer < 0:                       # the engine's spans-copper sentinel
        return tuple(range(1, int(engine.get_copper_layer_count()) + 1))
    human = _human_layer(resolver, layer)
    if human is not None:
        return (human,)
    # An engine that already reports human layers does not have this id in its
    # map; the value itself is then the layer.
    return (layer,) if layer > 0 else ()


def verified_anchor(
    session: AgentSession, x_mm: float, y_mm: float, layer: int, net_code: int,
    *, index: CopperAnchorIndex | None = None,
    resolver: LayerResolver | None = None,
    max_pad_probes: int = 8,
) -> AnchorResolution | None:
    """A point in this cluster whose net the engine proves is ``net_code``.

    Used where the ratsnest anchor is real copper the point-identity rule cannot
    name (a track-only cluster with no pad). The point is never guessed: the
    cluster comes from the engine's own connectivity query, and the net is either
    a pad's own net code or the unanimous net of every track/via the engine
    anchors in that cluster.
    """
    engine = session._engine
    try:
        points = list(engine.get_connected_points(float(x_mm), float(y_mm), int(layer)))
    except Exception:                   # noqa: BLE001 - unreadable is not a proof
        return None
    anchors = anchor_set([
        (p.x_mm, p.y_mm, p.layer) if hasattr(p, "x_mm")
        else (p[0], p[1], p[2])
        for p in points
    ])
    if not anchors:
        return None

    tried = 0
    for key in sorted(anchors):
        if tried >= max_pad_probes:
            break
        tried += 1
        try:
            endpoint = session.endpoint(*key)
        except Exception:               # noqa: BLE001 - no pad at this anchor
            continue
        if int(endpoint.net_code) == int(net_code):
            return AnchorResolution(point=key, net_code=int(net_code), proof="pad",
                                    cluster_items=len(anchors))

    index = index or CopperAnchorIndex.build(session, resolver=resolver)
    nets, netless = index.nets_in(anchors)
    if nets == {int(net_code)} and netless == 0:
        for key in sorted(anchors):
            if index.entries(key):
                return AnchorResolution(point=key, net_code=int(net_code),
                                        proof="copper",
                                        cluster_items=len(anchors))
    return None


def zone_anchor(
    session: AgentSession, point: tuple[float, float, int], net_code: int,
    *, index: CopperAnchorIndex | None = None,
    resolver: LayerResolver | None = None, samples: int = 16,
) -> AnchorResolution | None:
    """Re-anchor a point that sits in a same-net zone's void, or refuse.

    A ratsnest anchor can land inside a copper pour's clearance void (or a
    drill/hole) while the connection it represents is a pour-mediated one. The
    engine's own connectivity then has no cluster at the point, which used to
    make the whole endpoint unusable.

    This samples the same-net zone that *covers* the point (its layer and net come
    from the board's own zone inventory) and accepts a sample only when the
    engine proves the copper under it carries the scheduled net. To keep the
    answer unambiguous the samples must all belong to **one** connected
    component: two different same-net islands under one anchor cannot say which
    one the ratsnest meant, so the endpoint is refused rather than guessed.

    What the samples prove is that *the sample* is on the scheduled net's copper
    inside a covering same-net zone - not that the pour is the copper the ratsnest
    pointed at. The result is therefore labelled ``provenance="candidate"``: a new
    same-net endpoint the router may use, never a claim about the original one.
    """
    engine = session._engine
    x_mm, y_mm, layer = float(point[0]), float(point[1]), int(point[2])
    zones = []
    get_rows = getattr(engine, "get_board_items", None)
    if not callable(get_rows):
        return None                     # an engine without an inventory has no zones
    for row in get_rows():
        if row.kind != "zone" or int(row.net_code) != int(net_code):
            continue
        fields = str(row.physical_id).split("|")
        if len(fields) < 5:
            continue
        try:
            board_layer = int(fields[2])
            box_x, box_y, box_w, box_h = (
                float(value) / 1e6 for value in fields[4].split(","))
        except (TypeError, ValueError):
            continue
        if resolver is not None:
            try:
                human = resolver.human(board_layer)
            except Exception:           # noqa: BLE001 - not a copper layer
                continue
        else:
            human = board_layer
        if int(human) != layer:
            continue
        if not (box_x - 0.001 <= x_mm <= box_x + box_w + 0.001
                and box_y - 0.001 <= y_mm <= box_y + box_h + 0.001):
            continue
        zones.append((box_x, box_y, box_w, box_h))
    if not zones:
        return None

    side = max(2, int(math.sqrt(max(1, samples))))
    accepted: list[tuple[float, float, int]] = []
    components: list[frozenset] = []
    for box_x, box_y, box_w, box_h in zones:
        for row in range(side):
            for column in range(side):
                x = box_x + box_w * (column + 0.5) / side
                y = box_y + box_h * (row + 0.5) / side
                try:
                    cluster = session.cluster(x, y, layer)
                except Exception:       # noqa: BLE001 - no copper at the sample
                    continue
                if not cluster:
                    continue
                if index is not None:
                    nets, netless = index.nets_in(cluster)
                else:
                    nets, netless = set(), 0
                if nets != {int(net_code)} or netless:
                    continue
                if cluster not in components:
                    components.append(cluster)
                accepted.append((round(x, 4), round(y, 4), layer))
                if len(components) > 1:
                    return None             # ambiguous: refuse, never guess
        if accepted and len(components) == 1:
            break
    if not accepted or len(components) != 1:
        return None
    nearest = min(accepted, key=lambda candidate: (
        math.hypot(candidate[0] - x_mm, candidate[1] - y_mm), candidate))
    return AnchorResolution(point=nearest, net_code=int(net_code),
                            proof="pour", cluster_items=len(components[0]),
                            provenance="candidate",
                            moved_mm=math.hypot(nearest[0] - x_mm, nearest[1] - y_mm))


@dataclass(frozen=True)
class CopperComponent:
    """One connected copper component of one net, from native membership.

    Built from the engine's own terminal partition (``get_pad_cluster_members``
    over the complete pad inventory), not from sampling geometry. ``component_id``
    is that row's identity for this board; it is what makes "the same component"
    a checkable statement rather than a distance comparison.

    Copper with no pad at all is *not* invented here: a component with no
    terminal is invisible to the terminal partition, and this record never claims
    one exists. Such copper still reaches the router through
    ``CopperAnchorIndex`` and the same-cluster proof.
    """

    net_code: int
    net_name: str
    component_id: str
    #: Pad centres, the strongest possible anchor proof, nearest-first to the
    #: optional reference point the caller asked about, one entry per copper layer
    #: the terminal occupies. A bounded window of the whole set unless the
    #: component has fewer anchors than the caller's limit.
    anchors: tuple[tuple[float, float, int], ...]
    terminal_count: int
    layers: tuple[int, ...]
    bbox: tuple[float, float, float, float]
    #: How many anchors the component has in total, so a caller can report what a
    #: window left out instead of presenting a subset as the whole.
    anchor_total: int = 0

    @property
    def diagonal_mm(self) -> float:
        x0, y0, x1, y1 = self.bbox
        return math.hypot(x1 - x0, y1 - y0)

    def to_evidence(self) -> dict[str, Any]:
        return {
            "net_code": int(self.net_code),
            "component_id": self.component_id,
            "terminal_count": int(self.terminal_count),
            "anchor_total": int(self.anchor_total or len(self.anchors)),
            "anchors_offered": len(self.anchors),
            "layers": list(self.layers),
            "bbox": [round(value, 4) for value in self.bbox],
            "anchors": [[round(p[0], 4), round(p[1], 4), int(p[2])]
                        for p in self.anchors[:3]],
        }


def _first_human_layer(engine: Any, pad: Any, resolver: LayerResolver | None) -> int:
    """The copper layer a native pad anchor should be proved on."""
    layers = _pad_human_layers(engine, pad, resolver) if resolver is not None else ()
    if layers:
        return int(min(layers))
    human = _human_layer(resolver, int(pad.layer)) if resolver is not None else None
    if human is not None:
        return human
    return int(pad.layer) if int(pad.layer) > 0 else 1


def native_components(
    session: AgentSession, *,
    resolver: LayerResolver | None = None,
    reference: Mapping[int, tuple[float, float, int]] | None = None,
    max_anchors_per_component: int = 4,
    anchor_window: int = 0,
) -> dict[int, list[CopperComponent]]:
    """Every net's connected components, from the native terminal partition.

    ``reference`` optionally maps a net code to a point; each component's anchor
    list is then ordered by distance to it, so a caller asking "which component
    is this ratsnest endpoint about?" gets an answer in *components*, with the
    distance attached, instead of a nearest-anchor guess.

    A component's identity is derived from its **physical membership** (the
    sorted native physical ids of its terminals), not from the row index it
    happened to arrive in, so it stays the same identity across a reload or a
    re-ordered partition. Its anchors are expanded over the copper layers each
    terminal occupies, and ``anchor_window`` rotates which
    ``max_anchors_per_component`` of them are returned: a bounded window is a
    window, not the whole set, and the caller needs a way to see the rest.

    Returns ``{}`` when the engine cannot prove its terminal partition - an
    unreadable membership is not an empty board, and callers check for it.
    """
    engine = session._engine
    # Human layers come from the engine's own map, exactly as everywhere else:
    # a bare board-layer id would name the wrong copper on a four-layer board.
    resolver = resolver or LayerResolver.build(session)
    rows = getattr(engine, "get_pad_cluster_members", None)
    if not callable(rows):
        return {}
    try:
        pads = list(engine.get_pads())
    except Exception:                   # noqa: BLE001 - unreadable is not proof
        return {}
    by_physical: dict[str, Any] = {}
    for pad in pads:
        physical = str(getattr(pad, "physical_id", "") or "").strip()
        if physical:
            by_physical[physical] = pad
    net_names = {}
    try:
        net_names = {int(k): str(v) for k, v in engine.get_net_names().items()}
    except Exception:                   # noqa: BLE001
        net_names = {}

    components: dict[int, list[CopperComponent]] = {}
    try:
        cluster_rows = list(rows())
    except Exception:                   # noqa: BLE001
        return {}
    for row_index, row in enumerate(cluster_rows):
        try:
            net_code, members = int(row[0]), list(row[1])
        except Exception:               # noqa: BLE001 - unknown row shape
            return {}
        anchors: list[tuple[float, float, int]] = []
        layers: set[int] = set()
        xs: list[float] = []
        ys: list[float] = []
        for physical in members:
            pad = by_physical.get(str(physical))
            if pad is None:
                return {}               # a membership naming unknown copper
            x_mm, y_mm = float(pad.x_mm), float(pad.y_mm)
            pad_layers = _pad_human_layers(engine, pad, resolver) or (
                _first_human_layer(engine, pad, resolver),
            )
            # One anchor per layer the terminal actually occupies: a through-hole
            # pad is a legal endpoint on every copper layer it spans, and keeping
            # only the first hides the others from every caller.
            for layer in sorted(set(int(item) for item in pad_layers)):
                anchors.append((x_mm, y_mm, layer))
            xs.append(x_mm)
            ys.append(y_mm)
            layers.update(int(item) for item in pad_layers)
        if not anchors:
            continue
        point = (reference or {}).get(int(net_code))
        if point is not None:
            anchors.sort(key=lambda item: (
                math.hypot(item[0] - float(point[0]), item[1] - float(point[1])),
                item,
            ))
        anchor_total = len(anchors)
        anchors = _rotated_window(anchors, anchor_window,
                                  max_anchors_per_component)
        membership_digest = hashlib.sha1(
            "|".join(sorted(str(item) for item in members)).encode("utf-8")
        ).hexdigest()[:12]
        components.setdefault(int(net_code), []).append(CopperComponent(
            net_code=int(net_code),
            net_name=str(net_names.get(int(net_code), f"NET{net_code}")),
            component_id=f"net:{net_code}:mem:{membership_digest}",
            anchors=tuple(anchors),
            anchor_total=anchor_total,
            terminal_count=len(members),
            layers=tuple(sorted(layers)),
            bbox=(min(xs), min(ys), max(xs), max(ys)),
        ))
    return components


def _rotated_window(items: Sequence[Any], window: int, limit: int) -> list[Any]:
    """``limit`` items from ``items``, starting at ``window`` and wrapping.

    The rotation is what makes a bounded window *fair*: an unchanged board read
    with window 0, then 1, then 2 surfaces a different subset each time instead of
    the same first N forever. Returns everything when the list is shorter than the
    limit, and an empty list when the limit is 0.
    """
    if limit <= 0 or not items:
        return []
    if len(items) <= limit:
        return list(items)
    start = int(window) % len(items)
    return [items[(start + offset) % len(items)] for offset in range(limit)]


def component_anchor_candidates(
    session: AgentSession, point: tuple[float, float, int], net_code: int,
    *, index: CopperAnchorIndex | None = None,
    resolver: LayerResolver | None = None,
    radius_mm: float | None = 4.0, limit: int = 3, max_probes: int = 24,
    peer: tuple[float, float, int] | None = None,
) -> list[AnchorResolution]:
    """Proved anchors on the scheduled net's own copper, one per component.

    A ratsnest anchor can land where the board has no copper of that net at all.
    That says nothing about whether the *connection* is routable: the net's
    terminals are elsewhere on the same copper, and the engine can still bridge a
    different pair of its components.

    Candidates come from the **native component membership**
    (:func:`native_components`): one proved anchor per component, ordered by
    distance from ``point``. ``radius_mm=None`` leaves the search unbounded, which
    matters when the nearest component is far away - the membership, not a
    distance threshold, decides what exists. ``peer`` is the other endpoint of
    the connection: a component already connected to it is reported through
    ``already_connected`` on the resolution rather than offered as a route that
    would close nothing.

    Every result is ``provenance="candidate"`` with ``component_id`` and
    ``moved_mm``: it is copper of the same net offered as a substitute endpoint,
    never a claim that the ratsnest meant it.
    """
    components = native_components(session, resolver=resolver,
                                   reference={int(net_code): point}) \
        .get(int(net_code), [])
    if not components:
        # No provable terminal partition for this net: fall back to the anchor
        # index, which still proves each anchor through the cluster rule. This is
        # strictly weaker evidence (no component identity) and is labelled so.
        return _anchor_candidates_from_index(
            session, point, int(net_code), index=index, resolver=resolver,
            radius_mm=radius_mm, limit=limit, max_probes=max_probes,
        )

    peer_cluster = frozenset()
    if peer is not None:
        try:
            peer_cluster = session.cluster(float(peer[0]), float(peer[1]),
                                           int(peer[2]))
        except Exception:               # noqa: BLE001 - unreadable is not proof
            peer_cluster = frozenset()

    found: list[AnchorResolution] = []
    probed = 0
    for component in components:
        if len(found) >= limit or probed >= max_probes:
            break
        for anchor in component.anchors:
            if probed >= max_probes:
                break
            distance = math.hypot(anchor[0] - float(point[0]),
                                  anchor[1] - float(point[1]))
            if radius_mm is not None and distance > float(radius_mm):
                continue
            probed += 1
            resolution = verified_anchor(
                session, anchor[0], anchor[1], int(anchor[2]), int(net_code),
                index=index, resolver=resolver,
            )
            if resolution is None:
                continue
            try:
                cluster = session.cluster(*resolution.point)
            except Exception:           # noqa: BLE001 - unreadable is not proof
                continue
            found.append(replace(
                resolution, provenance="candidate",
                cluster_items=len(cluster),
                moved_mm=math.hypot(resolution.point[0] - float(point[0]),
                                    resolution.point[1] - float(point[1])),
                component_id=component.component_id,
                already_connected=bool(
                    peer_cluster and cluster and (cluster & peer_cluster)
                ),
            ))
            break                       # one candidate per component
        if len(found) >= limit:
            break
    return found


def _anchor_candidates_from_index(
    session: AgentSession, point: tuple[float, float, int], net_code: int,
    *, index: CopperAnchorIndex | None, resolver: LayerResolver | None,
    radius_mm: float | None, limit: int, max_probes: int,
) -> list[AnchorResolution]:
    """Component-less fallback: proved anchors of the net near ``point``."""
    index = index or CopperAnchorIndex.build(session, resolver=resolver)
    x_mm, y_mm = float(point[0]), float(point[1])
    anchors = index.anchors_with_net(int(net_code))
    if not anchors:
        return []
    ranked = sorted(
        (
            (math.hypot(key[0] - x_mm, key[1] - y_mm), key)
            for key in anchors
            if key != _round_anchor(point)
        ),
        key=lambda item: (item[0], item[1]),
    )
    found: list[AnchorResolution] = []
    components: list[frozenset] = []
    probed = 0
    for distance, key in ranked:
        if len(found) >= limit or probed >= max_probes:
            break
        if radius_mm is not None and distance > float(radius_mm):
            break
        probed += 1
        resolution = verified_anchor(
            session, key[0], key[1], int(key[2]), int(net_code), index=index,
            resolver=resolver,
        )
        if resolution is None:
            continue
        try:
            component = session.cluster(*resolution.point)
        except Exception:               # noqa: BLE001 - unreadable is not proof
            continue
        if not component or any(component & previous for previous in components):
            continue
        found.append(replace(
            resolution, provenance="candidate",
            cluster_items=len(component),
            moved_mm=math.hypot(resolution.point[0] - x_mm,
                                resolution.point[1] - y_mm),
        ))
        components.append(component)
    return found


@dataclass(frozen=True)
class ComponentPairScan:
    """Component-pair offers plus what the bounded windows left out.

    ``by_net`` is what a caller may work on. ``coverage`` says how much of the
    net's component graph was actually examined this pass, so "no offers" can
    never be read as "no disconnected components": a window is a window.
    """

    by_net: dict[int, list[NetPair]] = field(default_factory=dict)
    coverage: dict[str, Any] = field(default_factory=dict)

    @property
    def pairs(self) -> list[NetPair]:
        return [pair for pairs in self.by_net.values() for pair in pairs]

    def items(self):
        return self.by_net.items()


def component_pairs(
    session: AgentSession, *,
    resolver: LayerResolver | None = None,
    index: CopperAnchorIndex | None = None,
    max_components_per_net: int = 12,
    max_pairs_per_net: int = 4,
    max_anchor_probes: int = 4,
    min_gap_mm: float = 0.0,
    window: int = 0,
    max_gap_mm: float = 0.0,
) -> ComponentPairScan:
    """Distinct disconnected copper-component pairs, per net, from native membership.

    The ratsnest is a *drawing*: when its endpoint lands on no copper, the edge
    cannot be routed from, and retiring the pair throws away a connection the
    board may still admit. The engine's terminal partition is not a drawing - it
    says which terminals are electrically joined, so the same disconnected state
    can be expressed as "component A is not component B".

    This enumerates the nearest distinct component pairs of every net, proves an
    anchor on each side with the cluster rule, and returns them as offers with
    ``source="component"``. Bounds: at most ``max_components_per_net`` components
    and ``max_pairs_per_net`` pairs per net (nearest-first), and at most
    ``max_anchor_probes`` anchor combinations tried per pair. A pair whose
    anchors already share a cluster is skipped - it is already connected, so
    there is nothing to route.

    The windows **rotate** with ``window``: components are ordered by proximity
    and the window starts at ``window``, wrapping, and each component's anchors
    rotate with the same offset. On an unchanged board, successive scans therefore
    examine different components and different anchors rather than the same first
    few forever. ``coverage`` reports, per net, how many components and pairs were
    examined and how many the window and the pair cap left out, so a caller can
    say "capped" instead of claiming the net is exhausted.

    Each component's nearest neighbours are taken over **all** of that net's
    components, not over the window, so rotating the window can never make a
    distant component look nearest. ``max_gap_mm`` is an optional ceiling on how
    far apart an offered pair's anchors may be (0 disables it, which is the
    default): a long pair is sometimes the board's real outstanding connection -
    one net on the frozen board genuinely spans 86 mm - so dropping those by
    default would suppress work the ratsnest asks for. Pairs a caller does drop
    this way are counted in ``pairs_omitted_by_gap``.

    Nets with fewer than two proved components contribute nothing, and a net
    whose membership cannot be read contributes nothing at all: an unreadable
    partition is not an empty one.
    """
    components = native_components(
        session, resolver=resolver, anchor_window=window,
    )
    if not components:
        return ComponentPairScan()
    layer_count = 1
    try:
        layer_count = int(session._engine.get_copper_layer_count())
    except Exception:                   # noqa: BLE001 - absent accessor
        layer_count = 1

    out: dict[int, list[NetPair]] = {}
    per_net: dict[str, Any] = {}
    for net, items in components.items():
        if len(items) < 2:
            continue
        ordered = sorted(
            items, key=lambda component: (component.bbox, component.component_id),
        )
        ranked_components = _rotated_window(
            ordered, window, max_components_per_net,
        )
        considered_ids = {component.component_id for component in ranked_components}
        components_examined = 0
        components_failed = 0
        omitted_by_gap = 0
        candidates: list[NetPair] = []
        for left_index, left in enumerate(ranked_components):
            # Nearest neighbours are taken over the *whole* net, not over the
            # window: a rotating window that happens to hold only distant
            # components must not make one of them look like this component's
            # nearest neighbour.
            others = sorted(
                (right for right in ordered
                 if right.component_id != left.component_id),
                key=lambda right: (_bbox_gap(left.bbox, right.bbox),
                                   right.component_id),
            )[:3]
            for right in others:
                components_examined += 1
                if (_bbox_gap(left.bbox, right.bbox) > float(max_gap_mm)
                        and max_gap_mm > 0):
                    omitted_by_gap += 1
                    continue
                pair = _component_pair(
                    session, net, left, right, layer_count=layer_count,
                    max_anchor_probes=max_anchor_probes,
                )
                if pair is None:
                    components_failed += 1
                    continue
                if max_gap_mm > 0 and pair.gap_mm > float(max_gap_mm):
                    omitted_by_gap += 1
                    continue
                if pair.gap_mm < min_gap_mm:
                    components_failed += 1
                    continue
                candidates.append(pair)
        candidates.sort(key=_pair_rank_key)
        if candidates:
            out[int(net)] = candidates[:max_pairs_per_net]
        per_net[str(int(net))] = {
            "components_total": len(items),
            "components_in_window": len(ranked_components),
            "components_omitted_by_window": len(items) - len(ranked_components),
            "pairs_examined": components_examined,
            "pairs_unprovable": components_failed,
            "pairs_offered": len(out.get(int(net), [])),
            "pairs_omitted_by_cap": max(0, len(candidates) - max_pairs_per_net),
            "pairs_omitted_by_gap": omitted_by_gap,
            "component_ids_in_window": sorted(considered_ids),
        }
    coverage = {
        "window": int(window),
        "nets_total": len(components),
        "nets_with_two_or_more_components": len(per_net),
        "nets_with_pairs": len(out),
        "components_total": sum(
            len(items) for items in components.values()
        ),
        "components_in_multi_component_nets": sum(
            entry["components_total"] for entry in per_net.values()
        ),
        "components_in_window": sum(
            entry["components_in_window"] for entry in per_net.values()
        ),
        "components_omitted_by_window": sum(
            entry["components_omitted_by_window"] for entry in per_net.values()
        ),
        "pairs_considered": sum(entry["pairs_examined"] for entry in per_net.values()),
        "pairs_offered": sum(len(pairs) for pairs in out.values()),
        "pairs_omitted_by_cap": sum(
            entry["pairs_omitted_by_cap"] for entry in per_net.values()
        ),
        "pairs_omitted_by_gap": sum(
            entry["pairs_omitted_by_gap"] for entry in per_net.values()
        ),
        "per_net": per_net,
    }
    return ComponentPairScan(by_net=out, coverage=coverage)


def _bbox_gap(left: tuple[float, float, float, float],
              right: tuple[float, float, float, float]) -> float:
    """Distance between two axis-aligned boxes (0 when they overlap)."""
    dx = max(0.0, max(left[0], right[0]) - min(left[2], right[2]))
    dy = max(0.0, max(left[1], right[1]) - min(left[3], right[3]))
    return math.hypot(dx, dy)


def _component_pair(
    session: AgentSession, net: int, left: CopperComponent,
    right: CopperComponent, *, layer_count: int, max_anchor_probes: int,
) -> NetPair | None:
    """One proved anchor pair joining two distinct components, or ``None``."""
    combos = sorted(
        (
            (math.hypot(a[0] - b[0], a[1] - b[1]), a, b)
            for a in left.anchors for b in right.anchors
        ),
        key=lambda item: (item[0], item[1], item[2]),
    )[:max(1, int(max_anchor_probes))]
    for distance, start, target in combos:
        if distance <= COINCIDENT_EPS_MM and int(start[2]) == int(target[2]):
            continue
        try:
            start_net = session.net_at(start[0], start[1], int(start[2]))
            target_net = session.net_at(target[0], target[1], int(target[2]))
        except Exception:               # noqa: BLE001 - unreadable is not proof
            return None
        if start_net != int(net) or target_net != int(net):
            continue
        try:
            start_cluster = session.cluster(start[0], start[1], int(start[2]))
            target_cluster = session.cluster(target[0], target[1], int(target[2]))
        except Exception:               # noqa: BLE001
            return None
        if not start_cluster or not target_cluster:
            continue
        if start_cluster & target_cluster:
            return None                 # already joined: nothing to route
        kind = ("line" if distance > COINCIDENT_EPS_MM
                else "coincident_cross_layer")
        return NetPair(
            net_code=int(net),
            net_name=str(left.net_name),
            start=(float(start[0]), float(start[1]), int(start[2])),
            target=(float(target[0]), float(target[1]), int(target[2])),
            gap_mm=float(distance),
            start_layers=tuple(human_layers_at(session, start[0], start[1])),
            target_layers=tuple(human_layers_at(session, target[0], target[1])),
            pad_groups=0,
            pair_kind=kind,
            source="component",
            component_start=left.component_id,
            component_target=right.component_id,
        )
    return None


def reanchor_pair(
    session: AgentSession, pair: NetPair, *,
    index: CopperAnchorIndex | None = None,
    resolver: LayerResolver | None = None,
    allow_component_candidates: bool = True,
    component_radius_mm: float = 4.0,
) -> tuple[NetPair, dict[str, Any]] | None:
    """One pair whose endpoints both resolve to the scheduled net, or ``None``.

    Convenience wrapper over :func:`reanchor_pair_variants` returning the first
    (closest) substitution, for callers that can act on a single pair.

    An endpoint that already resolves is kept as-is (the ratsnest anchor stays
    authoritative while it is provable). An endpoint the point-identity rule
    cannot name is **substituted**, never "resolved": the replacement is copper
    of the scheduled net that the engine proves, which can be a different
    physical component from whatever the ratsnest edge meant. Every substitution
    is labelled (``provenance="candidate"``, ``proof``, ``component_id``,
    ``moved_mm``) so no report can present it as the anchor the ratsnest offered.
    """

    variants = reanchor_pair_variants(
        session, pair, index=index, resolver=resolver,
        allow_component_candidates=allow_component_candidates,
        component_radius_mm=component_radius_mm, limit=1,
    )
    return variants[0] if variants else None


def reanchor_pair_variants(
    session: AgentSession, pair: NetPair, *,
    index: CopperAnchorIndex | None = None,
    resolver: LayerResolver | None = None,
    allow_component_candidates: bool = True,
    component_radius_mm: float | None = 4.0,
    limit: int = 3,
) -> list[tuple[NetPair, dict[str, Any]]]:
    """Every endpoint substitution this pair admits, closest first, or ``[]``.

    For each endpoint the point-identity rule cannot name, the search widens in
    three steps and each step says what it actually proves:

    * a pad or unanimous copper in the anchor's **own cluster**
      (``provenance="original"``: the anchor is the ratsnest's, only its net
      needed naming);
    * a sample inside the covering same-net zone
      (``provenance="candidate"``, ``proof="pour"``); the sample proves that
      *the sample* sits on that net's copper inside a covering zone, which is not
      the same claim as "the pour is the copper the ratsnest meant", and it never
      proves the pour's island membership;
    * proved anchors of the net's **native components**, one per component and
      unbounded in radius when ``component_radius_mm is None``
      (``provenance="candidate"``, ``proof="pad"|"copper"``, ``component_id``,
      ``moved_mm``). A component already connected to the *other* endpoint is not
      offered: substituting into it would close nothing.

    Returns at most ``limit`` distinct substituted pairs. Each carries its own
    evidence with ``offer_relation`` - ``"original"`` when both anchors are the
    ratsnest's, ``"substitution"`` when at least one endpoint was moved - so the
    caller can record candidate substitution without ever claiming the original
    edge was resolved.
    """
    index = index or CopperAnchorIndex.build(session, resolver=resolver)
    evidence: dict[str, Any] = {"net_code": int(pair.net_code), "endpoints": {}}
    resolved: dict[str, tuple[float, float, int]] = {}
    component_of: dict[str, str] = {}
    for name, point in (("start", pair.start), ("target", pair.target)):
        if session.net_at(*point) == int(pair.net_code):
            resolved[name] = point
            evidence["endpoints"][name] = {"kept": True, "point": list(point)}
            continue
        anchor = verified_anchor(session, point[0], point[1], point[2],
                                 int(pair.net_code), index=index,
                                 resolver=resolver)
        if anchor is None:
            # Second attempt: the point may sit in a same-net zone's void (a
            # pour-mediated connection whose anchor is not on copper). The zone
            # inventory names the covering zone; the sample must be proved by the
            # engine and must resolve to exactly one component.
            anchor = zone_anchor(session, point, int(pair.net_code),
                                 index=index, resolver=resolver)
        if anchor is None and allow_component_candidates:
            # Third attempt: the point may sit on no scheduled-net copper at all.
            # The net's own components are real copper of the same net; offering
            # the nearest proved one is an honest substitute endpoint, and
            # refusing the pair on the point alone would retire a connection the
            # engine may well be able to close.
            peer = resolved.get("target" if name == "start" else "start")
            candidates = component_anchor_candidates(
                session, point, int(pair.net_code), index=index,
                resolver=resolver, radius_mm=component_radius_mm,
                peer=peer,
            )
            offerable = [item for item in candidates if not item.already_connected]
            if candidates:
                evidence.setdefault("component_candidates", {})[name] = [
                    candidate.to_evidence() for candidate in candidates
                ]
            if offerable:
                anchor = offerable[0]
        if anchor is None:
            evidence["endpoints"][name] = {
                "kept": False, "point": list(point),
                "reason": "no verified same-net anchor in this cluster",
            }
            return []
        resolved[name] = anchor.point
        if anchor.component_id:
            component_of[name] = anchor.component_id
        evidence["endpoints"][name] = {
            "kept": False, "replaced": anchor.to_evidence(),
        }
    substituted = replace(pair, start=resolved["start"], target=resolved["target"])
    moved_mm = math.hypot(resolved["start"][0] - pair.start[0],
                          resolved["start"][1] - pair.start[1]) + math.hypot(
        resolved["target"][0] - pair.target[0],
        resolved["target"][1] - pair.target[1],
    )
    evidence["offer_relation"] = classify_offer(pair, substituted)
    evidence["moved_mm"] = round(moved_mm, 4)
    evidence["components"] = {
        name: component_of.get(name, "") for name in ("start", "target")
    }
    return [(substituted, evidence)][:max(1, int(limit))]


#: The ratsnest edge's own anchors are preserved exactly.
OFFER_ORIGINAL = "original"
#: At least one endpoint was moved to other proved copper of the same net.
OFFER_SUBSTITUTION = "substitution"


def classify_offer(offered: NetPair, used: NetPair) -> str:
    """``"original"`` when the anchors did not move, ``"substitution"`` when they did.

    A substituted offer is a *candidate* the scheduler is entitled to try; it is
    never evidence that the offered edge was resolved, and this is the one place
    that distinction is computed so no caller has to re-derive it.
    """
    return (OFFER_ORIGINAL if (offered.start == used.start
                               and offered.target == used.target)
            else OFFER_SUBSTITUTION)


def component_waypoints(
    session: AgentSession, pair: NetPair, *,
    limit: int = 3, samples: int = 12,
    index: CopperAnchorIndex | None = None,
    resolver: LayerResolver | None = None,
) -> list[dict[str, Any]]:
    """Verified points inside the *target's own* connected copper component.

    A pour-aware escape: the router is given a target that lies on copper already
    connected to the far terminal, so closing pad -> waypoint closes the
    connection. Every candidate is proved by the engine's connectivity, not by
    geometry: the sample's cluster must **intersect the target's cluster**. That
    proof also enforces layer, holes and keepouts, because a point with no copper
    (or copper of another net) simply has no shared cluster.

    The candidate set is the component's own anchors (nearest to the start first)
    plus a bounded grid sampled inside their bounding box on the pair's layer.
    """
    engine = session._engine
    layer = int(pair.start[2])
    target_anchors = session.cluster(*pair.target)
    if not target_anchors:
        return []

    def accepted(x_mm: float, y_mm: float, sample_layer: int) -> frozenset:
        try:
            return session.cluster(float(x_mm), float(y_mm), int(sample_layer))
        except Exception:               # noqa: BLE001 - no copper at the sample
            return frozenset()

    def distance(point: tuple[float, float, int]) -> float:
        return math.hypot(point[0] - pair.start[0], point[1] - pair.start[1])

    found: list[dict[str, Any]] = []
    seen: set[tuple[float, float, int]] = set()
    anchors_on_layer = sorted(
        (key for key in target_anchors
         if int(key[2]) == layer and key != pair.target and key != pair.start),
        key=lambda key: (distance(key), key),
    )
    for key in anchors_on_layer:
        if len(found) >= limit:
            return found
        if key in seen:
            continue
        cluster = accepted(*key)
        if not (cluster & target_anchors):
            continue
        seen.add(key)
        found.append({
            "x_mm": key[0], "y_mm": key[1], "layer": layer,
            "source": "component_anchor",
            "shared_anchors": len(cluster & target_anchors),
        })

    if samples > 0 and len(found) < limit:
        xs = [key[0] for key in target_anchors if int(key[2]) == layer]
        ys = [key[1] for key in target_anchors if int(key[2]) == layer]
        if xs and ys:
            side = max(1, int(math.sqrt(samples)))
            for row in range(side):
                if len(found) >= limit:
                    break
                for column in range(side):
                    if len(found) >= limit:
                        break
                    x = min(xs) + (max(xs) - min(xs)) * (column + 0.5) / side
                    y = min(ys) + (max(ys) - min(ys)) * (row + 0.5) / side
                    key = _round_anchor((x, y, layer))
                    if key in seen:
                        continue
                    cluster = accepted(*key)
                    if not (cluster & target_anchors):
                        continue
                    seen.add(key)
                    found.append({
                        "x_mm": key[0], "y_mm": key[1], "layer": layer,
                        "source": "component_interior",
                        "shared_anchors": len(cluster & target_anchors),
                    })
    return found


def anchor_diagnosis(
    session: AgentSession, point: tuple[float, float, int], net_code: int,
    *, index: CopperAnchorIndex | None = None,
    resolver: LayerResolver | None = None, radius_mm: float = 2.0,
) -> dict[str, Any]:
    """Why the scheduler cannot use an anchor, in the engine's own terms.

    A ratsnest anchor is only useful if the board still has copper of that net
    under it. This reports, exactly: which copper layers carry a cluster there
    (and of which nets), the nearest item of the *scheduled* net, and the nearest
    foreign-net item - so a refusal names geometry and UUIDs instead of "blocked".
    """
    engine = session._engine
    x_mm, y_mm, layer = float(point[0]), float(point[1]), int(point[2])
    layers: dict[str, Any] = {}
    for human in range(1, int(engine.get_copper_layer_count()) + 1):
        try:
            cluster = session.cluster(x_mm, y_mm, human)
        except Exception as exc:  # noqa: BLE001 - a refusal is information
            layers[str(human)] = {"error": type(exc).__name__}
            continue
        if not cluster:
            continue
        if index is not None:
            nets, netless = index.nets_in(cluster)
        else:
            nets, netless = set(), 0
        layers[str(human)] = {
            "anchors": len(cluster),
            "nets": sorted(nets),
            "items_without_net": int(netless),
            "scheduled_net": int(net_code) in nets,
        }

    same_net: tuple[float, str] | None = None
    foreign: tuple[float, str] | None = None

    def consider(distance: float, description: str, item_net: int) -> None:
        nonlocal same_net, foreign
        if distance > radius_mm:
            return
        if int(item_net) == int(net_code):
            if same_net is None or distance < same_net[0]:
                same_net = (distance, description)
        elif int(item_net) > 0:
            if foreign is None or distance < foreign[0]:
                foreign = (distance, description)

    for pad in _items(engine, "get_pads"):
        uuid = str(getattr(pad, "uuid", "") or "")
        consider(math.hypot(float(pad.x_mm) - x_mm, float(pad.y_mm) - y_mm),
                 f"pad {getattr(pad, 'footprint_ref', '')}."
                 f"{getattr(pad, 'pad_name', '')}"
                 + (f" ({uuid[:8]})" if uuid else ""),
                 int(pad.net_code))
    for track in _items(engine, "get_tracks"):
        distance = _point_segment_distance(
            x_mm, y_mm, float(track.x1_mm), float(track.y1_mm),
            float(track.x2_mm), float(track.y2_mm),
        )
        uuid = str(getattr(track, "uuid", "") or "")
        consider(distance, f"track {uuid[:8]}".strip(), int(track.net_code))
    for via in _items(engine, "get_vias"):
        uuid = str(getattr(via, "uuid", "") or "")
        consider(math.hypot(float(via.x_mm) - x_mm, float(via.y_mm) - y_mm),
                 f"via {uuid[:8]}".strip(), int(via.net_code))

    if not layers:
        verdict = (
            f"anchor ({x_mm:.4f}, {y_mm:.4f}, layer {layer}) carries no copper "
            "on any copper layer of this board"
        )
        if foreign is not None:
            verdict += (
                f"; nearest foreign copper is {foreign[1]} at "
                f"{foreign[0]:.3f} mm"
            )
        if same_net is not None:
            verdict += (
                f"; nearest {net_code} copper is {same_net[1]} at "
                f"{same_net[0]:.3f} mm"
            )
        else:
            verdict += f"; no {net_code} copper within {radius_mm:.1f} mm"
    else:
        verdict = (
            f"anchor ({x_mm:.4f}, {y_mm:.4f}, layer {layer}) has copper but no "
            "verified same-net anchor"
        )
    return {
        "point": [x_mm, y_mm, layer],
        "layers": layers,
        "nearest_same_net": (
            {"distance_mm": round(same_net[0], 4), "item": same_net[1]}
            if same_net else None),
        "nearest_foreign": (
            {"distance_mm": round(foreign[0], 4), "item": foreign[1]}
            if foreign else None),
        "verdict": verdict,
    }


def progress_summary(session: AgentSession) -> dict[str, Any]:
    """Cheap board-level progress numbers (call before/after an attempt)."""
    engine = session._engine
    groups = engine.get_pad_groups()
    return {
        "unrouted_edges": int(engine.get_unrouted_count()),
        "pad_group_total": int(sum(int(v) for v in groups.values())),
        "track_count": int(engine.get_track_count()),
        "via_count": int(engine.get_via_count()),
    }


def compact_observation(
    session: AgentSession,
    pair: NetPair,
    *,
    resolver: LayerResolver | None = None,
    attempts: Iterable[Mapping[str, Any]] = (),
    last_drc_delta: Mapping[str, Any] | None = None,
    before: Mapping[str, Any] | None = None,
    radius_mm: float = 3.0,
    obstacle_limit: int = 8,
    budget_chars: int = 24_000,
) -> dict[str, Any]:
    """One connection's decision context, small enough for a single request.

    ``budget_chars`` is a hard ceiling (~4 characters per token, so 24k chars is
    roughly 6k tokens). Exceeding it drops obstacles rather than truncating JSON,
    so the planner never receives malformed input.
    """
    engine = session._engine
    resolver = resolver or LayerResolver.build(session)
    bbox = engine.get_board_bbox()
    netclass = engine.get_netclass_for_net(pair.net_code)
    edge_margin = None
    try:
        edge_margin = float(engine.get_design_rules().copper_edge_clearance_mm)
    except Exception:  # noqa: BLE001 - not fatal for planning
        edge_margin = None

    observation: dict[str, Any] = {
        "board": {
            "bbox_mm": [
                round(float(bbox.x_mm), 2), round(float(bbox.y_mm), 2),
                round(float(bbox.x_mm) + float(bbox.width_mm), 2),
                round(float(bbox.y_mm) + float(bbox.height_mm), 2),
            ],
            "copper_layers": int(engine.get_copper_layer_count()),
            "layer_map": resolver.to_evidence()["mirror_to_human"],
            "copper_edge_clearance_mm": edge_margin,
        },
        "connection": {
            **pair.to_dict(),
            "start_cluster_size": len(session.cluster(*pair.start)),
            "target_cluster_size": len(session.cluster(*pair.target)),
        },
        "legal": {
            "modes": list(MODE_NAMES),
            "target_layers": list(pair.target_layers) or [pair.target[2]],
            "start_layer": pair.start[2],
            "netclass": {
                "clearance_mm": getattr(netclass, "clearance_mm", None),
                "track_width_mm": getattr(netclass, "track_width_mm", None),
                "via_diameter_mm": getattr(netclass, "via_diameter_mm", None),
                "via_drill_mm": getattr(netclass, "via_drill_mm", None),
            },
        },
        "obstacles": nearest_obstacles(
            session, pair.target[0], pair.target[1], pair.net_code,
            resolver=resolver, radius_mm=radius_mm, limit=obstacle_limit,
        ),
        "progress": {
            "before": dict(before or progress_summary(session)),
            "last_drc_delta": dict(last_drc_delta or {}),
        },
        "attempts": [dict(item) for item in attempts],
    }

    # Drop obstacles (least important first) until the budget is met.
    while (
        len(json.dumps(observation, separators=(",", ":"))) > budget_chars
        and observation["obstacles"]
    ):
        observation["obstacles"].pop()
        observation.setdefault("notes", []).append(
            "obstacle list truncated to respect the request budget"
        )
    if len(json.dumps(observation, separators=(",", ":"))) > budget_chars:
        raise ValueError(
            f"observation exceeds the {budget_chars}-character budget even without "
            "obstacles; the pair itself is too large for one request"
        )
    return observation


def estimate_tokens(value: Any) -> int:
    """Rough token estimate (~4 characters per token) for budgeting."""
    text = value if isinstance(value, str) else json.dumps(value, separators=(",", ":"))
    return max(1, len(text) // 4)


def render_observation(observation: Mapping[str, Any]) -> str:
    """Stable JSON text for a prompt (sorted keys, compact separators)."""
    return json.dumps(observation, sort_keys=True, separators=(",", ":"))
