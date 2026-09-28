"""Transactional, state-guarded agent session over one KiCad engine.

The low-level interface is unchanged: ``start_route`` / ``make_line`` /
``make_via`` / ``finish`` still exist and this session dispatches through the
same functions in :mod:`pcb_world.core.action` the environment uses, so no
policy is re-implemented here. On top of them it adds what an agent needs to be
trustworthy:

1. **Validation before mutation.** Every action is validated while the engine
   lock is held; nothing malformed, out-of-phase or schema-unknown reaches C++.
2. **A token that binds to live state.** A token names this session, a revision
   *and* a fingerprint of the copper + routing session at issue time. A token
   from another session, an older revision, or a board mutated behind the
   session's back (even with an unchanged item count) is refused before anything
   is dispatched. ``AgentSession(..., require_tokens=False)`` is the explicit,
   documented opt-out; the model-facing tool adapter never uses it.
3. **A transaction.** :meth:`connect_targets` runs a deterministic
   start → advance → via/layer → finish sequence behind engine checkpoints, so a
   failed step is rolled back (shove displacement and router session state
   included). It is **atomic by default**: unless the connection is verified and
   the copper passes DRC acceptance, the whole attempt is rolled back.
   ``provisional=True`` is the explicit opt-in that keeps verified progress.
4. **Native DRC acceptance.** No mutation is kept if the engine's own DRC, under
   the proven rule context, reports a *new* relevant violation. Connectivity
   findings (unrouted ratsnest, dangling ends) are progress, not violations.
5. **Honest failure.** An unreadable engine is ``unverified``; a rollback the
   backend cannot perform quarantines the session instead of claiming
   ``committed=False`` while copper remains.
"""

from __future__ import annotations

import math
import threading
import weakref
from dataclasses import dataclass, field, replace
from typing import Any, Iterable, Mapping, Sequence

from pcb_world.core import action as core_action
from pcb_world.agent.actions import (
    ActionPhase,
    ActionValidator,
    BoardLimits,
    InvalidActionError,
    REASON_DRC_REGRESSION,
    REASON_DRC_UNAVAILABLE,
    REASON_ENDPOINT_AMBIGUOUS,
    REASON_ENDPOINT_UNKNOWN,
    REASON_CHECKPOINT_UNAVAILABLE,
    REASON_SESSION_QUARANTINED,
    REASON_TOKEN_REQUIRED,
    REASON_UNDERSPECIFIED,
    REASON_UNVERIFIED_STATE,
    StructuredAction,
    coerce_action,
    mode_to_int,
)
from pcb_world.agent.drc_gate import DrcGate, ViolationSet
from pcb_world.agent.rules import (
    RuleContext,
    RulesUnavailableError,
    assert_rules_applicable,
    resolve_rule_context,
    resolve_via_size,
)
from pcb_world.agent.state import (
    AgentSnapshot,
    Outcome,
    StateProbe,
    anchor_set,
    build_snapshot,
    canonical_rows,
    copper_totals,
    nets_changed,
    new_session_id,
    parse_token,
    rows_digest,
    state_fingerprint,
    unverifiable_properties,
)


# ---------------------------------------------------------------------------
# Engine serialisation
# ---------------------------------------------------------------------------

_LOCK_GUARD = threading.Lock()
_ENGINE_LOCKS: "weakref.WeakKeyDictionary[Any, threading.RLock]" = weakref.WeakKeyDictionary()


def engine_lock(engine: Any) -> threading.RLock:
    """Process-wide re-entrant lock for one engine object.

    The C++ router is a per-process singleton; concurrent use is unsafe. Every
    mutating entry point takes this lock, so one engine cannot be driven by two
    interleaved transactions.
    """
    with _LOCK_GUARD:
        lock = _ENGINE_LOCKS.get(engine)
        if lock is None:
            lock = threading.RLock()
            _ENGINE_LOCKS[engine] = lock
        return lock


# Actions that can change copper and therefore need the rule gate, a checkpoint
# and DRC acceptance. ``net_select`` only changes the selection.
_COPPER_ACTIONS = ("start_route", "make_line", "make_via", "finish", "net_end")

# How the copper stands after one operation. Distinct states because "no copper
# was added" and "copper may be left behind that we cannot account for" must not
# be reported the same way.
COPPER_UNCHANGED = "unchanged"
COPPER_RESTORED = "restored"
COPPER_KEPT = "kept"
COPPER_RETAINED_UNKNOWN = "retained_unknown"


class _UnverifiedState(RuntimeError):
    """The engine could not be read after a mutation; the mutation must be undone."""


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Endpoint:
    """Authoritative identity of one connection endpoint."""

    point: tuple[float, float, int]
    net_code: int
    cluster: frozenset
    pads: tuple[str, ...] = ()
    has_via: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "point": list(self.point),
            "net_code": self.net_code,
            "cluster_size": len(self.cluster),
            "pads": list(self.pads),
            "has_via": self.has_via,
        }


@dataclass(frozen=True)
class TransactionStep:
    """One mechanical step inside a connection transaction."""

    kind: str                      # start | line | via | switch | finish
    point: tuple[float, float] | None = None
    layer: int | None = None
    success: bool = False
    restored: bool = False
    head_after: tuple[float, float, float] | None = None
    detail: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "point": list(self.point) if self.point else None,
            "layer": self.layer,
            "success": self.success,
            "restored": self.restored,
            "head_after": list(self.head_after) if self.head_after else None,
            "detail": dict(self.detail),
        }


@dataclass(frozen=True)
class ConnectionResult:
    """Result of one transactional connection attempt.

    ``connected`` / ``committed`` describe the **final** board: if the attempt
    was rolled back they are reported from the restored state, never from the
    state that existed before the rollback.

    ``committed`` is three-valued on purpose:

    * ``True``  — copper from this attempt is on the board (verified);
    * ``False`` — no copper from this attempt remains (verified restore, or
      nothing was committed);
    * ``None``  — unknown: a rollback could not be established, so neither claim
      is made. ``copper_state`` carries the same distinction as a string, and
      ``dirty`` marks the session as quarantined.

    ``would_commit`` / ``evaluated`` exist for candidate probing: a probe result
    says what the candidate *would* have committed, while the board itself was
    restored (``committed=False``).
    """

    outcome: Outcome
    committed: bool | None
    connected: bool
    start: tuple[float, float, int] | None
    target: tuple[float, float, int] | None
    mode: str
    steps: tuple[TransactionStep, ...] = ()
    snapshot: AgentSnapshot | None = None
    evidence: Mapping[str, Any] = field(default_factory=dict)
    rollback_verified: bool | None = None
    identical_start: bool | None = None
    accepted: bool = False
    dirty: bool = False
    copper_state: str = COPPER_UNCHANGED
    would_commit: bool | None = None
    evaluated: bool = False

    @property
    def succeeded(self) -> bool:
        return self.outcome in (Outcome.OK, Outcome.ALREADY_CONNECTED)

    def to_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome.value,
            "committed": self.committed,
            "connected": self.connected,
            "accepted": self.accepted,
            "dirty": self.dirty,
            "copper_state": self.copper_state,
            "would_commit": self.would_commit,
            "evaluated": self.evaluated,
            "rollback_verified": self.rollback_verified,
            "identical_start": self.identical_start,
            "start": list(self.start) if self.start else None,
            "target": list(self.target) if self.target else None,
            "mode": self.mode,
            "steps": [s.to_dict() for s in self.steps],
            "evidence": dict(self.evidence),
            "snapshot": self.snapshot.to_dict() if self.snapshot else None,
        }


@dataclass(frozen=True)
class CandidateProbe:
    """One candidate strategy evaluated from the shared checkpoint."""

    name: str
    result: ConnectionResult

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "result": self.result.to_dict()}


def _point3(raw: Any) -> tuple[float, float, int] | None:
    """Best-effort 3-tuple for records, or ``None`` when the input is unusable.

    Never raises and never invents a coordinate: a record for a malformed
    endpoint carries ``None`` (JSON ``null``), not a NaN that a strict JSON
    encoder would reject.
    """
    if not isinstance(raw, (tuple, list)):
        return None
    values = list(raw)
    if len(values) == 2:
        values = [values[0], values[1], 1]
    if len(values) < 2:
        return None
    try:
        x_mm = float(values[0])
        y_mm = float(values[1])
        layer = int(values[2]) if len(values) > 2 else 1
    except (TypeError, ValueError, OverflowError):
        return None
    if not (math.isfinite(x_mm) and math.isfinite(y_mm)):
        return None
    return (x_mm, y_mm, layer)


# ---------------------------------------------------------------------------
# Session
# ---------------------------------------------------------------------------

class AgentSession:
    """Owns one engine, one validator, one rule context and one revision counter."""

    def __init__(
        self,
        engine: Any,
        *,
        board_path: str | None = None,
        limits: BoardLimits | None = None,
        rule_context: RuleContext | None = None,
        require_tokens: bool = True,
        incremental_drc: bool = False,
    ) -> None:
        self._engine = engine
        self.board_path = board_path or getattr(engine, "board_path", "") or ""
        self._lock = engine_lock(engine)
        self._limits = limits or self._derive_limits()
        self._validator = ActionValidator(self._limits)
        # Never None: a session constructed without a board path still gets a
        # context from the engine's own project path instead of raising later.
        self.rule_context = rule_context or resolve_rule_context(
            board_path, engine=engine
        )
        self._session_id = new_session_id()
        self.require_tokens = bool(require_tokens)
        self._revision = 0
        self._last_token: str | None = None
        self._dirty = False
        self._dirty_reason: str | None = None
        #: Opt-in scoped acceptance pass. Off by default: the acceptance gate is a
        #: whole-board DRC unless a caller has run the full-vs-incremental
        #: differential and turned it on (`tools/reliability/
        #: drc_incremental_differential.py`). Either way the saved artifact is
        #: re-verified by a fresh native child with a full DRC before promotion.
        self.incremental_drc = bool(incremental_drc)
        self._gate = DrcGate(self, allow_incremental_drc=self.incremental_drc)
        #: Every via size this session resolved, keyed by the net it was resolved
        #: for, plus the last pair actually handed to the router. Kept as a log
        #: because the router's size cache is process state, not board state: a
        #: run that routes several classes has to be able to say which size was in
        #: force for which net.
        self.routing_sizes: dict[str, Any] = {
            "by_net": {}, "applied": None, "failure": "",
        }
        #: The board's default class, applied once so a session that never routes
        #: still leaves the router with the project's own size rather than its
        #: built-in default. Each transaction then re-resolves for its own net.
        self._via_size_for_net(0)

    def _via_size_for_net(self, net_code: int) -> dict[str, Any]:
        """The via size in force for ``net_code``, applying it if it changed.

        The router's size cache is process state and it is not populated from the
        project; a size that satisfies one net's class does not necessarily
        satisfy another's, so the size is resolved for the net actually being
        routed and compared against the size *in force* on every entry - A -> B
        -> A has to end with A's size active, which a per-net cache that answers
        from memory cannot know. Diameter and drill are set as a *pair*, and any
        exception from either setter is an unknown outcome: this engine exposes no
        readback for its active sizes, so there is no way to prove the router's
        state, and the session quarantines itself instead of routing on it. The
        declared and adopted numbers are both kept, and the adoption is a starting
        point - the transaction's native DRC acceptance and the saved artifact's
        fresh whole-board gate are what prove the copper is lawful.
        """
        # Never answered from a per-net cache: what matters is the size actually
        # in force, which the last application chose, not what this net was told
        # some earlier time.
        resolution = resolve_via_size(self._engine, int(net_code))
        entry = resolution.to_evidence()
        entry["applied"] = False
        entry["attempted_diameter_mm"] = resolution.adopted_diameter_mm
        entry["attempted_drill_mm"] = resolution.adopted_drill_mm
        if not resolution.usable:
            entry["apply_reason"] = resolution.reason
            self.routing_sizes["by_net"][int(net_code)] = entry
            return entry
        applied = self.routing_sizes.get("applied")
        if applied is not None and (
            applied["adopted_via_diameter_mm"] == resolution.adopted_diameter_mm
            and applied["adopted_via_drill_mm"] == resolution.adopted_drill_mm
        ):
            entry["applied"] = True
            entry["apply_reason"] = "already in force"
            self.routing_sizes["by_net"][int(net_code)] = entry
            return entry
        try:
            self._engine.set_via_diameter(resolution.adopted_diameter_mm)
            self._engine.set_via_drill(resolution.adopted_drill_mm)
        except Exception as exc:        # noqa: BLE001 - outcome unknown: fail closed
            reason = (
                "a via size setter failed, and this engine exposes no readback for "
                "its active sizes, so the router's state cannot be proven "
                f"({type(exc).__name__}: {exc}) while setting "
                f"{resolution.adopted_diameter_mm} mm / "
                f"{resolution.adopted_drill_mm} mm for net {int(net_code)}"
            )
            entry["apply_reason"] = reason
            self.routing_sizes["by_net"][int(net_code)] = entry
            self.routing_sizes["failure"] = reason
            self.quarantine(reason)
            return entry
        entry["applied"] = True
        entry["apply_reason"] = "applied"
        self.routing_sizes["by_net"][int(net_code)] = entry
        self.routing_sizes["applied"] = entry
        return entry

    # -- construction helpers ---------------------------------------------

    @classmethod
    def from_env(cls, env: Any, *, acknowledge_env_desync: bool = False) -> "AgentSession":
        """Adapter for an environment that owns the engine.

        **Mixing is not supported.** The environment keeps its own step counter,
        action mask, closed-net set, reward state and ``current_net_id``; a
        session transaction changes the engine without touching any of them, so
        after one transaction ``env.step`` would act on a bookkeeping state that
        no longer matches the board. Either drive the engine through
        :class:`AgentSession` or through ``env.step``, not both. The explicit
        ``acknowledge_env_desync=True`` is required to acknowledge that trade-off
        (for example, when the env is only used as a board holder).
        """
        engine = getattr(env, "_engine", None)
        if engine is None:
            raise ValueError("env has no _engine; construct AgentSession(engine=...) instead")
        if not acknowledge_env_desync:
            raise RuntimeError(
                "AgentSession.from_env bypasses the environment's step counter, action "
                "mask, closed-net set and reward state: mixing env.step with session "
                "calls is unsupported. Pass acknowledge_env_desync=True only if this "
                "env is not being stepped."
            )
        return cls(engine, board_path=getattr(env, "board_path", None))

    def _derive_limits(self) -> BoardLimits:
        bbox: tuple[float, float, float, float] | None = None
        copper = 2
        try:
            raw = self._engine.get_board_bbox()
            bbox = (
                float(raw.x_mm), float(raw.y_mm),
                float(raw.x_mm) + float(raw.width_mm),
                float(raw.y_mm) + float(raw.height_mm),
            )
        except Exception:  # noqa: BLE001 - absent outline is "no bounds", not an error
            bbox = None
        try:
            copper = int(self._engine.get_copper_layer_count())
        except Exception:  # noqa: BLE001 - documented 2-layer default
            copper = 2
        return BoardLimits(bbox=bbox, copper_layers=max(copper, 2))

    @property
    def limits(self) -> BoardLimits:
        return self._limits

    @property
    def revision(self) -> int:
        return self._revision

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def dirty(self) -> bool:
        return self._dirty

    @property
    def dirty_reason(self) -> str | None:
        return self._dirty_reason

    def quarantine(self, reason: str) -> None:
        """Mark this session unusable after a rollback the backend could not do.

        Copper may remain on the board that the session believes it removed, so
        every later mutation is refused: an honest stop beats compounding an
        unknown state. ``snapshot()`` still works and reports ``dirty``.
        """
        self._dirty = True
        self._dirty_reason = reason
        self._gate.invalidate()

    # -- probing -----------------------------------------------------------

    def _probe(self, *, include_geometry: bool = True) -> StateProbe:
        """Read the engine's state. Never raises; failure lands in ``error``."""
        try:
            session = self._engine.get_routing_session_state()
            track_count = int(self._engine.get_track_count())
            via_count = int(self._engine.get_via_count())
            unrouted = int(self._engine.get_unrouted_count())
            rows: tuple = ()
            digest = None
            if include_geometry:
                rows = canonical_rows(self._engine.get_tracks(), self._engine.get_vias())
                digest = rows_digest(rows)
            route_active = bool(session.is_routing)
            head = None
            if route_active and float(session.route_head[2]) >= 0:
                head = (
                    float(session.route_head[0]), float(session.route_head[1]),
                    float(session.route_head[2]),
                )
            target = None
            if route_active and float(session.routing_target[2]) >= 0:
                target = (
                    float(session.routing_target[0]), float(session.routing_target[1]),
                    float(session.routing_target[2]),
                )
            layer = int(session.current_layer)
            net_code = int(session.current_net_code)
            return StateProbe(
                route_active=route_active,
                current_net_code=net_code if net_code > 0 else None,
                head=head,
                target=target,
                layer=layer if layer >= 1 else None,
                track_count=track_count,
                via_count=via_count,
                unrouted_count=unrouted,
                rows=rows,
                geometry_digest=digest,
            )
        except Exception as exc:  # noqa: BLE001 - reported verbatim, never swallowed
            return StateProbe(error=f"{type(exc).__name__}: {exc}")

    def _emit(
        self,
        probe: StateProbe,
        outcome: Outcome,
        *,
        action: str | None = None,
        evidence: Mapping[str, Any] | None = None,
    ) -> AgentSnapshot:
        self._revision += 1
        snap = build_snapshot(
            revision=self._revision, probe=probe, outcome=outcome,
            session_id=self._session_id, action=action, evidence=evidence,
            dirty=self._dirty,
        )
        self._last_token = snap.token
        return snap

    def snapshot(self, *, include_geometry: bool = True) -> AgentSnapshot:
        """Take and record the authoritative state; the token comes back with it.

        ``include_geometry=False`` is a cheap read **without** a usable token: the
        token binds the full live fingerprint, so a geometry-less probe cannot
        mint one. The returned snapshot carries an empty ``token`` and
        ``allowed_next_actions == ()`` — callers that want to mutate must take a
        full snapshot.
        """
        with self._lock:
            probe = self._probe(include_geometry=include_geometry)
            if not include_geometry:
                self._revision += 1
                return AgentSnapshot(
                    revision=self._revision,
                    token="",
                    outcome=Outcome.UNSUPPORTED if self._dirty else Outcome.OK,
                    session_id=self._session_id,
                    route_active=probe.route_active,
                    current_net_code=probe.current_net_code,
                    head=probe.head,
                    target=probe.target,
                    layer=probe.layer,
                    track_count=probe.track_count,
                    via_count=probe.via_count,
                    unrouted_count=probe.unrouted_count,
                    geometry_digest=None,
                    allowed_next_actions=(),
                    evidence={
                        "token": "unavailable",
                        "note": (
                            "include_geometry=False cannot mint a mutation token: the "
                            "token binds the full copper fingerprint. Take a full "
                            "snapshot to mutate."
                        ),
                    },
                    dirty=self._dirty,
                )
            outcome = Outcome.UNSUPPORTED if self._dirty else Outcome.OK
            return self._emit(probe, outcome, evidence=(
                {"session_quarantined": True, "reason": self._dirty_reason}
                if self._dirty else None
            ))

    def geometry(self) -> dict[str, Any]:
        """Canonical copper + session state, for callers that keep their own checks."""
        with self._lock:
            probe = self._probe()
            return {
                "digest": probe.geometry_digest,
                "track_count": probe.track_count,
                "via_count": probe.via_count,
                "route_active": probe.route_active,
                "head": probe.head,
                "net_code": probe.current_net_code,
                "layer": probe.layer,
                "unverifiable": list(unverifiable_properties()),
            }

    # -- token checks ------------------------------------------------------

    def _token_status(self, token: str | None, probe: StateProbe) -> str:
        """``missing`` | ``malformed`` | ``foreign`` | ``stale`` | ``ok``."""
        if token is None:
            return "missing"
        parsed = parse_token(token)
        if parsed is None:
            return "malformed"
        session_id, revision, fingerprint = parsed
        if session_id != self._session_id:
            return "foreign"
        if revision != self._revision or fingerprint != state_fingerprint(probe):
            return "stale"
        return "ok"

    def _stale_evidence(self, token: str | None, status: str, probe: StateProbe) -> dict[str, Any]:
        return {
            "supplied_token": token,
            "token_status": status,
            "session_id": self._session_id,
            "revision": self._revision,
            "current_token": self._last_token,
            "live_fingerprint": state_fingerprint(probe),
        }

    def _gate_token(
        self, token: str | None, probe: StateProbe, action: str | None
    ) -> AgentSnapshot | None:
        """Return a refusal snapshot, or ``None`` when the call may proceed."""
        status = self._token_status(token, probe)
        if status == "ok":
            return None
        if status == "missing" and not self.require_tokens:
            return None            # documented opt-out (constructed explicitly)
        outcome = (
            Outcome.INVALID_ACTION if status in ("missing", "malformed")
            else Outcome.STALE_STATE
        )
        evidence = self._stale_evidence(token, status, probe)
        if status == "missing":
            evidence["reason"] = REASON_TOKEN_REQUIRED
            evidence["hint"] = (
                "take a snapshot and pass its token; construct the session with "
                "require_tokens=False to opt out explicitly"
            )
        return self._emit(probe, outcome, action=action, evidence=evidence)

    def _gate_unusable(self, probe: StateProbe, action: str | None) -> AgentSnapshot | None:
        if not self._dirty:
            return None
        return self._emit(
            probe, Outcome.UNSUPPORTED, action=action,
            evidence={"reason": REASON_SESSION_QUARANTINED, "detail": self._dirty_reason},
        )

    # -- rule gate ---------------------------------------------------------

    def _require_rules(self) -> dict[str, Any]:
        """Prove the applicable rule context, or raise :class:`RulesUnavailableError`."""
        if self._dirty:
            raise RulesUnavailableError(
                "session is quarantined; rule context cannot be trusted",
                reason=REASON_SESSION_QUARANTINED,
            )
        return assert_rules_applicable(self._engine, self.rule_context)

    # -- single action -----------------------------------------------------

    def act(
        self,
        action: StructuredAction | Mapping[str, Any],
        *,
        token: str | None = None,
    ) -> AgentSnapshot:
        """Validate and dispatch one action, returning the state after it.

        ``token`` is required by default (see :meth:`_token_status` for what it is
        bound to). A copper-changing action is checkpointed and DRC-gated: if the
        engine's own DRC, under the proven rule context, reports a new relevant
        violation, the action is rolled back and reported as ``routing_failed``.
        """
        try:
            structured = coerce_action(action)
        except InvalidActionError as exc:
            with self._lock:
                probe = self._probe(include_geometry=False)
                return self._emit(
                    probe, Outcome.INVALID_ACTION, action=None, evidence=exc.to_evidence(),
                )

        with self._lock:
            probe = self._probe()
            if not probe.verified:
                return self._emit(
                    probe, Outcome.UNVERIFIED, action=structured.name,
                    evidence={"stage": "pre_action_probe"},
                )
            refusal = self._gate_unusable(probe, structured.name)
            if refusal is not None:
                return refusal
            refusal = self._gate_token(token, probe, structured.name)
            if refusal is not None:
                return refusal
            try:
                env_action = self._validator.validate(
                    structured, phase=ActionPhase(route_active=bool(probe.route_active))
                )
            except InvalidActionError as exc:
                outcome = (
                    Outcome.NO_ACTIVE_ROUTE
                    if exc.reason == "wrong_phase" and not probe.route_active
                    else Outcome.INVALID_ACTION
                )
                return self._emit(
                    probe, outcome, action=structured.name, evidence=exc.to_evidence(),
                )

            if structured.name in _COPPER_ACTIONS:
                return self._act_gated(structured, env_action, probe)
            try:
                success, info = self._dispatch(structured, env_action)
            except Exception as exc:  # noqa: BLE001
                return self._emit(
                    self._probe(), Outcome.ROUTING_FAILED, action=structured.name,
                    evidence={"stage": "dispatch", "exception": f"{type(exc).__name__}: {exc}"},
                )
            return self._emit(
                self._probe(), Outcome.OK if success else Outcome.ROUTING_FAILED,
                action=structured.name,
                evidence={"dispatch_success": bool(success), "dispatch_info": dict(info)},
            )

    def _act_gated(
        self, structured: StructuredAction, env_action: Mapping[str, Any], probe: StateProbe
    ) -> AgentSnapshot:
        """Copper-changing single action: rules, checkpoint, DRC acceptance.

        Invariant: once the checkpoint exists, *every* exit path either keeps
        copper the gate accepted or has attempted a restore and verified it. Any
        error the gate cannot classify (a connectivity rebuild failure, an
        unreadable post-probe, an unexpected exception) is treated as
        "state unproven": restore, verify, and quarantine when the restore cannot
        be established. Copper is never left behind by an unverified path.
        """
        try:
            rules_evidence = self._require_rules()
        except RulesUnavailableError as exc:
            return self._emit(
                probe, Outcome.UNSUPPORTED, action=structured.name, evidence=exc.detail,
            )
        rules_path = str(rules_evidence["drc_rules_path"])
        try:
            baseline = self._gate.baseline(rules_path, str(probe.geometry_digest))
        except Exception as exc:  # noqa: BLE001 - no baseline, no acceptance
            return self._emit(
                probe, Outcome.UNSUPPORTED, action=structured.name,
                evidence={
                    "reason": REASON_DRC_UNAVAILABLE,
                    "exception": f"{type(exc).__name__}: {exc}",
                    "rules": rules_evidence,
                },
            )

        engine = self._engine
        try:
            handle = engine.checkpoint()
        except Exception as exc:  # noqa: BLE001 - nothing mutated yet, so this is safe
            return self._emit(
                probe, Outcome.UNSUPPORTED, action=structured.name,
                evidence={
                    "reason": REASON_CHECKPOINT_UNAVAILABLE,
                    "exception": f"{type(exc).__name__}: {exc}",
                },
            )
        try:
            evidence: dict[str, Any] = {"rules": rules_evidence}
            try:
                try:
                    success, info = self._dispatch(structured, env_action)
                except Exception as exc:  # noqa: BLE001 - the engine may have mutated
                    success, info = False, {"exception": f"{type(exc).__name__}: {exc}"}
                if structured.name in ("start_route", "make_line", "make_via", "finish"):
                    engine.build_connectivity()
                after = self._probe()
                if not after.verified:
                    raise _UnverifiedState(f"post-action probe failed: {after.error}")
            except Exception as exc:  # noqa: BLE001 - restore, never leave copper behind
                evidence["stage"] = "post_action_state"
                evidence["exception"] = f"{type(exc).__name__}: {exc}"
                return self._rollback_action(
                    structured.name, handle, probe, evidence,
                    Outcome.UNVERIFIED, REASON_UNVERIFIED_STATE,
                )

            evidence["dispatch_success"] = bool(success)
            evidence["dispatch_info"] = dict(info)

            try:
                delta = self._gate.verify(baseline, rules_path, str(after.geometry_digest))
            except Exception as exc:  # noqa: BLE001 - no acceptance, no keep
                evidence["exception"] = f"{type(exc).__name__}: {exc}"
                return self._rollback_action(
                    structured.name, handle, probe, evidence,
                    Outcome.UNSUPPORTED, REASON_DRC_UNAVAILABLE,
                )

            if not delta.acceptable:
                evidence["reason"] = REASON_DRC_REGRESSION
                evidence["drc_delta"] = delta.to_evidence()
                return self._rollback_action(
                    structured.name, handle, probe, evidence,
                    Outcome.ROUTING_FAILED, REASON_DRC_REGRESSION,
                )

            evidence["drc_delta"] = delta.to_evidence()
            return self._emit(
                after, Outcome.OK if success else Outcome.ROUTING_FAILED,
                action=structured.name, evidence=evidence,
            )
        except Exception as exc:  # noqa: BLE001 - last-resort guard for this call
            evidence = {
                "stage": "unexpected",
                "exception": f"{type(exc).__name__}: {exc}",
                "rules": rules_evidence,
            }
            return self._rollback_action(
                structured.name, handle, probe, evidence,
                Outcome.UNSUPPORTED, REASON_UNVERIFIED_STATE,
            )
        finally:
            self._release(handle)

    def _rollback_action(
        self,
        action_name: str,
        handle: int,
        expected: StateProbe,
        evidence: dict[str, Any],
        outcome: Outcome,
        reason: str,
    ) -> AgentSnapshot:
        """Restore one action's checkpoint, verify, and quarantine if it cannot be."""
        evidence.setdefault("reason", reason)
        # A failed path invalidates every cached DRC judgement: the copper the
        # cache described may no longer exist.
        self._gate.invalidate()
        restored, rollback = self._restore_and_verify(handle, expected)
        evidence["rollback"] = rollback
        if not restored:
            evidence["copper_state"] = COPPER_RETAINED_UNKNOWN
            self.quarantine(
                f"{action_name} could not be verified and the rollback to the "
                "pre-action state could not be established"
            )
            outcome = Outcome.UNSUPPORTED
        else:
            evidence["copper_state"] = COPPER_RESTORED
        return self._emit(self._probe(), outcome, action=action_name, evidence=evidence)

    def _dispatch(
        self, structured: StructuredAction, env_action: Mapping[str, Any]
    ) -> tuple[bool, Mapping[str, Any]]:
        """Call the same core action functions the environment dispatches to."""
        name = structured.name
        engine = self._engine
        if name == "net_select":
            return core_action.net_select(engine, int(env_action["net_id"]))
        if name == "start_route":
            return core_action.start_route(
                engine, env_action["x_mm"], env_action["y_mm"], int(env_action["layer"])
            )
        if name == "net_end":
            current = int(engine.get_current_net_code())
            return core_action.net_end(engine, current if current > 0 else 0)
        if name == "make_line":
            return core_action.make_line(
                engine, env_action["x_mm"], env_action["y_mm"], int(env_action["routing_mode"])
            )
        if name == "make_via":
            return core_action.make_via(
                engine, env_action["x_mm"], env_action["y_mm"], int(env_action["routing_mode"])
            )
        if name == "finish":
            return core_action.finish(engine, int(env_action["routing_mode"]))
        raise InvalidActionError("unknown_action", f"cannot dispatch {name!r}")

    # -- endpoints ---------------------------------------------------------

    def cluster(self, x_mm: float, y_mm: float, layer: int) -> frozenset[tuple[float, float, int]]:
        """Anchor set of the copper cluster under a point (empty when none)."""
        points = self._engine.get_connected_points(float(x_mm), float(y_mm), int(layer))
        return anchor_set(points)

    def board_digest(self) -> str | None:
        """Geometry digest of the committed board, or ``None`` when unreadable.

        A read-only convenience for callers that need to tell one board state
        from another (the runner files failed attempts against it) without
        minting a token or emitting a snapshot.
        """
        with self._lock:
            probe = self._probe(include_geometry=True)
            return probe.geometry_digest if probe.verified else None

    def endpoint(self, x_mm: float, y_mm: float, layer: int) -> Endpoint:
        """Authoritative identity of one endpoint, or a refusal.

        The cluster comes from the engine's own connectivity query, so the layer
        question is answered by the engine rather than by guessing what an item's
        ``layer`` integer means. The net comes from the pads inside that cluster:

        * no copper at the point -> ``endpoint_unknown``;
        * copper but no pad in the cluster (a bare track/via island) ->
          ``endpoint_unknown``, because the net cannot be established;
        * pads with different net codes in one cluster -> ``endpoint_ambiguous``.

        A rotated pad is not a problem for this test: membership is decided by the
        cluster's own anchors, not by an axis-aligned box.

        Membership *is* layer-aware: the cluster anchor carries the human layer
        the engine reported, and a pad only counts when it sits on that layer (or
        spans copper, reported as a negative layer). Matching on X/Y alone would
        credit a pad on another layer - and therefore another net - to this
        cluster, which is how unrelated nets used to collapse into one endpoint.
        """
        cluster = self.cluster(x_mm, y_mm, layer)
        if not cluster:
            raise InvalidActionError(
                REASON_ENDPOINT_UNKNOWN,
                f"no copper at ({x_mm}, {y_mm}) on layer {layer}",
                x_mm=float(x_mm), y_mm=float(y_mm), layer=int(layer), cluster_size=0,
            )
        anchors: dict[tuple[float, float], set[int]] = {}
        for anchor in cluster:
            anchors.setdefault((anchor[0], anchor[1]), set()).add(int(anchor[2]))
        nets: set[int] = set()
        pad_names: list[str] = []
        has_via = False
        for pad in self._engine.get_pads():
            key = (round(float(pad.x_mm), 4), round(float(pad.y_mm), 4))
            layers = anchors.get(key)
            if layers is None:
                continue
            # ``PadInfo.layer`` is a PCB_LAYER_ID while the cluster's anchors are
            # human layers; a negative value is the engine's spans-copper
            # convention (a through-hole pad exists on every layer it crosses).
            pad_layer = self._human_pad_layer(int(pad.layer))
            if pad_layer is not None and pad_layer not in layers:
                continue
            code = int(pad.net_code)
            if code > 0:
                nets.add(code)
            pad_names.append(f"{pad.footprint_ref}.{pad.pad_name}")
        via_map = getattr(self._engine, "layer_map", None)
        for via in self._engine.get_vias():
            key = (round(float(via.x_mm), 4), round(float(via.y_mm), 4))
            anchor_layers = anchors.get(key)
            if anchor_layers is None:
                continue
            try:
                if via_map is None:
                    raise KeyError("engine layer map unavailable")
                span = {
                    int(via_map.board_to_human(int(via.top_layer))),
                    int(via_map.board_to_human(int(via.bottom_layer))),
                }
                span.update(range(min(span), max(span) + 1))
            except Exception as exc:  # noqa: BLE001 - unknown spans fail closed
                raise InvalidActionError(
                    REASON_ENDPOINT_UNKNOWN,
                    f"cannot resolve via layer span at ({via.x_mm}, {via.y_mm}): "
                    f"{type(exc).__name__}",
                    x_mm=float(x_mm), y_mm=float(y_mm), layer=int(layer),
                ) from None
            if not (anchor_layers & span):
                continue
            has_via = True
            code = int(via.net_code)
            if code > 0:
                nets.add(code)

        if not nets:
            raise InvalidActionError(
                REASON_ENDPOINT_UNKNOWN,
                f"copper at ({x_mm}, {y_mm}) layer {layer} carries no resolvable net "
                "(no pad or via in its cluster)",
                x_mm=float(x_mm), y_mm=float(y_mm), layer=int(layer),
                cluster_size=len(cluster), pads=pad_names,
            )
        if len(nets) > 1:
            raise InvalidActionError(
                REASON_ENDPOINT_AMBIGUOUS,
                f"copper at ({x_mm}, {y_mm}) layer {layer} joins nets {sorted(nets)}",
                x_mm=float(x_mm), y_mm=float(y_mm), layer=int(layer), net_codes=sorted(nets),
            )
        return Endpoint(
            point=(float(x_mm), float(y_mm), int(layer)),
            net_code=next(iter(nets)),
            cluster=cluster,
            pads=tuple(sorted(pad_names)),
            has_via=has_via,
        )

    def _human_pad_layer(self, pad_layer: int) -> int | None:
        """Human layer for a pad's ``PCB_LAYER_ID``; ``None`` when it spans copper."""
        if int(pad_layer) < 0:
            return None
        layer_map = getattr(self._engine, "layer_map", None)
        if layer_map is None:
            return int(pad_layer)
        try:
            return int(layer_map.board_to_human(int(pad_layer)))
        except KeyError:
            # Already a human layer number (an engine that reports human layers).
            return int(pad_layer)

    def net_at(self, x_mm: float, y_mm: float, layer: int) -> int:
        """Net code at a point, or 0 when it cannot be established.

        Conservative wrapper over :meth:`endpoint`; a caller that needs the
        refusal reason should use :meth:`endpoint` directly.
        """
        try:
            return self.endpoint(x_mm, y_mm, layer).net_code
        except InvalidActionError:
            return 0

    def _connectivity(
        self,
        start_pt: tuple[float, float, int],
        target_pt: tuple[float, float, int],
    ) -> tuple[bool, frozenset[tuple[float, float, int]]]:
        """Are the two copper groups one? Raises if the engine cannot be read."""
        self._engine.build_connectivity()
        shared = self.cluster(*start_pt) & self.cluster(*target_pt)
        return bool(shared), shared

    # -- checkpoints -------------------------------------------------------

    def _release(self, handle: int) -> None:
        try:
            self._engine.release_checkpoint(handle)
        except Exception:  # noqa: BLE001 - the handle is already unusable
            pass

    def _restore_and_verify(
        self, handle: int, expected: StateProbe
    ) -> tuple[bool, dict[str, Any]]:
        """Restore a checkpoint and prove the copper *and* session came back.

        Session comparison covers every field the engine reports for an idle or
        active router: ``route_active``, head, **target**, current net and
        **current layer**. The engine normalises them to its idle sentinels
        (``head``/``target`` ``(0, 0, -1)``, layer ``-1``, net ``-1``) when no
        route is open, and this probe converts those to ``None`` before comparing,
        so an idle-to-idle restore compares equal without pretending the sentinel
        is real state.

        ``routing_target`` is **advisory**: it is a router hint that the engine
        recomputes on world resync and does not round-trip through
        checkpoint/restore (measured on the pinned engine). It is compared and
        reported (`target_matches` / `target_advisory_mismatch`) but a difference
        there does not fail the rollback as long as the copper, route-active flag,
        head, net and layer all match — those are the fields that describe real
        state.
        """
        try:
            restored = bool(self._engine.restore(handle))
        except Exception as exc:  # noqa: BLE001
            return False, {
                "restore_exception": f"{type(exc).__name__}: {exc}",
                "expected_digest": expected.geometry_digest,
            }
        if not restored:
            return False, {"restored": False, "expected_digest": expected.geometry_digest}
        after = self._probe()
        if not after.verified:
            return False, {
                "restored": True,
                "probe_error": after.error,
                "unverifiable": list(unverifiable_properties()),
            }
        copper_ok = after.geometry_digest == expected.geometry_digest
        authoritative_ok = (
            after.route_active == expected.route_active
            and after.head == expected.head
            and after.current_net_code == expected.current_net_code
            and after.layer == expected.layer
        )
        target_matches = after.target == expected.target
        detail = {
            "restored": True,
            "copper_digest_matches": copper_ok,
            "session_state_matches": authoritative_ok,
            "target_matches": target_matches,
            "target_advisory": not target_matches,
            "target_advisory_note": (
                "routing_target is an advisory router hint the engine recomputes on "
                "resync; copper, route-active, head, net and layer are the verified "
                "fields"
            ),
            "expected_digest": expected.geometry_digest,
            "actual_digest": after.geometry_digest,
            "expected_state": {
                "route_active": expected.route_active,
                "head": list(expected.head) if expected.head else None,
                "target": list(expected.target) if expected.target else None,
                "net_code": expected.current_net_code,
                "layer": expected.layer,
            },
            "actual_state": {
                "route_active": after.route_active,
                "head": list(after.head) if after.head else None,
                "target": list(after.target) if after.target else None,
                "net_code": after.current_net_code,
                "layer": after.layer,
            },
            "expected_head": list(expected.head) if expected.head else None,
            "actual_head": list(after.head) if after.head else None,
            "expected_target": list(expected.target) if expected.target else None,
            "actual_target": list(after.target) if after.target else None,
            "expected_layer": expected.layer,
            "actual_layer": after.layer,
            "unverifiable": list(unverifiable_properties()),
        }
        return (copper_ok and authoritative_ok), detail

    # -- transactions ------------------------------------------------------

    def connect_targets(
        self,
        start: Sequence[float],
        target: Sequence[float],
        mode: str | int = "walkaround",
        *,
        waypoints: Iterable[Sequence[float]] = (),
        token: str | None = None,
        atomic: bool = True,
        provisional: bool = False,
    ) -> ConnectionResult:
        """Connect ``start`` to ``target`` as one transaction.

        ``waypoints`` are intermediate points; each may be ``(x, y)`` or
        ``(x, y, layer)``, and a layer that differs from the head's current layer
        inserts a via + layer switch there.

        Atomic by default: unless the connection is verified **and** the copper
        passes native DRC acceptance, everything this call did is rolled back.
        ``provisional=True`` keeps verified progress when the connection does not
        close — an explicit choice, never the default, and never a way to keep
        copper that fails DRC acceptance.

        ``atomic=False`` without ``provisional=True`` is refused: it would be a
        request to leave copper behind without saying so.
        """
        if not atomic and not provisional:
            with self._lock:
                probe = self._probe(include_geometry=False)
                return self._fail(
                    Outcome.INVALID_ACTION, _point3(start), _point3(target), mode, probe,
                    {
                        "reason": REASON_UNDERSPECIFIED,
                        "message": (
                            "atomic=False requires provisional=True: partial copper "
                            "must be an explicit choice"
                        ),
                    },
                )
        with self._lock:
            return self._connect_locked(
                start, target, mode, waypoints=waypoints, token=token,
                atomic=atomic, provisional=provisional,
            )

    def _connect_locked(
        self,
        start: Sequence[float],
        target: Sequence[float],
        mode: str | int,
        *,
        waypoints: Iterable[Sequence[float]] = (),
        token: str | None = None,
        atomic: bool = True,
        provisional: bool = False,
        internal: bool = False,
    ) -> ConnectionResult:
        if waypoints is None:
            waypoints = ()
        elif not isinstance(waypoints, (tuple, list)):
            probe = self._probe(include_geometry=False)
            return self._fail(
                Outcome.INVALID_ACTION, _point3(start), _point3(target), mode, probe,
                {
                    "reason": "malformed_coordinate",
                    "message": (
                        "waypoints must be a sequence of (x, y) or (x, y, layer) points, "
                        f"got {type(waypoints).__name__}"
                    ),
                },
            )
        start3 = _point3(start)
        target3 = _point3(target)
        probe = self._probe()
        if not probe.verified:
            return self._fail(
                Outcome.UNVERIFIED, start3, target3, mode, probe,
                {"stage": "pre_transaction_probe"}, action="connect_targets",
            )
        if not internal:
            blocked = self._gate_unusable(probe, "connect_targets")
            if blocked is not None:
                return self._fail(
                    Outcome.UNSUPPORTED, start3, target3, mode, probe,
                    {"reason": REASON_SESSION_QUARANTINED, "detail": self._dirty_reason},
                )
            refusal = self._gate_token(token, probe, "connect_targets")
            if refusal is not None:
                status = self._token_status(token, probe)
                outcome = (
                    Outcome.INVALID_ACTION if status in ("missing", "malformed")
                    else Outcome.STALE_STATE
                )
                return self._fail(
                    outcome, start3, target3, mode, probe, refusal.evidence,
                )

        try:
            start_pt, target_pt, plan = self._build_plan(start, target, mode, waypoints)
        except InvalidActionError as exc:
            return self._fail(
                Outcome.INVALID_ACTION, start3, target3, mode, probe, exc.to_evidence(),
            )

        try:
            rules_evidence = self._require_rules()
        except RulesUnavailableError as exc:
            return self._fail(
                Outcome.UNSUPPORTED, start_pt, target_pt, mode, probe, exc.detail,
            )
        rules_path = str(rules_evidence["drc_rules_path"])

        # Endpoint identity before any mutation: unknown or ambiguous copper is a
        # refusal, not a guess.
        try:
            self._engine.build_connectivity()
            start_end = self.endpoint(*start_pt)
            target_end = self.endpoint(*target_pt)
        except InvalidActionError as exc:
            return self._fail(
                Outcome.INVALID_ACTION, start_pt, target_pt, mode, probe, exc.to_evidence(),
            )
        except Exception as exc:  # noqa: BLE001
            return self._fail(
                Outcome.UNVERIFIED, start_pt, target_pt, mode, self._probe(),
                {"stage": "endpoint_probe", "exception": f"{type(exc).__name__}: {exc}"},
            )
        if start_end.net_code != target_end.net_code:
            return self._fail(
                Outcome.INVALID_ACTION, start_pt, target_pt, mode, probe,
                {
                    "reason": REASON_ENDPOINT_AMBIGUOUS,
                    "start": start_end.to_dict(), "target": target_end.to_dict(),
                    "message": "start and target resolve to different nets",
                },
            )
        if start_end.cluster & target_end.cluster:
            evidence = {
                "stage": "precondition",
                "start": start_end.to_dict(), "target": target_end.to_dict(),
                "shared_anchors": [list(a) for a in sorted(start_end.cluster & target_end.cluster)],
                "rules": rules_evidence,
            }
            return self._fail(
                Outcome.ALREADY_CONNECTED, start_pt, target_pt, mode, probe, evidence,
                connected=True,
            )

        # Transaction entry, and the first point where the route's *own* net is
        # known: hand the router that net's effective via size before any copper
        # is placed, and compare it with the size already in force. A setter
        # failure quarantines the session inside the helper, so the refusal is
        # reported rather than routed around.
        via_size = self._via_size_for_net(start_end.net_code)
        if self._dirty:
            return self._fail(
                Outcome.UNSUPPORTED, start_pt, target_pt, mode, probe,
                {
                    "reason": REASON_SESSION_QUARANTINED,
                    "detail": self._dirty_reason,
                    "via_size": via_size,
                },
            )
        if any(kind == "via" for kind, _point, _layer in plan) and not via_size.get(
            "applied"
        ):
            # This plan would place a via and the size it would be placed with is
            # not a size this session can call lawful. Refusing here is the only
            # honest option: routing on would use whatever the router happens to
            # have in force, which is exactly the stale setting this guards against.
            return self._fail(
                Outcome.UNSUPPORTED, start_pt, target_pt, mode, probe,
                {
                    "reason": "via_size_unusable",
                    "message": (
                        "this plan needs a via and no lawful via size could be "
                        "established for its net"
                    ),
                    "via_size": via_size,
                    "rules": rules_evidence,
                },
            )

        try:
            baseline = self._gate.baseline(rules_path, str(probe.geometry_digest))
        except Exception as exc:  # noqa: BLE001
            return self._fail(
                Outcome.UNSUPPORTED, start_pt, target_pt, mode, probe,
                {
                    "reason": REASON_DRC_UNAVAILABLE,
                    "exception": f"{type(exc).__name__}: {exc}",
                    "rules": rules_evidence,
                },
            )

        return self._execute_plan(
            start_pt, target_pt, mode, plan, probe=probe,
            atomic=atomic, provisional=provisional,
            rules_evidence=rules_evidence, rules_path=rules_path, baseline=baseline,
            start_end=start_end, target_end=target_end,
            via_size=via_size,
        )

    def _fail(
        self,
        outcome: Outcome,
        start: tuple[float, float, int],
        target: tuple[float, float, int],
        mode: str | int,
        probe: StateProbe,
        evidence: Mapping[str, Any],
        *,
        connected: bool = False,
        action: str = "connect_targets",
    ) -> ConnectionResult:
        snap = self._emit(probe, outcome, action=action, evidence=evidence)
        return ConnectionResult(
            outcome=outcome, committed=False, connected=connected,
            start=start, target=target, mode=str(mode), steps=(), snapshot=snap,
            evidence=dict(evidence), dirty=self._dirty, copper_state=COPPER_UNCHANGED,
        )

    # -- plan construction -------------------------------------------------

    def _build_plan(
        self,
        start: Sequence[float],
        target: Sequence[float],
        mode: str | int,
        waypoints: Iterable[Sequence[float]],
    ) -> tuple[
        tuple[float, float, int],
        tuple[float, float, int],
        list[tuple[str, tuple[float, float], int | None]],
    ]:
        if not isinstance(start, (tuple, list)) or not isinstance(target, (tuple, list)):
            raise InvalidActionError(
                "malformed_coordinate",
                "start and target must each be (x, y) or (x, y, layer)",
            )
        mode_int = mode_to_int(mode)
        if self._engine.is_routing():
            raise InvalidActionError(
                "wrong_phase",
                "connect_targets starts its own route; finish or cancel the active one first",
                route_active=True,
            )
        start_pt = self._point(start, "start", default_layer=1)
        target_pt = self._point(target, "target", default_layer=start_pt[2])

        steps: list[tuple[str, tuple[float, float], int | None]] = [
            ("start", (start_pt[0], start_pt[1]), start_pt[2])
        ]
        current_layer = start_pt[2]
        for index, raw in enumerate(waypoints):
            point = self._point(raw, f"waypoint[{index}]", default_layer=current_layer)
            if point[2] != current_layer:
                # ``make_via`` commits the line it routed and *finishes* the
                # session (``fix_route(force_finish=True)``), so the plan has to
                # re-open the route on the new layer before it can lay any more
                # copper. Without this step every layer-changing waypoint ended
                # at the trailing line with the session idle.
                steps.append(("via", (point[0], point[1]), None))
                steps.append(("switch", (point[0], point[1]), point[2]))
                steps.append(("restart", (point[0], point[1]), point[2]))
                current_layer = point[2]
            steps.append(("line", (point[0], point[1]), None))
        if target_pt[2] != current_layer:
            steps.append(("via", (target_pt[0], target_pt[1]), None))
            steps.append(("switch", (target_pt[0], target_pt[1]), target_pt[2]))
            steps.append(("restart", (target_pt[0], target_pt[1]), target_pt[2]))
        steps.append(("line", (target_pt[0], target_pt[1]), None))

        phase = ActionPhase(route_active=False)
        for kind, (x_mm, y_mm), layer in steps:
            if kind == "start":
                act = StructuredAction.start_route(x_mm, y_mm, layer or 1)
            elif kind == "restart":
                act = StructuredAction.start_route(x_mm, y_mm, layer or 1)
            elif kind == "line":
                act = StructuredAction.make_line(x_mm, y_mm, mode_int)
            elif kind == "via":
                act = StructuredAction.make_via(x_mm, y_mm, mode_int)
            else:
                continue
            self._validator.validate(act, phase=phase)
            if kind in ("start", "restart"):
                phase = ActionPhase(route_active=True)
            elif kind == "via":
                # A via step finishes the route it routed, so the actions after
                # it are validated against an idle session - which is what the
                # executor sees too.
                phase = ActionPhase(route_active=False)
        return start_pt, target_pt, steps

    def _point(
        self, raw: Sequence[float], label: str, *, default_layer: int
    ) -> tuple[float, float, int]:
        if not isinstance(raw, (tuple, list)):
            raise InvalidActionError(
                "malformed_coordinate",
                f"{label} must be (x, y) or (x, y, layer), got {type(raw).__name__}",
                field=label,
            )
        if len(raw) not in (2, 3):
            raise InvalidActionError(
                "malformed_coordinate",
                f"{label} must be (x, y) or (x, y, layer), got {len(raw)} value(s)",
                field=label,
            )
        x_mm = self._validator.coord(raw[0], field_name=f"{label}.x_mm")
        y_mm = self._validator.coord(raw[1], field_name=f"{label}.y_mm")
        self._validator.bounds(x_mm, y_mm)
        layer = (
            self._validator.layer(raw[2]) if len(raw) == 3
            else self._validator.layer(default_layer)
        )
        return (x_mm, y_mm, layer)

    # -- execution ---------------------------------------------------------

    def _execute_plan(
        self,
        start_pt: tuple[float, float, int],
        target_pt: tuple[float, float, int],
        mode: str | int,
        plan: Sequence[tuple[str, tuple[float, float], int | None]],
        *,
        probe: StateProbe,
        atomic: bool,
        provisional: bool,
        rules_evidence: Mapping[str, Any],
        rules_path: str,
        baseline: ViolationSet,
        start_end: Endpoint,
        target_end: Endpoint,
        via_size: Mapping[str, Any] | None = None,
    ) -> ConnectionResult:
        engine = self._engine
        mode_int = mode_to_int(mode)
        evidence: dict[str, Any] = {
            "rules": rules_evidence,
            #: The via size the router was told to use for *this* net (declared and
            #: adopted values, and where they came from) - a routing decision, not
            #: a rule change, and part of the transaction's evidence.
            "via_size": dict(via_size or {}),
            "start": start_end.to_dict(),
            "target": target_end.to_dict(),
            "plan": [
                {"kind": kind, "point": list(point), "layer": layer}
                for kind, point, layer in plan
            ],
            "pre_transaction": {
                "digest": probe.geometry_digest,
                "track_count": probe.track_count,
                "via_count": probe.via_count,
            },
            "atomic": bool(atomic and not provisional),
        }
        rows_before = probe.rows
        try:
            transaction_ckpt = engine.checkpoint()
        except Exception as exc:  # noqa: BLE001 - nothing mutated yet
            return self._finish(
                start_pt, target_pt, mode, (), probe,
                {**evidence, "reason": REASON_CHECKPOINT_UNAVAILABLE,
                 "exception": f"{type(exc).__name__}: {exc}"},
                outcome=Outcome.UNSUPPORTED, committed=False, connected=False,
                accepted=False, rollback_verified=None, copper_state=COPPER_UNCHANGED,
            )
        steps: list[TransactionStep] = []
        failed = False
        rollback_verified: bool | None = None
        rollback_failed = False
        connectivity_error = False

        try:
            live_ckpt: int | None = None
            try:
                for kind, point, layer in plan:
                    current_layer = self._current_layer()
                    if kind == "switch" and (current_layer is None or current_layer == layer):
                        steps.append(TransactionStep(
                            kind=kind, point=point, layer=layer, success=True,
                            restored=False, head_after=self._head(),
                            detail={
                                "already_on_layer": current_layer == layer,
                                "route_inactive": current_layer is None,
                            },
                        ))
                        continue

                    if kind == "line" and self._head_matches(point, current_layer):
                        # The head already sits on the requested point and layer:
                        # e.g. a pair whose two anchors coincide across layers was
                        # bridged by the via step, so the trailing zero-length move
                        # is redundant (and PNS rejects it as a stuck route).
                        steps.append(TransactionStep(
                            kind=kind, point=point, layer=layer, success=True,
                            restored=False, head_after=self._head(),
                            detail={"already_at_point": True},
                        ))
                        continue

                    step_probe = self._probe()
                    try:
                        ckpt = engine.checkpoint()
                    except Exception as exc:  # noqa: BLE001 - stop before mutating
                        evidence["reason"] = REASON_CHECKPOINT_UNAVAILABLE
                        evidence["exception"] = f"{type(exc).__name__}: {exc}"
                        failed = True
                        break
                    if live_ckpt is not None:
                        self._release(live_ckpt)
                    live_ckpt = ckpt
                    try:
                        ok, detail = self._run_step(kind, point, layer, mode_int)
                    except Exception as exc:  # noqa: BLE001
                        ok, detail = False, {"exception": f"{type(exc).__name__}: {exc}"}

                    if not ok:
                        restored, rollback = self._restore_and_verify(ckpt, step_probe)
                        if not restored:
                            rollback_failed = True
                        rollback_verified = restored
                        self._release(ckpt)
                        live_ckpt = None
                        steps.append(TransactionStep(
                            kind=kind, point=point, layer=layer, success=False,
                            restored=restored, head_after=self._head(),
                            detail={**detail, "rollback": rollback},
                        ))
                        failed = True
                        break

                    steps.append(TransactionStep(
                        kind=kind, point=point, layer=layer, success=True,
                        restored=False, head_after=self._head(), detail=detail,
                    ))
                    try:
                        reached, _shared = self._connectivity(start_pt, target_pt)
                    except Exception as exc:  # noqa: BLE001
                        evidence["connectivity_probe_error"] = f"{type(exc).__name__}: {exc}"
                        connectivity_error = True
                        break
                    if reached:
                        evidence["connection_reached_at_step"] = kind
                        break
            finally:
                # Every step handle is released on every path, including one that
                # leaves the loop by exception.
                if live_ckpt is not None:
                    self._release(live_ckpt)
                    live_ckpt = None

            # What did the attempt actually change? (Net-attributed, so a shove
            # that displaced foreign copper is visible even after a restore.)
            rows_probe = self._probe()
            if not rows_probe.verified:
                return self._unreadable_transaction(
                    start_pt, target_pt, mode, steps, transaction_ckpt, probe, evidence,
                    stage="post_step_state", detail=rows_probe.error,
                )
            rows_after = rows_probe.rows
            evidence["changed_nets"] = list(nets_changed(rows_before, rows_after))
            length_before, tracks_before, vias_before = copper_totals(rows_before)
            length_after, tracks_after, vias_after = copper_totals(rows_after)
            # Quality metrics for the attempt, computed once here so callers
            # (scheduler ranking, reports) do not re-derive them from raw rows.
            evidence["added_length_mm"] = round(max(0.0, length_after - length_before), 4)
            evidence["vias_added"] = max(0, vias_after - vias_before)
            evidence["tracks_added"] = max(0, tracks_after - tracks_before)
            evidence["steps_count"] = len(steps)

            # A rollback the backend could not perform means copper may remain
            # that this session believes it removed. Stop, quarantine, and say so.
            if rollback_failed:
                evidence["reason"] = "rollback_failed"
                for step in reversed(steps):
                    if step.restored is False and not step.success:
                        evidence.setdefault("rollback", dict(step.detail).get("rollback"))
                        break
                evidence["copper_state"] = COPPER_RETAINED_UNKNOWN
                self.quarantine(
                    "a rollback could not be verified: the board may hold copper this "
                    "session did not intend to keep"
                )
                return self._finish(
                    start_pt, target_pt, mode, steps, self._probe(), evidence,
                    outcome=Outcome.UNSUPPORTED, committed=None, connected=False,
                    accepted=False, rollback_verified=rollback_verified,
                    copper_state=COPPER_RETAINED_UNKNOWN,
                )

            connected, shared = False, frozenset()
            if not connectivity_error:
                try:
                    connected, shared = self._connectivity(start_pt, target_pt)
                except Exception as exc:  # noqa: BLE001
                    evidence["connectivity_probe_error"] = f"{type(exc).__name__}: {exc}"
                    connectivity_error = True
            evidence["shared_anchors"] = [list(a) for a in sorted(shared)]

            # A connectivity result we could not read is never usable progress,
            # not even in provisional mode: undo and report unverified.
            if connectivity_error:
                evidence["reason"] = REASON_UNVERIFIED_STATE
                restored, rollback = self._restore_and_verify(transaction_ckpt, probe)
                evidence["rollback"] = rollback
                if not restored:
                    rollback_failed = True
                    evidence["copper_state"] = COPPER_RETAINED_UNKNOWN
                    self.quarantine(
                        "connectivity could not be read and the rollback could not be verified"
                    )
                else:
                    evidence["copper_state"] = COPPER_RESTORED
                connected, shared = self._connectivity_after_restore(start_pt, target_pt, evidence)
                evidence["shared_anchors"] = [list(a) for a in sorted(shared)]
                return self._finish(
                    start_pt, target_pt, mode, steps, self._probe(), evidence,
                    outcome=Outcome.UNSUPPORTED if rollback_failed else Outcome.UNVERIFIED,
                    committed=None if rollback_failed else False, connected=connected,
                    accepted=False, rollback_verified=restored if not rollback_failed else False,
                    copper_state=COPPER_RETAINED_UNKNOWN if rollback_failed else COPPER_RESTORED,
                )

            # Atomic behaviour: keep nothing unless the connection closed.
            if atomic and not provisional and not connected and not rollback_failed:
                restored, rollback = self._restore_and_verify(transaction_ckpt, probe)
                if restored:
                    self._gate.seed(baseline, str(probe.geometry_digest))
                evidence["rollback"] = rollback
                evidence["reason"] = "connection_not_verified"
                if not restored:
                    rollback_failed = True
                    evidence["copper_state"] = COPPER_RETAINED_UNKNOWN
                else:
                    evidence["copper_state"] = COPPER_RESTORED
                rollback_verified = restored
                connected, shared = self._connectivity_after_restore(start_pt, target_pt, evidence)
                evidence["shared_anchors"] = [list(a) for a in sorted(shared)]
                final_probe = self._probe()
                if rollback_failed:
                    self.quarantine(
                        "connection attempt was rolled back but the restore could not be verified"
                    )
                return self._finish(
                    start_pt, target_pt, mode, steps, final_probe, evidence,
                    outcome=(Outcome.UNSUPPORTED if rollback_failed else Outcome.ROUTING_FAILED),
                    committed=None if rollback_failed else False,
                    connected=connected, accepted=False,
                    rollback_verified=rollback_verified,
                    copper_state=COPPER_RETAINED_UNKNOWN if rollback_failed else COPPER_RESTORED,
                )

            # DRC acceptance on whatever state we are about to keep.
            keep_probe = self._probe()
            if not keep_probe.verified:
                return self._unreadable_transaction(
                    start_pt, target_pt, mode, steps, transaction_ckpt, probe, evidence,
                    stage="pre_acceptance_state", detail=keep_probe.error,
                )
            try:
                delta = self._gate.verify(baseline, rules_path, str(keep_probe.geometry_digest))
            except Exception as exc:  # noqa: BLE001
                evidence["reason"] = REASON_DRC_UNAVAILABLE
                evidence["exception"] = f"{type(exc).__name__}: {exc}"
                restored, rollback = self._restore_and_verify(transaction_ckpt, probe)
                evidence["rollback"] = rollback
                if not restored:
                    rollback_failed = True
                    evidence["copper_state"] = COPPER_RETAINED_UNKNOWN
                    self.quarantine("could not verify the restore after a DRC failure")
                else:
                    evidence["copper_state"] = COPPER_RESTORED
                    self._gate.seed(baseline, str(probe.geometry_digest))
                connected, shared = self._connectivity_after_restore(start_pt, target_pt, evidence)
                evidence["shared_anchors"] = [list(a) for a in sorted(shared)]
                return self._finish(
                    start_pt, target_pt, mode, steps, self._probe(), evidence,
                    outcome=Outcome.UNSUPPORTED,
                    committed=None if rollback_failed else False, connected=connected,
                    accepted=False, rollback_verified=restored or False,
                    copper_state=COPPER_RETAINED_UNKNOWN if rollback_failed else COPPER_RESTORED,
                )

            evidence["drc_delta"] = delta.to_evidence()
            if not delta.acceptable:
                evidence["reason"] = REASON_DRC_REGRESSION
                # The plan *did* close the connection; the gate refused the copper
                # it closed it with. Recorded before the rollback, because after it
                # the board no longer shows the connection at all.
                evidence["connected_before_refusal"] = bool(connected)
                restored, rollback = self._restore_and_verify(transaction_ckpt, probe)
                evidence["rollback"] = rollback
                if not restored:
                    rollback_failed = True
                    evidence["copper_state"] = COPPER_RETAINED_UNKNOWN
                    self.quarantine("routed copper added DRC violations and the rollback "
                                    "could not be verified")
                else:
                    evidence["copper_state"] = COPPER_RESTORED
                    self._gate.seed(baseline, str(probe.geometry_digest))
                connected, shared = self._connectivity_after_restore(start_pt, target_pt, evidence)
                evidence["shared_anchors"] = [list(a) for a in sorted(shared)]
                return self._finish(
                    start_pt, target_pt, mode, steps, self._probe(), evidence,
                    outcome=Outcome.UNSUPPORTED if rollback_failed else Outcome.ROUTING_FAILED,
                    committed=None if rollback_failed else False, connected=connected,
                    accepted=False,
                    rollback_verified=restored if not rollback_failed else False,
                    copper_state=COPPER_RETAINED_UNKNOWN if rollback_failed else COPPER_RESTORED,
                )

            accepted = bool(connected) and delta.acceptable
            committed = accepted or bool(
                provisional and evidence.get("changed_nets") and delta.acceptable
            )
            # Nothing is accepted until the final readable state has been
            # established: a copper-keeping result must never rest on a read that
            # failed. (``_finish`` enforces the same invariant centrally.)
            final_probe = self._probe()
            if not final_probe.verified:
                return self._unreadable_transaction(
                    start_pt, target_pt, mode, steps, transaction_ckpt, probe, evidence,
                    stage="final_state", detail=final_probe.error,
                )
            return self._finish(
                start_pt, target_pt, mode, steps, final_probe, evidence,
                outcome=Outcome.OK if accepted else Outcome.ROUTING_FAILED,
                committed=committed,
                connected=connected, accepted=accepted,
                rollback_verified=rollback_verified,
                copper_state=COPPER_KEPT if committed else COPPER_UNCHANGED,
            )
        except Exception as exc:  # noqa: BLE001 - no unverified copper may survive
            evidence["stage"] = "transaction"
            evidence["exception"] = f"{type(exc).__name__}: {exc}"
            self._gate.invalidate()
            restored, rollback = self._restore_and_verify(transaction_ckpt, probe)
            evidence["rollback"] = rollback
            if not restored:
                self.quarantine(
                    "the transaction failed unexpectedly and the rollback could not be verified"
                )
                evidence["copper_state"] = COPPER_RETAINED_UNKNOWN
                return self._finish(
                    start_pt, target_pt, mode, steps, self._probe(), evidence,
                    outcome=Outcome.UNSUPPORTED, committed=None, connected=False,
                    accepted=False, rollback_verified=False,
                    copper_state=COPPER_RETAINED_UNKNOWN,
                )
            evidence["copper_state"] = COPPER_RESTORED
            return self._finish(
                start_pt, target_pt, mode, steps, self._probe(), evidence,
                outcome=Outcome.UNVERIFIED, committed=False, connected=False,
                accepted=False, rollback_verified=True,
                copper_state=COPPER_RESTORED,
            )
        finally:
            self._release(transaction_ckpt)

    def _connectivity_after_restore(
        self,
        start_pt: tuple[float, float, int],
        target_pt: tuple[float, float, int],
        evidence: dict[str, Any],
    ) -> tuple[bool, frozenset]:
        """Re-read connectivity from the restored board (never the pre-rollback one)."""
        try:
            return self._connectivity(start_pt, target_pt)
        except Exception as exc:  # noqa: BLE001
            evidence["connectivity_after_restore_error"] = f"{type(exc).__name__}: {exc}"
            return False, frozenset()

    def _unreadable_transaction(
        self,
        start_pt: tuple[float, float, int],
        target_pt: tuple[float, float, int],
        mode: str | int,
        steps: Sequence[TransactionStep],
        transaction_ckpt: int,
        expected: StateProbe,
        evidence: dict[str, Any],
        *,
        stage: str,
        detail: str | None,
    ) -> ConnectionResult:
        """The engine went unreadable mid-transaction: undo, verify, or quarantine.

        An unreadable read is never evidence of success. The attempt is rolled
        back; if the rollback itself cannot be established the session is
        quarantined and the result says ``committed=None`` with
        ``copper_state='retained_unknown'`` rather than claiming nothing was kept.
        """
        evidence["reason"] = REASON_UNVERIFIED_STATE
        evidence["stage"] = stage
        if detail:
            evidence["probe_error"] = detail
        self._gate.invalidate()
        restored, rollback = self._restore_and_verify(transaction_ckpt, expected)
        evidence["rollback"] = rollback
        if restored:
            evidence["copper_state"] = COPPER_RESTORED
            outcome, committed, state = Outcome.UNVERIFIED, False, COPPER_RESTORED
        else:
            evidence["copper_state"] = COPPER_RETAINED_UNKNOWN
            self.quarantine(
                "the engine became unreadable mid-transaction and the rollback could "
                "not be verified"
            )
            outcome, committed, state = Outcome.UNSUPPORTED, None, COPPER_RETAINED_UNKNOWN
        connected, shared = self._connectivity_after_restore(start_pt, target_pt, evidence)
        evidence["shared_anchors"] = [list(a) for a in sorted(shared)]
        return self._finish(
            start_pt, target_pt, mode, steps, self._probe(), evidence,
            outcome=outcome, committed=committed, connected=connected,
            accepted=False, rollback_verified=restored, copper_state=state,
        )

    def _finish(
        self,
        start_pt: tuple[float, float, int],
        target_pt: tuple[float, float, int],
        mode: str | int,
        steps: Sequence[TransactionStep],
        final_probe: StateProbe,
        evidence: Mapping[str, Any],
        *,
        outcome: Outcome,
        committed: bool | None,
        connected: bool,
        accepted: bool,
        rollback_verified: bool | None,
        copper_state: str | None = None,
    ) -> ConnectionResult:
        # Central invariant: a result may never contradict the snapshot it carries.
        # If the final state could not be read, nothing about acceptance or
        # commitment is knowable -- and if the copper was supposed to be kept,
        # the session is quarantined rather than reporting success on a board it
        # cannot see.
        if not final_probe.verified and (accepted or committed or copper_state == COPPER_KEPT):
            if copper_state == COPPER_KEPT or committed:
                self.quarantine(
                    "the final board state could not be read after keeping copper"
                )
            evidence = dict(evidence)
            evidence["final_state_unreadable"] = True
            if final_probe.error:
                evidence["final_probe_error"] = final_probe.error
            outcome = Outcome.UNVERIFIED
            accepted = False
            if copper_state == COPPER_KEPT or committed:
                committed = None
                copper_state = COPPER_RETAINED_UNKNOWN
        evidence = dict(evidence)
        evidence["final"] = {
            "digest": final_probe.geometry_digest,
            "track_count": final_probe.track_count,
            "via_count": final_probe.via_count,
            "route_active": final_probe.route_active,
        }
        evidence.setdefault("verification_limits", list(unverifiable_properties()))
        state = copper_state or (COPPER_KEPT if committed else COPPER_UNCHANGED)
        evidence.setdefault("copper_state", state)
        snap = self._emit(final_probe, outcome, action="connect_targets", evidence=evidence)
        return ConnectionResult(
            outcome=outcome,
            committed=(None if (self._dirty or committed is None) else bool(committed)),
            connected=connected, start=start_pt, target=target_pt, mode=str(mode),
            steps=tuple(steps), snapshot=snap, evidence=evidence,
            rollback_verified=rollback_verified, accepted=accepted, dirty=self._dirty,
            copper_state=state,
        )

    def _run_step(
        self,
        kind: str,
        point: tuple[float, float],
        layer: int | None,
        mode_int: int,
    ) -> tuple[bool, Mapping[str, Any]]:
        engine = self._engine
        x_mm, y_mm = point
        if kind == "start":
            return core_action.start_route(engine, x_mm, y_mm, layer or 1)
        if kind == "restart":
            # Re-open the route after a via finished it, on the new layer. The
            # via just placed is this net's copper at this point, so the head
            # starts on it.
            return core_action.start_route(engine, x_mm, y_mm, layer or 1)
        if kind == "line":
            return core_action.make_line(engine, x_mm, y_mm, mode_int)
        if kind == "via":
            return core_action.make_via(engine, x_mm, y_mm, mode_int)
        if kind == "switch":
            ok = bool(engine.switch_layer(layer or 1))
            return ok, {"layer": layer}
        if kind == "finish":
            return core_action.finish(engine, mode_int)
        raise InvalidActionError("unknown_action", f"unknown transaction step {kind!r}")

    def _head(self) -> tuple[float, float, float] | None:
        try:
            session = self._engine.get_routing_session_state()
            if session.is_routing and float(session.route_head[2]) >= 0:
                return (
                    float(session.route_head[0]), float(session.route_head[1]),
                    float(session.route_head[2]),
                )
        except Exception:  # noqa: BLE001 - the step record simply has no head
            return None
        return None

    def _current_layer(self) -> int | None:
        head = self._head()
        if head is None or head[2] < 0:
            return None
        return int(head[2])

    def _head_matches(self, point: tuple[float, float], layer: int | None) -> bool:
        """True when the routing head is already at ``point`` (and ``layer``)."""
        head = self._head()
        if head is None:
            return False
        if layer is not None and int(head[2]) != int(layer):
            return False
        return abs(head[0] - point[0]) <= 1e-6 and abs(head[1] - point[1]) <= 1e-6

    # -- candidate probing -------------------------------------------------

    def probe_candidates(
        self,
        start: Sequence[float],
        target: Sequence[float],
        candidates: Sequence[Mapping[str, Any]],
        *,
        mode: str | int = "walkaround",
        token: str | None = None,
        max_candidates: int = 8,
    ) -> tuple[list[CandidateProbe], AgentSnapshot]:
        """Evaluate every candidate from the *same* checkpoint, then restore.

        A candidate is ``{"name": str, "waypoints": [...], "mode": str | int}``
        (``mode``/``waypoints`` optional). Each candidate's result is its
        ``would-commit`` outcome; the returned snapshot describes the board after
        every candidate has been undone, so the caller always sees the restored
        state. Probing stops immediately if a restore cannot be verified.
        """
        if len(candidates) > max_candidates:
            snap = self._emit(
                self._probe(include_geometry=False), Outcome.INVALID_ACTION,
                action="probe_candidates",
                evidence={
                    "reason": "too_many_candidates",
                    "requested": len(candidates), "max_candidates": max_candidates,
                },
            )
            return [], snap

        start3 = _point3(start)
        target3 = _point3(target)
        with self._lock:
            probe = self._probe()
            if not probe.verified:
                snap = self._emit(
                    probe, Outcome.UNVERIFIED, action="probe_candidates",
                    evidence={"stage": "pre_probe"},
                )
                return [], snap
            refusal = self._gate_unusable(probe, "probe_candidates")
            if refusal is not None:
                return [], refusal
            refusal = self._gate_token(token, probe, "probe_candidates")
            if refusal is not None:
                return [], refusal
            try:
                self._require_rules()
            except RulesUnavailableError as exc:
                snap = self._emit(
                    probe, Outcome.UNSUPPORTED, action="probe_candidates",
                    evidence=exc.detail,
                )
                return [], snap

            base_digest = probe.geometry_digest
            base_ckpt = self._engine.checkpoint()
            results: list[CandidateProbe] = []
            restore_failed = False
            try:
                for index, candidate in enumerate(candidates):
                    name = str(candidate.get("name", f"candidate_{index}"))
                    mode_i = candidate.get("mode", mode)
                    waypoints = candidate.get("waypoints", ())

                    if not self._restore_to(base_ckpt, probe):
                        restore_failed = True
                        results.append(CandidateProbe(name, ConnectionResult(
                            Outcome.UNSUPPORTED, False, False, start3, target3, str(mode_i),
                            (), None,
                            {"reason": "checkpoint_restore_mismatch", "candidate": name},
                            rollback_verified=False, identical_start=False,
                            dirty=self._dirty,
                        )))
                        break

                    result = self._connect_locked(
                        start, target, mode_i, waypoints=waypoints,
                        token=None, internal=True,
                    )
                    # A probe result answers "what would this candidate have
                    # committed?" while the board itself has been restored, so the
                    # two facts are reported as two fields.
                    results.append(CandidateProbe(
                        name,
                        replace(
                            result, identical_start=True, evaluated=True,
                            would_commit=result.committed, committed=False,
                            copper_state=(
                                COPPER_RETAINED_UNKNOWN if result.dirty else COPPER_RESTORED
                            ),
                        ),
                    ))
                    if result.dirty:
                        restore_failed = True
                        break
            finally:
                restored = self._restore_to(base_ckpt, probe)
                self._release(base_ckpt)
                if not restored:
                    restore_failed = True
                    self.quarantine(
                        "candidate probing could not restore the shared checkpoint"
                    )

            self._engine.build_connectivity()
            final_probe = self._probe()
            restored_ok = final_probe.geometry_digest == base_digest
            if not restored_ok and not self._dirty:
                self.quarantine("candidate probing left the board different from the checkpoint")
            outcome = Outcome.OK if (restored_ok and not restore_failed) else Outcome.UNSUPPORTED
            snap = self._emit(
                final_probe, outcome, action="probe_candidates",
                evidence={
                    "restored_ok": restored_ok,
                    "restore_failed": restore_failed,
                    "base_geometry_digest": base_digest,
                    "restored_geometry_digest": final_probe.geometry_digest,
                    "session_quarantined": self._dirty,
                    "quarantine_reason": self._dirty_reason,
                    "unverifiable": list(unverifiable_properties()),
                },
            )
            return results, snap

    def _restore_to(self, handle: int, expected: StateProbe) -> bool:
        restored, _detail = self._restore_and_verify(handle, expected)
        return restored
