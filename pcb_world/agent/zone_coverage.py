"""Advisory zone-copper prefilter: does a plan's own geometry sit in a pour?

The refusal-driven search spends its budget on plans whose copper the native
gate then refuses, and the largest well-evidenced class of those refusals is a
plan's own via landing against a **foreign copper pour**. The router cannot see
pours: KiCad's sync gate admits rule-area keepouts into the PNS world and
nothing else, so the obstacle model a plan is built against carries no pour
copper at all.

This module answers the missing question with the engine's read-only
``get_zone_point_hits()`` query and turns it into a *ranking* signal:

* :class:`ZoneCoverage` batches points into one engine call per batch and caches
  the answers for as long as the zone fill cannot have changed (see
  :meth:`ZoneCoverage.invalidate` — a router's own copper edits never change zone
  fill; only a refill does);
* every answer is one of ``none``, ``own``, ``foreign``, ``mixed`` or
  ``unknown``, and ``unknown`` always **passes through** — it never suppresses,
  never de-prioritises and is never read as safe;
* the geometric test uses the engine's own distance to filled copper against a
  caller-supplied margin (copper radius + clearance), so a via's circumference
  and a track's half-width are accounted for rather than only the point.

What this is not: it is not a collision test against the whole board, it says
nothing about tracks, pads, holes or other nets' copper, and it never replaces
the native DRC. A point answer is not a path answer — a candidate is a plan, so
only the plan's own named geometry is sampled, and a plan can still land copper
somewhere no sample looked. The transactional DRC remains the only authority on
whether copper may be kept.

Ranking is also not a promise that a plan is still attempted: this module only
reorders, and the caller's own ``candidate_limit`` truncation happens after the
reorder, so a plan moved to the back can fall outside that window.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping, Sequence

#: Verdict states. ``unknown`` is not a synonym for ``none``: it means the
#: question could not be answered for this point, and callers must treat it as
#: unanswered.
NONE = "none"
OWN = "own"
FOREIGN = "foreign"
MIXED = "mixed"
UNKNOWN = "unknown"

#: Zone-fill provenance values the engine can report (see wire.ZonePointHit).
#: ``loaded_unverified`` is the only "there is fill geometry here" value, and it
#: deliberately does not claim the fill matches the current design rules.
LOADED_UNVERIFIED = "loaded_unverified"
NO_FILL = "no_fill"


@dataclass(frozen=True)
class ZonePointVerdict:
    """One point's pour answer, with the evidence behind it.

    ``nets`` holds the nets of the pours whose filled copper is within the
    requested margin of the point (a pour *touching* the point's copper). It is
    empty for ``none`` and for ``unknown``. ``own_island_contact`` is True when
    one of those pours is the candidate's own net *and* the engine reports the
    covering fill polygon as an insulated island — recorded because island
    copper is not connected copper, but not folded into the net-identity state.
    """

    point: tuple[float, float, int]
    state: str
    nets: tuple[int, ...] = ()
    margin_mm: float = 0.0
    provenance: str = NO_FILL
    own_island_contact: bool = False
    in_keepout: bool = False
    #: Distance to the nearest *foreign* pour's copper that the query reported,
    #: or None when the engine reported no foreign pour inside the query's own
    #: window. It is a measured floor, never a proof: "None" means "nothing
    #: closer than the window", which is only useful to a caller that knows what
    #: window it asked for.
    foreign_distance_mm: float | None = None
    reason: str = ""
    available: bool = True
    from_cache: bool = False

    @property
    def is_unknown(self) -> bool:
        return self.state == UNKNOWN

    def to_evidence(self) -> dict[str, Any]:
        return {
            "x_mm": round(float(self.point[0]), 4),
            "y_mm": round(float(self.point[1]), 4),
            "layer": int(self.point[2]),
            "state": self.state,
            "nets": list(self.nets),
            "margin_mm": round(float(self.margin_mm), 4),
            "fill_provenance": self.provenance,
            "own_island_contact": bool(self.own_island_contact),
            "in_keepout": bool(self.in_keepout),
            "foreign_distance_mm": (
                None if self.foreign_distance_mm is None
                else round(float(self.foreign_distance_mm), 4)),
            "available": bool(self.available),
            "reason": self.reason,
        }


@dataclass(frozen=True)
class CandidateZoneRisk:
    """A candidate plan's pour verdict, aggregated over the points it names.

    ``state`` is the *strongest* signal any sampled point produced, ordered
    ``unknown`` < ``none`` < ``own`` < ``mixed`` < ``foreign`` for ranking
    (``unknown`` is reported first only because it is never acted on).
    ``suppressed`` is always False and ``points`` names only the risky samples
    this aggregation actually took — *not* a claim that the plan still gets
    tried. This class ranks; it never removes a candidate from the list it was
    given. The runner truncates that list to its own ``candidate_limit`` after
    ranking, so a plan ranked late here can miss the attempt.
    """

    state: str
    points_checked: int = 0
    points: tuple[tuple[float, float, int], ...] = ()
    nets: tuple[int, ...] = ()
    margin_mm: float = 0.0
    available: bool = True
    reason: str = ""
    suppressed: bool = False

    @property
    def is_foreign(self) -> bool:
        return self.state in (FOREIGN, MIXED)

    def to_evidence(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "points_checked": int(self.points_checked),
            "points": [[round(float(p[0]), 4), round(float(p[1]), 4), int(p[2])]
                       for p in self.points],
            "nets": list(self.nets),
            "margin_mm": round(float(self.margin_mm), 4),
            "available": bool(self.available),
            "suppressed": False,
            "reason": self.reason,
        }


def board_margin_mm(engine) -> tuple[float, str]:
    """The board's own copper radius + clearance, and what it was derived from.

    A plan's copper has width: a track is ``track_width`` wide and a via is
    ``via_diameter`` across, and the DRC needs the rule clearance besides. The
    margin this returns is therefore the distance at which *that* copper would
    touch a pour, so the query answers the geometric question the plan actually
    poses rather than only "is the point on copper".

    Unreadable or unset rule fields (KiCad writes a negative sentinel for
    "unset") are not guessed at: the missing term contributes 0.0 and the
    returned ``basis`` says so, which makes the answer strictly weaker and
    never stronger. Callers that need the stronger form must supply their own
    margin.
    """
    clearance = 0.0
    copper_radius = 0.0
    notes: list[str] = []

    try:
        rules = engine.get_design_rules()
    except Exception as exc:  # noqa: BLE001 - unreadable rules are "unknown"
        return 0.0, f"rules unreadable ({type(exc).__name__})"

    def _positive(value: Any) -> float | None:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        number = float(value)
        return number if math.isfinite(number) and number >= 0.0 else None

    raw_clearance = _positive(getattr(rules, "min_clearance_mm", None))
    if raw_clearance is None:
        notes.append("clearance unset")
    else:
        clearance = raw_clearance

    netclass = getattr(rules, "default_netclass", None)
    halves = []
    for field_name in ("track_width_mm", "via_diameter_mm"):
        raw = _positive(getattr(netclass, field_name, None) if netclass else None)
        if raw is None:
            continue
        halves.append(raw / 2.0)
    if halves:
        copper_radius = max(halves)
    else:
        notes.append("copper width unset")

    margin = clearance + copper_radius
    basis = "clearance %s + copper radius %s" % (
        f"{clearance:.4f}" if raw_clearance is not None else "0",
        f"{copper_radius:.4f}" if halves else "0",
    )
    if notes:
        basis += " (" + ", ".join(notes) + ")"
    return margin, basis


def sample_leg(
    start: Sequence[float], target: Sequence[float], *,
    pitch_mm: float, max_samples: int,
) -> list[tuple[float, float, int]]:
    """Deterministic samples along the straight leg ``start`` → ``target``.

    A candidate with no waypoints — a direct or shove plan — still *names* the
    connection it will route, so its corridor is a straight line between the two
    endpoints and that line is the only geometry the plan declares. Sampling it
    is what lets the prefilter see a pour the plan has to cross; without it a
    plan with no waypoints would be permanently unclassifiable.

    The samples are a *sample*: the router may walk around, shove copper and drop
    its via somewhere between them, so a clean result here is not proof that the
    plan keeps off a pour. That asymmetry is deliberate — the prefilter only ever
    uses a *positive* contact to rank a plan later, and the native DRC remains
    the authority.

    Both endpoint layers are sampled when they differ: a leg that changes layer
    will place a via somewhere along it, and the pour contact that matters is on
    either copper face.
    """
    x0, y0, layer0 = float(start[0]), float(start[1]), int(start[2])
    x1, y1, layer1 = float(target[0]), float(target[1]), int(target[2])
    layers = (layer0,) if layer0 == layer1 else (layer0, layer1)
    if int(max_samples) < 2:
        return [(x0, y0, layer) for layer in layers] + [(x1, y1, layer) for layer in layers]

    length = math.hypot(x1 - x0, y1 - y0)
    steps = max(1, min(int(max_samples), int(math.ceil(length / max(1e-6, float(pitch_mm))))))
    points: list[tuple[float, float, int]] = []
    for index in range(steps + 1):
        fraction = index / steps
        x = x0 + (x1 - x0) * fraction
        y = y0 + (y1 - y0) * fraction
        for layer in layers:
            points.append((round(x, 4), round(y, 4), layer))
    return points


@dataclass(frozen=True)
class _Unresolved:
    """A point the engine cannot answer, cached so it is not asked twice."""

    reason: str
    available: bool = True


class ZoneCoverage:
    """Cached, batched wrapper over the engine's read-only zone point query.

    The cache lifetime is the point of the class. Zone *fill* cannot change
    while a router edits copper — refills are a separate, whole-board operation
    (``KiCadEngine.fill_zones``) that bumps the engine's fill epoch — so an
    answer stays valid for the whole run and must not be re-fetched per
    candidate. The brief for this prefilter requires exactly that: no
    whole-board scan (or repeated query) per candidate.

    ``invalidate()`` drops the cache; it is called when the engine is replaced
    or when a refill bumps the epoch, and tests pin both paths.
    """

    def __init__(self, session, *, enabled: bool = True) -> None:
        self._session = session
        self.enabled = bool(enabled)
        #: (x, y, human layer, margin) -> the engine's answer for that point, or
        #: an :class:`_Unresolved` marker. The engine's answer is **net
        #: independent** — the query does not take a net — so it is what gets
        #: cached; the net-dependent verdict is derived from it on every call.
        #: Caching the verdict instead would hand one pair's own/foreign
        #: classification to the next pair that asked about the same copper.
        self._row_cache: dict[tuple, object] = {}
        self._generation: tuple | None = None
        self.queries = 0
        self.cache_hits = 0
        self.batches = 0
        self.unsupported_reason = ""

    # -- engine access -----------------------------------------------------

    @property
    def engine(self):
        return self._session._engine

    def _current_generation(self) -> tuple:
        """Identity of the zone geometry the cache describes.

        The engine's identity covers a reload (a new engine is a new board),
        and the fill epoch covers an in-place refill. Nothing else can change a
        zone fill polygon.
        """
        engine = self.engine
        epoch = getattr(engine, "zone_fill_epoch", 0)
        return (id(engine), int(epoch))

    def available(self) -> bool:
        """Whether this engine can answer the query at all."""
        if not self.enabled:
            self.unsupported_reason = self.unsupported_reason or "prefilter disabled"
            return False
        return callable(getattr(self.engine, "get_zone_point_hits", None))

    def invalidate(self) -> None:
        self._row_cache.clear()
        self._generation = None

    # -- queries -----------------------------------------------------------

    @staticmethod
    def _cache_key(point: Sequence[float], margin_mm: float) -> tuple:
        return (round(float(point[0]), 4), round(float(point[1]), 4),
                int(point[2]), round(float(margin_mm), 4))

    def _human_to_board(self, human_layer: int) -> int | None:
        layer_map = getattr(self.engine, "layer_map", None)
        if layer_map is None:
            return None
        try:
            return int(layer_map.human_to_board(int(human_layer)))
        except Exception:  # noqa: BLE001 - an unmappable layer is unknown, not a guess
            return None

    def verdicts(
        self, points: Iterable[Sequence[float]], net_code: int, *, margin_mm: float = 0.0,
    ) -> list[ZonePointVerdict]:
        """Classify ``points`` (``(x_mm, y_mm, human_layer)``) in one batch.

        Cached *engine rows* are reused across calls and across nets; only the
        points whose row is not cached cost an engine call. The verdict itself is
        always computed here, from this call's ``net_code``. The returned list
        matches ``points`` in order.
        """
        margin = float(margin_mm)
        wanted = [tuple(point) for point in points]

        if not self.available():
            reason = self.unsupported_reason or "engine exposes no zone point query"
            return [
                ZonePointVerdict(point=(float(p[0]), float(p[1]), int(p[2])),
                                 state=UNKNOWN, margin_mm=margin,
                                 reason=reason, available=False)
                for p in wanted
            ]

        generation = self._current_generation()
        if generation != self._generation:
            self._row_cache.clear()
            self._generation = generation

        out: list[ZonePointVerdict | None] = [None] * len(wanted)
        pending: list[tuple[int, tuple]] = []
        for index, point in enumerate(wanted):
            key = self._cache_key(point, margin)
            cached = self._row_cache.get(key)
            if cached is None:
                pending.append((index, point))
            elif isinstance(cached, _Unresolved):
                self.cache_hits += 1
                out[index] = ZonePointVerdict(
                    point=(float(point[0]), float(point[1]), int(point[2])),
                    state=UNKNOWN, margin_mm=margin, available=cached.available,
                    reason=cached.reason,
                )
            else:
                self.cache_hits += 1
                out[index] = self._classify(point, net_code, margin, cached)

        if pending:
            self.queries += len(pending)
            self.batches += 1
            queries = []
            unresolved: list[int] = []
            for index, point in pending:
                board_layer = self._human_to_board(point[2])
                if board_layer is None:
                    marker = _Unresolved("layer does not map to a board copper layer")
                    self._row_cache[self._cache_key(point, margin)] = marker
                    out[index] = ZonePointVerdict(
                        point=(float(point[0]), float(point[1]), int(point[2])),
                        state=UNKNOWN, margin_mm=margin, reason=marker.reason)
                    continue
                queries.append((float(point[0]), float(point[1]), board_layer))
                unresolved.append(index)

            if queries:
                # The window *is* the caller's margin: every zone whose copper is
                # close enough to fail `distance < margin` is reported, and the
                # response stays bounded by the geometry the caller asked about.
                rows = self.engine.get_zone_point_hits(tuple(queries), window_mm=margin)
                if rows is None:
                    for index in unresolved:
                        point = wanted[index]
                        marker = _Unresolved(
                            "engine build has no zone point query", available=False)
                        self._row_cache[self._cache_key(point, margin)] = marker
                        out[index] = ZonePointVerdict(
                            point=(float(point[0]), float(point[1]), int(point[2])),
                            state=UNKNOWN, margin_mm=margin, available=False,
                            reason=marker.reason)
                else:
                    # A short or over-long answer must not silently drop a point.
                    # ``zip`` would truncate to the shorter side, leaving an
                    # ``out`` entry as None - and the caller then received a
                    # *shorter* list than it asked about, so a point simply
                    # vanished from the risk aggregation. Anything that is not
                    # exactly one row per query is answered as unknown instead.
                    rows = list(rows)
                    if len(rows) != len(unresolved):
                        reason = (
                            f"engine returned {len(rows)} rows for {len(unresolved)} "
                            "queries; refusing to guess which point is missing"
                        )
                        for index in unresolved:
                            point = wanted[index]
                            marker = _Unresolved(reason, available=False)
                            self._row_cache[self._cache_key(point, margin)] = marker
                            out[index] = ZonePointVerdict(
                                point=(float(point[0]), float(point[1]), int(point[2])),
                                state=UNKNOWN, margin_mm=margin, available=False,
                                reason=reason)
                        rows = []
                    for index, row in zip(unresolved, rows):
                        if row is None:
                            point = wanted[index]
                            marker = _Unresolved("engine returned no row for this query")
                            self._row_cache[self._cache_key(point, margin)] = marker
                            out[index] = ZonePointVerdict(
                                point=(float(point[0]), float(point[1]), int(point[2])),
                                state=UNKNOWN, margin_mm=margin, reason=marker.reason)
                            continue
                        self._row_cache[self._cache_key(wanted[index], margin)] = row
                        verdict = self._classify(wanted[index], net_code, margin, row)
                        out[index] = verdict

        # Last line of defence: the answer is always exactly one verdict per
        # point, in order. A hole here would silently shorten the list and drop a
        # sample from the caller's aggregation, so anything that somehow has no
        # verdict is answered as unknown rather than omitted.
        for index, verdict in enumerate(out):
            if verdict is None:
                point = wanted[index]
                out[index] = ZonePointVerdict(
                    point=(float(point[0]), float(point[1]), int(point[2])),
                    state=UNKNOWN, margin_mm=margin, available=False,
                    reason="no verdict was produced for this point")
        return [verdict for verdict in out if verdict is not None]

    def verdict(
        self, point: Sequence[float], net_code: int, *, margin_mm: float = 0.0,
    ) -> ZonePointVerdict:
        """One point's verdict (cached; a second call costs no engine round trip)."""
        return self.verdicts([point], net_code, margin_mm=margin_mm)[0]

    def _classify(self, point, net_code: int, margin: float, row) -> ZonePointVerdict:
        """Turn one engine row into a net-identity verdict.

        The rules that make this conservative rather than optimistic:

        * an engine row that is not ``resolved`` is ``unknown``;
        * a zone that covers the point by outline but carries no fill for the
          layer cannot be measured, so it is ``unknown`` — "no stored fill" is
          not proof that the current rules would place no copper there;
        * a hit at ``distance_mm < margin`` is contact; contact with any net
          other than the candidate's — including a netless pour — is
          ``foreign``, and contact with own and foreign nets at once is
          ``mixed``. Both are reported, neither names a single net.
        """
        point_key = (float(point[0]), float(point[1]), int(point[2]))
        status = str(getattr(row, "status", ""))
        provenance = str(getattr(row, "fill_provenance", NO_FILL))

        if status != "resolved":
            return ZonePointVerdict(
                point=point_key, state=UNKNOWN, margin_mm=margin,
                provenance=provenance or UNKNOWN,
                reason=str(getattr(row, "reason", "")) or "engine did not resolve the point",
            )

        nets: set[int] = set()
        own_island = False
        nearest_foreign: float | None = None
        for hit in getattr(row, "hits", ()) or ():
            if bool(getattr(hit, "is_rule_area", False)):
                continue                       # keepouts are reported, not ranked
            distance = getattr(hit, "distance_mm", -1.0)
            try:
                distance = float(distance)
            except (TypeError, ValueError):
                distance = -1.0
            if distance < 0.0:
                if bool(getattr(hit, "in_outline", False)):
                    return ZonePointVerdict(
                        point=point_key, state=UNKNOWN, margin_mm=margin,
                        provenance=UNKNOWN, in_keepout=bool(getattr(row, "in_keepout", False)),
                        reason="a covering zone has no stored fill for this layer",
                    )
                continue
            hit_net = int(getattr(hit, "net_code", -1))
            # Every reported hit is inside the query's window, so this is the
            # nearest foreign copper among the geometry the caller asked about.
            if hit_net != int(net_code) and (
                    nearest_foreign is None or distance < nearest_foreign):
                nearest_foreign = distance
            if distance >= margin:
                continue
            nets.add(hit_net)
            if hit_net == int(net_code) and bool(getattr(hit, "fill_is_island", False)):
                own_island = True

        if not nets:
            state = NONE
        elif nets == {int(net_code)}:
            state = OWN
        elif int(net_code) in nets:
            state = MIXED
        else:
            state = FOREIGN

        return ZonePointVerdict(
            point=point_key, state=state, nets=tuple(sorted(nets)),
            margin_mm=margin, provenance=provenance, own_island_contact=own_island,
            in_keepout=bool(getattr(row, "in_keepout", False)),
            foreign_distance_mm=nearest_foreign,
        )

    # -- candidate aggregation --------------------------------------------

    @staticmethod
    def _strongest(states: Iterable[str]) -> str:
        """The ranking-relevant combination of several point verdicts.

        ``unknown`` is reported only when *every* point is unknown: one
        unanswered point among answered ones must not erase a foreign signal
        (the plan still touches that pour), and it must not be upgraded to
        ``none`` either — the aggregate keeps the answered evidence and the
        caller sees the per-point detail in ``points``.
        """
        seen = list(states)
        if not seen:
            return UNKNOWN
        if any(state == FOREIGN for state in seen):
            return FOREIGN
        if any(state == MIXED for state in seen):
            return MIXED
        if any(state == OWN for state in seen):
            return OWN
        if any(state == NONE for state in seen):
            return NONE
        return UNKNOWN

    def candidate_risk(
        self, waypoints: Iterable[Sequence[float]], net_code: int, *, margin_mm: float = 0.0,
        legs: Iterable[tuple[Sequence[float], Sequence[float]]] = (),
        pitch_mm: float = 0.0, max_samples: int = 0,
    ) -> CandidateZoneRisk:
        """Aggregate a plan's own named geometry into one ranking signal.

        ``waypoints`` is the candidate's declared geometry — the points the plan
        deliberately routes through. They are *not* the whole path: the router
        decides where vias actually land, so this is a sample of the plan and
        never a proof about it. A ``foreign`` or ``mixed`` result therefore only
        de-prioritises a plan within the list it is given; ``suppressed`` stays
        False and nothing is dropped here. Whether a de-prioritised plan is still
        attempted is the caller's business: the runner truncates to its
        ``candidate_limit`` after ranking, so a plan moved late can miss the
        attempt.

        ``legs`` adds straight corridors to sample (the candidate's own
        start→target line, for plans that name no waypoints), bounded by
        ``pitch_mm`` and ``max_samples`` per leg. Without legs a waypoint-less
        plan has no geometry to sample at all and is reported as unknown, which
        is honest but useless; with them the plan's corridor is what gets
        tested.
        """
        points = [tuple(point) for point in waypoints]
        for leg_start, leg_target in legs:
            points.extend(sample_leg(
                leg_start, leg_target, pitch_mm=pitch_mm, max_samples=max_samples))
        if not self.available():
            return CandidateZoneRisk(
                state=UNKNOWN, points_checked=0, margin_mm=float(margin_mm),
                available=False,
                reason=self.unsupported_reason or "engine exposes no zone point query",
            )
        if not points:
            return CandidateZoneRisk(
                state=UNKNOWN, points_checked=0, margin_mm=float(margin_mm),
                reason="the plan names no waypoints to sample",
            )

        verdicts = self.verdicts(points, net_code, margin_mm=margin_mm)
        nets: set[int] = set()
        risky_points: list[tuple[float, float, int]] = []
        for verdict in verdicts:
            nets.update(verdict.nets)
            if verdict.state in (FOREIGN, MIXED):
                risky_points.append(verdict.point)

        return CandidateZoneRisk(
            state=self._strongest(verdict.state for verdict in verdicts),
            points_checked=len(verdicts),
            points=tuple(risky_points),
            nets=tuple(sorted(nets)),
            margin_mm=float(margin_mm),
        )

    # -- bookkeeping -------------------------------------------------------

    @property
    def stats(self) -> dict[str, int]:
        """Query/cache counters, for the run's own evidence.

        ``queries`` counts point rows that needed an engine call; ``cache_hits``
        counts rows served from the cache (and therefore re-classified locally,
        which is why a hit is not "the same verdict" — it is the same copper).
        """
        return {
            "queries": int(self.queries),
            "cache_hits": int(self.cache_hits),
            "batches": int(self.batches),
            "cached_points": len(self._row_cache),
        }


@dataclass(frozen=True)
class ZoneOpening:
    """A measured spot on a cross-layer pair's corridor to place the via.

    ``point`` is ``(x_mm, y_mm, transition_layer)`` — the human layer the plan
    should be on *after* the via, which is the far endpoint's layer. Handing this
    straight to ``connect_targets(waypoints=...)`` makes the session emit
    ``make_via`` at exactly this coordinate, so the family controls where the via
    drops instead of leaving it to the router.

    ``clearance_mm`` is the *measured* distance from this point to the nearest
    foreign-net zone copper across both copper faces a through via touches. It is
    a floor, not a proof: values are capped at the search window, and a point with
    no foreign pour inside the window reports the window. ``own_contact`` says an
    own-net pour is within the via's own copper radius + clearance here, which is
    the pour-mediated case worth preferring over an empty spot.

    This is deliberately not a legality claim. The opening is a *candidate*: the
    via still has to be routed to, the transactional native DRC still decides
    whether the copper may be kept, and a spot the pre-check likes can still be
    refused.
    """

    point: tuple[float, float, int]
    clearance_mm: float
    own_contact: bool
    provenance: str
    unknown: bool = False

    def to_evidence(self) -> dict[str, Any]:
        return {
            "x_mm": round(float(self.point[0]), 4),
            "y_mm": round(float(self.point[1]), 4),
            "layer": int(self.point[2]),
            "clearance_mm": round(float(self.clearance_mm), 4),
            "own_contact": bool(self.own_contact),
            "fill_provenance": self.provenance,
            "is_unknown": bool(self.unknown),
        }


def via_layer_span(coverage: "ZoneCoverage") -> tuple[int, ...] | None:
    """The human copper layers a via placed by this harness spans, or ``None``.

    Read from the engine's own layer map (``layer_map.board_layer_order`` and its
    length), not from the two layers a plan happens to change between. The
    harness places its via with the router's own through-via action, so the barrel
    and the hole exist on **every** copper layer in the stack: a span of just the
    two endpoints was measured to miss foreign planes on interior layers, and the
    corrected replay of the phase-14 refusals showed findings on a layer outside
    even the endpoints' interval.

    Returning the whole stack is therefore the fail-closed answer: it is a
    superset of any partial (blind/buried) pair, so a screen built on it can only
    refuse more openings, never fewer. ``None`` means the map could not be read —
    in which case the caller must not offer an opening at all.

    The map is checked rather than trusted. Length agreement with ``max_layer``
    alone is not enough: a map can repeat a layer, name a non-copper layer, or
    carry metadata that cannot be converted at all, and each of those means the
    span is not proven. Any of them returns ``None`` — every failure is a refusal,
    never a smaller span.
    """
    engine = getattr(coverage, "engine", None)
    layer_map = getattr(engine, "layer_map", None)
    try:
        order = getattr(layer_map, "board_layer_order", None)
        ordered = [int(layer) for layer in order] if order is not None else []
        # ``max_layer`` is metadata too: a missing, non-numeric or unconvertible
        # value is a refusal, not a default.
        max_layer = int(layer_map.max_layer)
    except Exception:  # noqa: BLE001 - any malformed metadata is a refusal
        return None
    if len(ordered) < 2:
        return None
    # LayerMapping numbers copper layers 1..N in top-to-bottom order; the map and
    # its ordering must agree, every layer must appear exactly once, and every
    # entry must be a copper layer (the engine's ids are even and non-negative:
    # F.Cu 0, B.Cu 2, then the inner layers from 4 upwards in steps of two).
    if max_layer != len(ordered):
        return None
    if len(set(ordered)) != len(ordered):
        return None
    if any(layer < 0 or layer % 2 for layer in ordered):
        return None
    # The map's own converters are the authority for "mappable": when it exposes
    # them, human and board ids must agree in both directions.
    try:
        human_to_board = getattr(layer_map, "human_to_board", None)
        board_to_human = getattr(layer_map, "board_to_human", None)
        for human, board in zip(range(1, len(ordered) + 1), ordered):
            if callable(human_to_board) and int(human_to_board(human)) != board:
                return None
            if callable(board_to_human) and int(board_to_human(board)) != human:
                return None
    except Exception:  # noqa: BLE001 - an unmappable layer is a refusal
        return None
    return tuple(range(1, len(ordered) + 1))


def find_openings(
    coverage: "ZoneCoverage",
    start: Sequence[float],
    target: Sequence[float],
    net_code: int,
    *,
    margin_mm: float,
    search_mm: float,
    pitch_mm: float,
    max_samples: int,
    max_openings: int,
    band_mm: float,
    obstacles: Iterable[Mapping[str, Any]] = (),
) -> list[ZoneOpening]:
    """Measured via-placement openings along a cross-layer pair's corridor.

    Only a pair whose endpoint layers differ can use this: the via *is* the
    transition, so a same-layer pair has nothing to transition to. The corridor
    is sampled once (bounded by ``pitch_mm`` and ``max_samples``) and each sample
    is asked about **every copper layer the via spans** — see
    :func:`via_layer_span` — because a via's barrel and hole exist on all of
    them, not only on the two the plan changes between.

    A sample is offered only when it is measurably clear:

    * every covering zone on every spanned layer resolved (a zone with no stored fill
      makes the point unknown, and unknown is never offered as an opening);
    * the nearest foreign-net pour copper is at least ``margin_mm`` away on every
      spanned layer — a distance, not a containment test, so the via's own radius
      and the board's clearance are already accounted for;
    * no foreign track/via/pad from the caller's obstacle observation is closer
      than ``margin_mm``. This is the *existing* prefilter, reused; a pad or hole
      the via provably cannot land on is not an opening.

    An unreadable layer map — or any layer whose answer is unknown — disqualifies
    the sample rather than being skipped: not knowing what a via spans is a
    reason to refuse an opening, never to assume it is clear.

    ``max_openings`` bounds how many are returned and ``band_mm`` keeps them
    apart, so the family offers distinct places rather than a cluster of
    neighbours. Points that qualify on an own-net pour sort first (the
    pour-mediated case), then by measured clearance.
    """
    if int(max_openings) <= 0:
        return []
    start_layer, target_layer = int(start[2]), int(target[2])
    if start_layer == target_layer:
        return []
    if not coverage.available():
        return []
    span = via_layer_span(coverage)
    if not span:
        # Fail closed: without the board's own layer order there is no way to
        # know which copper the via touches, and "the two endpoints" is exactly
        # the assumption that let foreign planes through on interior layers.
        return []
    if start_layer not in span or target_layer not in span:
        # An endpoint on a layer the map does not prove is not a place this board
        # can put copper: refusing here keeps a caller's impossible layer from
        # being reported as a measured opening.
        return []

    samples = sample_leg(start, target, pitch_mm=pitch_mm, max_samples=max_samples)
    if not samples:
        return []

    # Positions first, then the span: ``sample_leg`` duplicates a layer-changing
    # leg across its two endpoint layers (useful for the ranking path, which
    # consumes the samples as points), while this finder asks about every layer
    # itself and only needs each position once. Deduplicating here is what keeps
    # the query count at ``positions x spanned layers`` instead of double that.
    positions: list[tuple[float, float]] = []
    seen_positions: set[tuple[float, float]] = set()
    for x, y, _layer in samples:
        position = (round(float(x), 4), round(float(y), 4))
        if position in seen_positions:
            continue
        seen_positions.add(position)
        positions.append(position)

    # Every spanned layer, one batched query.
    probes: list[tuple[float, float, int]] = []
    for x, y in positions:
        for layer in span:
            probes.append((x, y, layer))

    margin = max(0.0, float(margin_mm))
    search = max(margin, float(search_mm))
    # A wider window than the acceptance margin: the *ranking* wants to know how
    # much room there is, not only whether the point fails the margin test.
    verdicts = coverage.verdicts(probes, net_code, margin_mm=search)
    by_point = {(round(v.point[0], 4), round(v.point[1], 4), int(v.point[2])): v
                for v in verdicts}

    blocking: list[tuple[float, float, int, float, bool, str, bool]] = []
    # The via only decides where the layer changes; the copper *before* it is
    # routed on the start layer, and the router cannot see pours at all (KiCad's
    # sync admits rule-area keepouts and nothing else), so a straight approach
    # that crosses a foreign pour is a violation no via placement can rescue.
    # The prefix is therefore part of the measurement: an opening is offered
    # only when every sample from the start endpoint to it is itself clear.
    approach_clear = True
    for x, y in positions:
        start_face = by_point.get((round(x, 4), round(y, 4), start_layer))
        if start_face is None or start_face.state == UNKNOWN:
            approach_clear = False
        else:
            measured = (search if start_face.foreign_distance_mm is None
                        else float(start_face.foreign_distance_mm))
            if measured < margin:
                approach_clear = False
        if not approach_clear:
            continue                       # and it stays false for everything after
        faces = [by_point.get((round(x, 4), round(y, 4), layer))
                 for layer in span]
        if any(face is None for face in faces):
            continue                       # a face we cannot see is not an opening
        if any(face.state == UNKNOWN for face in faces):
            continue                       # unknown is never offered as an opening
        # A netless pour counts as foreign: its copper is nobody's to use, so it
        # is measured by the same distance as any other net.
        distances = [
            search if face.foreign_distance_mm is None else float(face.foreign_distance_mm)
            for face in faces
        ]
        clearance = min(distances)
        own = any(int(net_code) in face.nets for face in faces)
        provenance = faces[0].provenance if faces else NO_FILL
        blocking.append((x, y, target_layer, clearance, own, provenance, False))

    if not blocking:
        return []

    # Reuse the caller's existing obstacle observation for tracks/vias/pads: a
    # sample beside a foreign pad or hole is not an opening either.
    rejects: list[tuple[float, float]] = []
    for obstacle in obstacles or ():
        try:
            ox = float(obstacle["x_mm"])
            oy = float(obstacle["y_mm"])
            od = float(obstacle.get("distance_mm") or 0.0)
        except (KeyError, TypeError, ValueError):
            continue
        # ``distance_mm`` is centre-to-centre; the obstacle's own size is not in
        # the record, so the conservative reading is the centre distance itself.
        if od < margin:
            rejects.append((ox, oy))

    chosen: list[ZoneOpening] = []
    for x, y, layer, clearance, own, provenance, _u in sorted(
            blocking, key=lambda row: (-int(row[4]), -row[3], row[0], row[1])):
        if clearance < margin:
            continue
        if any(math.hypot(x - rx, y - ry) < band_mm for rx, ry in rejects):
            continue
        if any(math.hypot(x - opening.point[0], y - opening.point[1]) < band_mm
               for opening in chosen):
            continue
        chosen.append(ZoneOpening(
            point=(round(float(x), 4), round(float(y), 4), int(layer)),
            clearance_mm=float(min(clearance, search)),
            own_contact=bool(own),
            provenance=str(provenance)))
        if len(chosen) >= int(max_openings):
            break
    return chosen


@dataclass(frozen=True)
class EdgeScreen:
    """One edge's read-only screening result.

    ``status`` is one of:

    * ``openings``   — at least one measured opening (``openings`` holds them);
    * ``none``       — screened, and the corridor has no qualifying spot;
    * ``same_layer`` — the endpoints share a layer, so a via hop is not the shape
      and the family has nothing to say about it;
    * ``unknown``    — the query could not answer (older engine, unresolved zone
      or layer), so nothing is claimed either way;
    * ``budget``     — not screened: the caller's edge or wall-clock bound ran out.

    ``reason`` carries the specific cause for the two non-answers. A screen is a
    *measurement*, never a legality claim, and screening never mutates the board:
    it is the cheap pass that decides whether a transactional trial is worth
    spending at all.
    """

    edge: tuple
    net_code: int
    cross_layer: bool
    status: str
    openings: tuple["ZoneOpening", ...] = ()
    reason: str = ""
    #: How many copper layers the screen asked about for this edge — every layer
    #: the via spans, or 0 when the screen could not read the board's layer map
    #: and therefore refused to offer anything.
    layers_checked: int = 0

    @property
    def has_openings(self) -> bool:
        return self.status == "openings"

    def to_evidence(self) -> dict[str, Any]:
        return {
            "edge": list(self.edge),
            "net_code": int(self.net_code),
            "cross_layer": bool(self.cross_layer),
            "status": self.status,
            "openings": [opening.to_evidence() for opening in self.openings],
            "reason": self.reason,
            "layers_checked": int(self.layers_checked),
        }


def screen_openings(
    coverage: "ZoneCoverage",
    edges: Iterable[tuple[tuple, int]],
    *,
    margin_mm: float,
    max_edges: int | None = None,
    time_budget_s: float | None = None,
    clock: Callable[[], float] = time.monotonic,
    pitch_mm: float = 0.5,
    max_samples: int = 96,
    max_openings: int = 3,
    band_mm: float = 0.5,
    search_mm: float = 3.0,
    obstacles: Mapping[tuple, Iterable[Mapping[str, Any]]] | None = None,
) -> list[EdgeScreen]:
    """Measure openings for many edges without touching the board.

    ``edges`` is a sequence of ``(edge_key, net_code)`` where ``edge_key`` is the
    canonical 6-tuple :func:`pcb_world.agent.observations.edge_key` produces —
    ``(x0, y0, layer0, x1, y1, layer1)``. The function is read-only end to end:
    it only issues the zone query (whose answers are cached per point by
    :class:`ZoneCoverage`), never starts a route and never checkpoints, so it can
    be run against a live board to decide which edges deserve a transactional
    trial.

    Two bounds are enforced, both optional and both reported rather than hidden:
    ``max_edges`` caps how many edges are screened and ``time_budget_s`` caps the
    wall clock. Anything not reached comes back with status ``budget`` — a
    screening that ran out of room never looks like a screening that found
    nothing.

    ``obstacles`` optionally maps an edge key to that edge's foreign
    track/via/pad observation, reusing the caller's existing prefilter; without
    it the opening test still applies the zone geometry and the approach rule.
    """
    rows: list[EdgeScreen] = []
    started = clock()
    budget = None if time_budget_s is None else started + max(0.0, float(time_budget_s))
    limit = None if max_edges is None else max(0, int(max_edges))

    for edge_key, net_code in edges:
        key = tuple(edge_key)
        net = int(net_code)
        if len(key) != 6:
            rows.append(EdgeScreen(key, net, False, "unknown", (),
                                   "edge key is not (x0, y0, layer0, x1, y1, layer1)"))
            continue
        start = (float(key[0]), float(key[1]), int(key[2]))
        target = (float(key[3]), float(key[4]), int(key[5]))
        cross = int(start[2]) != int(target[2])
        if not cross:
            rows.append(EdgeScreen(key, net, False, "same_layer", (),
                                   "a via hop is not this edge's shape"))
            continue
        if limit is not None and len(rows) >= limit:
            rows.append(EdgeScreen(key, net, True, "budget", (),
                                   f"max_edges={limit} reached before this edge"))
            continue
        if budget is not None and clock() > budget:
            rows.append(EdgeScreen(key, net, True, "budget", (),
                                   f"time_budget_s={time_budget_s} reached"))
            continue
        if not coverage.available():
            rows.append(EdgeScreen(
                key, net, True, "unknown", (),
                coverage.unsupported_reason or "engine exposes no zone point query"))
            continue
        span = via_layer_span(coverage)
        if not span:
            # No readable layer map: refuse the edge rather than guess the span.
            rows.append(EdgeScreen(
                key, net, True, "unknown", (),
                "the board's copper layer map could not be read, so the layers a "
                "via spans are unknown and no opening may be offered", 0))
            continue
        if int(start[2]) not in span or int(target[2]) not in span:
            rows.append(EdgeScreen(
                key, net, True, "unknown", (),
                "an endpoint names a human layer the board's proven span does not "
                "contain, so this edge cannot be placed here", len(span)))
            continue
        openings = find_openings(
            coverage, start, target, net,
            margin_mm=margin_mm, search_mm=search_mm, pitch_mm=pitch_mm,
            max_samples=max_samples, max_openings=max_openings, band_mm=band_mm,
            obstacles=(obstacles or {}).get(key, ()))
        if openings:
            rows.append(EdgeScreen(key, net, True, "openings", tuple(openings),
                                   "", len(span)))
        else:
            rows.append(EdgeScreen(
                key, net, True, "none", (), 
                "no corridor sample had both a clear approach and the margin "
                "clear of foreign pour on every layer the via spans",
                len(span)))
    return rows


def screen_summary(rows: Iterable[EdgeScreen]) -> dict[str, int]:
    """Aggregate counts over screens — the public, geometry-free view."""
    counts: dict[str, int] = {}
    for row in rows:
        counts[row.status] = counts.get(row.status, 0) + 1
    counts["total"] = sum(counts.values())
    counts["edges_with_openings"] = counts.get("openings", 0)
    counts["openings_total"] = sum(len(row.openings) for row in rows)
    return counts
