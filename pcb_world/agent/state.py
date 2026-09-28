"""Authoritative state snapshots and canonical geometry for the agent layer.

A caller never has to guess what the engine did. Every action — including every
failure — returns an :class:`AgentSnapshot` carrying:

* a monotonically increasing ``revision`` and an opaque ``token`` that binds
  that revision to this *session* and to a fingerprint of the live engine state;
* whether a route is active, and the route head / target / layer **when the
  engine actually reports them** (``None`` otherwise — never a placeholder);
* the classified :attr:`AgentSnapshot.outcome` and machine-readable evidence.

Two rules this module enforces on itself:

1. **Nothing is invented.** If reading the engine raises, the snapshot is
   ``unverified`` and carries the error text. It never reports a default, a
   zero, or a stale value as if it were current.
2. **Geometry is compared canonically.** :func:`canonical_rows` quantises to
   nanometres (KiCad's internal unit), sorts items, and includes every property
   the wire mirrors actually expose — including a via's layer span. What the
   mirrors do *not* expose is listed by :func:`unverifiable_properties` and
   travels with rollback evidence, so an "exact restore" claim never covers a
   property nobody could read back.
"""

from __future__ import annotations

import hashlib
import math
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Sequence

from pcb_world.agent.actions import SCHEMA_VERSION


class Outcome(str, Enum):
    """Classified result of one agent operation."""

    OK = "ok"
    ALREADY_CONNECTED = "already_connected"
    NO_ACTIVE_ROUTE = "no_active_route"
    STALE_STATE = "stale_state"
    INVALID_ACTION = "invalid_action"
    ROUTING_FAILED = "routing_failed"
    UNSUPPORTED = "unsupported"
    UNVERIFIED = "unverified"


SUCCESS_OUTCOMES: frozenset[Outcome] = frozenset({Outcome.OK, Outcome.ALREADY_CONNECTED})

# KiCad works in nanometres; 1 nm = 1e-6 mm. Canonical rows are integer nm so a
# comparison cannot be defeated by float formatting or a sub-nm jitter.
NM_PER_MM = 1_000_000


def to_nm(value_mm: Any) -> int:
    """Canonical integer nanometres for a millimetre value."""
    return int(round(float(value_mm) * NM_PER_MM))


def _round_anchor(point: Sequence[Any]) -> tuple[float, float, int]:
    """Stable rounding for connectivity anchors (KiCad works in nm)."""
    return (round(float(point[0]), 4), round(float(point[1]), 4), int(point[2]))


def anchor_set(points: Sequence[Sequence[Any]] | None) -> frozenset[tuple[float, float, int]]:
    """Normalise an anchor list for set comparison; ``None`` -> empty set."""
    if not points:
        return frozenset()
    return frozenset(_round_anchor(p) for p in points)


def unverifiable_properties() -> tuple[str, ...]:
    """Properties the engine's wire mirrors do not expose.

    Listed explicitly so a rollback is never reported as "exact" over data that
    could not be read back. Sourced from ``pcb_world.engine.wire``'s
    ``TrackInfo`` / ``ViaInfo`` (the only geometry the client sees).
    """
    return (
        "track_type (arc vs segment: TrackInfo carries endpoints only)",
        "arc_mid_point (not exposed; an arc is compared by endpoints/layer/net/width)",
        "item_locked_flag (not exposed as a mirror field)",
        "solder_mask_margin (not exposed as a mirror field)",
        "pad_rotation_for_non_cardinal_angles (only 90/270 are baked into w/h)",
        "routing_target (advisory router hint; the engine recomputes it on resync "
        "and does not round-trip it through checkpoint/restore)",
    )


def _attr(obj: Any, name: str, default: Any = None) -> Any:
    return getattr(obj, name, default)


def canonical_rows(
    tracks: Sequence[Any] | None, vias: Sequence[Any] | None
) -> tuple:
    """Canonical, sorted, integer-nanometre rows for every visible copper item.

    Tracks: endpoints, width, layer, net. Vias: position, diameter, drill and the
    full layer span (top AND bottom), net. Nothing is rounded away.
    """
    rows: list[tuple] = []
    for t in tracks or ():
        rows.append((
            "track",
            to_nm(_attr(t, "x1_mm", 0.0)), to_nm(_attr(t, "y1_mm", 0.0)),
            to_nm(_attr(t, "x2_mm", 0.0)), to_nm(_attr(t, "y2_mm", 0.0)),
            to_nm(_attr(t, "width_mm", 0.0)),
            int(_attr(t, "layer", -1)), int(_attr(t, "net_code", -1)),
        ))
    for v in vias or ():
        rows.append((
            "via",
            to_nm(_attr(v, "x_mm", 0.0)), to_nm(_attr(v, "y_mm", 0.0)),
            to_nm(_attr(v, "diameter_mm", 0.0)), to_nm(_attr(v, "drill_mm", 0.0)),
            int(_attr(v, "top_layer", -1)), int(_attr(v, "bottom_layer", -1)),
            int(_attr(v, "net_code", -1)),
        ))
    rows.sort()
    return tuple(rows)


def rows_digest(rows: tuple) -> str:
    h = hashlib.sha256()
    for row in rows:
        h.update(repr(row).encode())
    return h.hexdigest()[:16]


def geometry_digest(
    tracks: Sequence[Any] | None,
    vias: Sequence[Any] | None,
) -> str:
    """Content digest of the copper a transaction must be able to restore."""
    return rows_digest(canonical_rows(tracks, vias))


def nets_changed(before: tuple, after: tuple) -> tuple[int, ...]:
    """Net codes whose canonical rows differ between two row snapshots."""
    def by_net(rows: tuple) -> dict[int, list[tuple]]:
        out: dict[int, list[tuple]] = {}
        for row in rows:
            out.setdefault(int(row[-1]), []).append(row)
        return out

    b, a = by_net(before), by_net(after)
    changed = [net for net in set(b) | set(a) if b.get(net) != a.get(net)]
    return tuple(sorted(changed))


def copper_totals(rows: tuple) -> tuple[float, int, int]:
    """``(copper_length_mm, track_count, via_count)`` from canonical rows.

    Length is the sum of track segment lengths, which is the "added copper"
    quality signal a routing agent wants; vias are counted, not measured.
    """
    length_mm = 0.0
    tracks = 0
    vias = 0
    for row in rows:
        if not row:
            continue
        if row[0] == "track":
            tracks += 1
            length_mm += math.hypot(row[3] - row[1], row[4] - row[2]) / NM_PER_MM
        elif row[0] == "via":
            vias += 1
    return (round(length_mm, 6), tracks, vias)


@dataclass(frozen=True)
class StateProbe:
    """Raw, read-only engine state as observed at one instant.

    ``error`` is set (and every other field left ``None``) when the engine could
    not be read — the snapshot built from this probe is then ``unverified``.
    """

    route_active: bool | None = None
    current_net_code: int | None = None
    head: tuple[float, float, float] | None = None
    target: tuple[float, float, float] | None = None
    layer: int | None = None
    track_count: int | None = None
    via_count: int | None = None
    unrouted_count: int | None = None
    rows: tuple = ()
    geometry_digest: str | None = None
    error: str | None = None

    @property
    def verified(self) -> bool:
        return self.error is None


def state_fingerprint(probe: StateProbe) -> str:
    """Fingerprint of everything a token should bind: copper + session state."""
    material = "|".join(
        "" if value is None else str(value)
        for value in (
            probe.route_active, probe.current_net_code, probe.head, probe.target,
            probe.layer, probe.track_count, probe.via_count, probe.unrouted_count,
            probe.geometry_digest,
        )
    )
    return hashlib.sha256(material.encode()).hexdigest()[:12]


def snapshot_token(revision: int, probe: StateProbe, session_id: str) -> str:
    """Opaque token = session identity + revision + live-state fingerprint.

    All three parts matter: the session id stops a token from another session
    being replayed, the revision catches "you are answering a question I have
    already moved past", and the fingerprint catches a board mutated by anything
    other than this session (another handle, a direct engine call) even when the
    item count is unchanged.
    """
    return f"{session_id}.r{revision}.{state_fingerprint(probe)}"


def parse_token(token: str) -> tuple[str, int, str] | None:
    """Split a token; ``None`` when it is not one this module issued."""
    if not isinstance(token, str):
        return None
    parts = token.split(".")
    if len(parts) != 3 or not parts[1].startswith("r"):
        return None
    try:
        revision = int(parts[1][1:])
    except ValueError:
        return None
    return parts[0], revision, parts[2]


@dataclass(frozen=True)
class AgentSnapshot:
    """Immutable, versioned view of the engine after one agent operation."""

    revision: int
    token: str
    outcome: Outcome
    session_id: str = ""
    route_active: bool | None = None
    current_net_code: int | None = None
    head: tuple[float, float, float] | None = None
    target: tuple[float, float, float] | None = None
    layer: int | None = None
    track_count: int | None = None
    via_count: int | None = None
    unrouted_count: int | None = None
    geometry_digest: str | None = None
    allowed_next_actions: tuple[str, ...] = ()
    evidence: Mapping[str, Any] = field(default_factory=dict)
    schema_version: str = SCHEMA_VERSION
    action: str | None = None
    dirty: bool = False

    @property
    def verified(self) -> bool:
        """False when the engine could not be read; the snapshot claims nothing."""
        return self.outcome is not Outcome.UNVERIFIED

    @property
    def succeeded(self) -> bool:
        return self.outcome in SUCCESS_OUTCOMES

    def to_dict(self) -> dict[str, Any]:
        """JSON-shaped form for tool output / logs."""
        return {
            "schema_version": self.schema_version,
            "revision": self.revision,
            "token": self.token,
            "session_id": self.session_id,
            "outcome": self.outcome.value,
            "action": self.action,
            "dirty": self.dirty,
            "route_active": self.route_active,
            "current_net_code": self.current_net_code,
            "head": list(self.head) if self.head is not None else None,
            "target": list(self.target) if self.target is not None else None,
            "layer": self.layer,
            "track_count": self.track_count,
            "via_count": self.via_count,
            "unrouted_count": self.unrouted_count,
            "geometry_digest": self.geometry_digest,
            "allowed_next_actions": list(self.allowed_next_actions),
            "evidence": dict(self.evidence),
        }


def allowed_actions(route_active: bool | None, *, dirty: bool = False) -> tuple[str, ...]:
    """Actions the observed phase permits.

    Unknown state (an unreadable engine) and a dirty session advertise **no**
    mutating action. While a route is open the transaction tool is not advertised
    either: ``connect_targets`` starts its own route and refuses to attach to an
    existing one, so offering it there would be a lie.
    """
    if dirty or route_active is None:
        return ()
    if route_active:
        return ("make_line", "make_via", "finish", "net_end")
    return ("net_select", "start_route", "connect_targets")


def build_snapshot(
    *,
    revision: int,
    probe: StateProbe,
    outcome: Outcome,
    session_id: str = "",
    action: str | None = None,
    evidence: Mapping[str, Any] | None = None,
    dirty: bool = False,
) -> AgentSnapshot:
    """Assemble a snapshot from a probe, never upgrading an unverified probe."""
    if not probe.verified:
        outcome = Outcome.UNVERIFIED
    elif dirty:
        outcome = Outcome.UNSUPPORTED
    return AgentSnapshot(
        revision=revision,
        token=snapshot_token(revision, probe, session_id),
        outcome=outcome,
        session_id=session_id,
        route_active=probe.route_active,
        current_net_code=probe.current_net_code,
        head=probe.head,
        target=probe.target,
        layer=probe.layer,
        track_count=probe.track_count,
        via_count=probe.via_count,
        unrouted_count=probe.unrouted_count,
        geometry_digest=probe.geometry_digest,
        allowed_next_actions=allowed_actions(probe.route_active, dirty=dirty),
        evidence=dict(evidence or {}),
        action=action,
        dirty=dirty,
    )


def new_session_id() -> str:
    """Short, unique id for one :class:`~pcb_world.agent.session.AgentSession`."""
    return uuid.uuid4().hex[:12]


class StaleStateError(RuntimeError):
    """A mutating call carried a token from a different, older state."""

    def __init__(self, message: str, **detail: Any) -> None:
        super().__init__(message)
        self.detail: dict[str, Any] = {"reason": Outcome.STALE_STATE.value, **detail}
