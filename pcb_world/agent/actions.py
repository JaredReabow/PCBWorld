"""Structured, validated agent actions over the existing routing action set.

This module is the agent-facing *front door* to the same six routing actions the
RL index space and the LLM text projection already use. It adds, and only adds:

* a **versioned** structured action record (:class:`StructuredAction`) that is
  converted to the legacy env dict by :meth:`StructuredAction.to_env_action`, so
  every existing consumer keeps working unchanged;
* the human-facing mode names ``mark_obstacles`` / ``shove`` / ``walkaround``,
  mapped to the engine's integer encoding **centrally** through
  :mod:`pcb_world.core.action_schema` — the single source of truth for what
  ``m``/``p``/``w`` mean. Nothing here re-declares the numbers ``0/1/2``, so a
  change in the canonical table cannot silently desynchronise the agent layer;
* validation that runs *before* any engine mutation: non-finite or malformed
  coordinates, points outside the board outline, an unknown or non-copper layer,
  an unknown mode, an unknown action name, and wrong-phase operations all raise
  :class:`InvalidActionError` carrying a machine-readable ``reason``.

The LLM projection (``methods/llm_agent``) and the RL codec are untouched: they
remain the back-compatible text/index paths, and this layer sits beside them.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

from pcb_world.core.action_schema import (
    ACT_FINISH,
    ACT_IDLE,
    ACT_MAKE_LINE,
    ACT_MAKE_VIA,
    ACT_NET_END,
    ACT_NET_SELECT,
    ACT_START_ROUTE,
    ACTION_NAMES,
    MODE_INT_TO_LETTER,
    MODE_LETTER_TO_INT,
)


# Version of the *agent-facing* action/observation contract. Bumped whenever the
# shape or meaning of what this package hands back changes; it travels in every
# snapshot so a caller can refuse a contract it does not understand.
SCHEMA_VERSION = "1.0"

# Versions this build can execute. A payload declaring anything else is refused
# rather than best-effort interpreted (a caller written for 2.0 must not be run
# as if it were 1.0).
SUPPORTED_SCHEMA_VERSIONS: tuple[str, ...] = (SCHEMA_VERSION,)


# ---------------------------------------------------------------------------
# Routing modes — names owned here, numbers owned by action_schema
# ---------------------------------------------------------------------------

# Canonical letter -> human name. The letters are the legacy encoding; the
# numbers behind them come from ``MODE_LETTER_TO_INT`` and are never re-typed.
_MODE_LETTER_TO_NAME: dict[str, str] = {
    "m": "mark_obstacles",
    "p": "shove",
    "w": "walkaround",
}

MODE_NAME_TO_INT: dict[str, int] = {
    name: MODE_LETTER_TO_INT[letter] for letter, name in _MODE_LETTER_TO_NAME.items()
}
MODE_INT_TO_NAME: dict[int, str] = {v: k for k, v in MODE_NAME_TO_INT.items()}
MODE_NAMES: tuple[str, ...] = tuple(MODE_NAME_TO_INT)


def mode_to_int(mode: str | int) -> int:
    """Resolve a mode name (``shove``), legacy letter (``p``) or int to its index.

    Raises :class:`InvalidActionError` (``reason="unknown_mode"``) for anything
    that is not one of the three engine modes — a silent fallback to walkaround
    would hide a caller's typo behind a plausible route.
    """
    if isinstance(mode, bool):  # bool is an int subclass; never a mode
        raise InvalidActionError("unknown_mode", f"mode must be a name/letter/int, got {mode!r}")
    if isinstance(mode, int):
        if mode in MODE_INT_TO_NAME:
            return mode
        raise InvalidActionError(
            "unknown_mode",
            f"mode {mode!r} is not a routing mode; valid ints: {sorted(MODE_INT_TO_NAME)}",
        )
    if isinstance(mode, str):
        key = mode.strip()
        if key in MODE_NAME_TO_INT:
            return MODE_NAME_TO_INT[key]
        if key in MODE_LETTER_TO_INT:
            return MODE_LETTER_TO_INT[key]
        try:
            as_int = int(key)
        except ValueError:
            as_int = None
        if as_int is not None:
            return mode_to_int(as_int)
    raise InvalidActionError(
        "unknown_mode",
        f"mode {mode!r} is not a routing mode; valid names: {list(MODE_NAMES)}",
    )


def mode_to_letter(mode: str | int) -> str:
    """Legacy single-letter encoding of a mode (back-compatible text output)."""
    index = mode_to_int(mode)
    letter = MODE_INT_TO_LETTER.get(index)
    if letter is None:  # pragma: no cover - MODE_* tables are in sync by construction
        raise InvalidActionError("unknown_mode", f"no letter for mode index {index}")
    return letter


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class InvalidActionError(ValueError):
    """An action was rejected before it could touch the engine.

    ``reason`` is a stable machine-readable code (see :data:`INVALID_REASONS`)
    and ``detail`` carries whatever the caller needs to correct the action.
    """

    def __init__(self, reason: str, message: str, **detail: Any) -> None:
        super().__init__(message)
        self.reason = reason
        self.detail: dict[str, Any] = {"reason": reason, **detail}

    def to_evidence(self) -> dict[str, Any]:
        return dict(self.detail)


# Reason codes, one per rejected condition. Kept as module constants so callers
# and tests share the vocabulary instead of matching on message text.
REASON_UNKNOWN_ACTION = "unknown_action"
REASON_MALFORMED_COORDINATE = "malformed_coordinate"
REASON_NONFINITE_COORDINATE = "nonfinite_coordinate"
REASON_OUT_OF_BOUNDS = "out_of_bounds"
REASON_UNKNOWN_LAYER = "unknown_layer"
REASON_NON_COPPER_LAYER = "non_copper_layer"
REASON_UNKNOWN_MODE = "unknown_mode"
REASON_WRONG_PHASE = "wrong_phase"
REASON_MISSING_PARAM = "missing_param"
REASON_UNKNOWN_FIELD = "unknown_field"
REASON_SCHEMA_UNSUPPORTED = "schema_unsupported"
REASON_TOKEN_REQUIRED = "token_required"
REASON_ENDPOINT_UNKNOWN = "endpoint_unknown"
REASON_ENDPOINT_AMBIGUOUS = "endpoint_ambiguous"
REASON_DRC_UNAVAILABLE = "drc_unavailable"
REASON_DRC_REGRESSION = "drc_regression"
REASON_RULES_UNAVAILABLE = "rules_unavailable"
REASON_SESSION_QUARANTINED = "session_quarantined"
REASON_UNDERSPECIFIED = "underspecified"
REASON_CHECKPOINT_UNAVAILABLE = "checkpoint_unavailable"
REASON_UNVERIFIED_STATE = "unverified_state"
REASON_RULES_CONTEXT_CHANGED = "rules_context_changed"

INVALID_REASONS: tuple[str, ...] = (
    REASON_UNKNOWN_ACTION,
    REASON_MALFORMED_COORDINATE,
    REASON_NONFINITE_COORDINATE,
    REASON_OUT_OF_BOUNDS,
    REASON_UNKNOWN_LAYER,
    REASON_NON_COPPER_LAYER,
    REASON_UNKNOWN_MODE,
    REASON_WRONG_PHASE,
    REASON_MISSING_PARAM,
    REASON_UNKNOWN_FIELD,
    REASON_SCHEMA_UNSUPPORTED,
    REASON_TOKEN_REQUIRED,
    REASON_ENDPOINT_UNKNOWN,
    REASON_ENDPOINT_AMBIGUOUS,
    REASON_DRC_UNAVAILABLE,
    REASON_DRC_REGRESSION,
    REASON_RULES_UNAVAILABLE,
    REASON_SESSION_QUARANTINED,
    REASON_UNDERSPECIFIED,
    REASON_CHECKPOINT_UNAVAILABLE,
    REASON_UNVERIFIED_STATE,
    REASON_RULES_CONTEXT_CHANGED,
)


def _as_float(value: Any, field_name: str) -> float:
    """Coerce a coordinate at construction, with a classed error on failure.

    Failing here rather than in the validator means a malformed coordinate never
    even becomes an action -- but the error is the same ``InvalidActionError``
    the validator raises, so callers have one thing to catch.
    """
    if isinstance(value, bool):
        raise InvalidActionError(
            REASON_MALFORMED_COORDINATE,
            f"{field_name} must be a number, got {value!r}", field=field_name,
        )
    try:
        return float(value)
    except (TypeError, ValueError):
        raise InvalidActionError(
            REASON_MALFORMED_COORDINATE,
            f"{field_name} must be a number, got {value!r}",
            field=field_name, value=repr(value),
        ) from None


def _as_int(value: Any, field_name: str) -> int:
    """Strict integer coercion: no bools, no silently truncated fractions.

    ``int(1.8)`` is 1 and ``int(True)`` is 1; both would turn a caller's typo
    into a plausible action, so they are refused instead.
    """
    if isinstance(value, bool):
        raise InvalidActionError(
            REASON_MALFORMED_COORDINATE,
            f"{field_name} must be an integer, got {value!r}", field=field_name,
        )
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if value.is_integer() and math.isfinite(value):
            return int(value)
        raise InvalidActionError(
            REASON_MALFORMED_COORDINATE,
            f"{field_name} must be a whole number, got {value!r}", field=field_name,
        )
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            raise InvalidActionError(
                REASON_MALFORMED_COORDINATE,
                f"{field_name} must be an integer, got {value!r}",
                field=field_name, value=value,
            ) from None
    raise InvalidActionError(
        REASON_MALFORMED_COORDINATE,
        f"{field_name} must be an integer, got {type(value).__name__}",
        field=field_name,
    )


# ---------------------------------------------------------------------------
# Phase
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ActionPhase:
    """What the engine currently allows, as far as the caller knows.

    ``route_active`` is the only phase bit the low-level interface has: while a
    route is open, ``make_line`` / ``make_via`` / ``finish`` / ``net_end`` are
    meaningful and ``start_route`` is not, and vice versa. Keeping it a single
    bit mirrors the engine instead of inventing a state machine on top.
    """

    route_active: bool = False


# ---------------------------------------------------------------------------
# Structured action
# ---------------------------------------------------------------------------

# Which parameters each action carries. Derived from ACTION_NAMES so an action
# added to the canonical registry is visible here as "unknown params" rather
# than silently accepted.
_ACTION_PARAMS: dict[str, tuple[str, ...]] = {
    "net_select": ("net_id",),
    "start_route": ("x_mm", "y_mm", "layer"),
    "net_end": (),
    "make_line": ("x_mm", "y_mm", "mode"),
    "make_via": ("x_mm", "y_mm", "mode"),
    "finish": ("mode",),
}

# Action name -> canonical action index, resolved from the registry by name.
_ACTION_NAME_TO_TYPE: dict[str, int] = {
    "net_select": ACT_NET_SELECT,
    "start_route": ACT_START_ROUTE,
    "net_end": ACT_NET_END,
    "make_line": ACT_MAKE_LINE,
    "make_via": ACT_MAKE_VIA,
    "finish": ACT_FINISH,
}

assert set(_ACTION_NAME_TO_TYPE) == set(_ACTION_PARAMS)  # noqa: S101 - import-time contract


@dataclass(frozen=True)
class StructuredAction:
    """One validated-by-construction agent action.

    Instances are plain data; validation happens in :class:`ActionValidator`
    against a real board (bounds/layer/phase), which is what keeps this module
    free of board knowledge. ``params`` are the *canonical* parameter names
    (``x_mm``/``y_mm``/``layer``/``mode``/``net_id``).
    """

    name: str
    params: Mapping[str, Any] = field(default_factory=dict)
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        # A record may be built by hand (or decoded from JSON), so the contract
        # is asserted here as well as at the coercion boundary.
        if self.schema_version not in SUPPORTED_SCHEMA_VERSIONS:
            raise InvalidActionError(
                REASON_SCHEMA_UNSUPPORTED,
                f"schema_version {self.schema_version!r} is not supported "
                f"(supported: {list(SUPPORTED_SCHEMA_VERSIONS)})",
                schema_version=self.schema_version,
                supported=list(SUPPORTED_SCHEMA_VERSIONS),
            )
        if self.name not in _ACTION_PARAMS:
            raise InvalidActionError(
                REASON_UNKNOWN_ACTION,
                f"unknown action {self.name!r}; known: {sorted(_ACTION_PARAMS)}",
                action=self.name,
            )
        unknown = [key for key in self.params if key not in _ACTION_PARAMS[self.name]]
        if unknown:
            raise InvalidActionError(
                REASON_UNKNOWN_FIELD,
                f"{self.name} got unknown parameter(s) {sorted(unknown)}; "
                f"expected {list(_ACTION_PARAMS[self.name])}",
                action=self.name, unknown=sorted(unknown),
                expected=list(_ACTION_PARAMS[self.name]),
            )

    # -- constructors ------------------------------------------------------

    @classmethod
    def net_select(cls, net_id: int) -> "StructuredAction":
        return cls("net_select", {"net_id": _as_int(net_id, "net_id")})

    @classmethod
    def start_route(cls, x_mm: float, y_mm: float, layer: int) -> "StructuredAction":
        return cls("start_route", {
            "x_mm": _as_float(x_mm, "x_mm"), "y_mm": _as_float(y_mm, "y_mm"),
            "layer": _as_int(layer, "layer"),
        })

    @classmethod
    def make_line(cls, x_mm: float, y_mm: float, mode: str | int = "walkaround") -> "StructuredAction":
        return cls("make_line", {
            "x_mm": _as_float(x_mm, "x_mm"), "y_mm": _as_float(y_mm, "y_mm"), "mode": mode,
        })

    @classmethod
    def make_via(cls, x_mm: float, y_mm: float, mode: str | int = "walkaround") -> "StructuredAction":
        return cls("make_via", {
            "x_mm": _as_float(x_mm, "x_mm"), "y_mm": _as_float(y_mm, "y_mm"), "mode": mode,
        })

    @classmethod
    def finish(cls, mode: str | int = "walkaround") -> "StructuredAction":
        return cls("finish", {"mode": mode})

    @classmethod
    def net_end(cls) -> "StructuredAction":
        return cls("net_end", {})

    # -- legacy bridges ----------------------------------------------------

    def to_env_action(self) -> dict[str, Any]:
        """The legacy env dict for this action (back-compatible in one direction).

        ``mode`` is emitted as the legacy integer, which is exactly what the RL
        index path and ``pcb_world.core.env.PCBWorld.step`` expect.
        """
        try:
            action_type = _ACTION_NAME_TO_TYPE[self.name]
        except KeyError:  # pragma: no cover - constructors cover the registry
            raise InvalidActionError(
                REASON_UNKNOWN_ACTION,
                f"unknown action {self.name!r}; known: {sorted(_ACTION_NAME_TO_TYPE)}",
            ) from None
        out: dict[str, Any] = {"action_type": action_type}
        for key, value in self.params.items():
            # ``mode`` is this layer's name for the routing-mode parameter; the
            # legacy env/RL dictionaries spell it ``routing_mode``.
            out["routing_mode" if key == "mode" else key] = (
                mode_to_int(value) if key == "mode" else value
            )
        return out

    def to_text(self) -> str:
        """Legacy ``<action>name p1 p2 ...</action>`` text (LLM projection form).

        Positions exist because the projection's parser is positional; the
        parameter order is taken from the canonical registry.
        """
        if self.name not in _ACTION_PARAMS:
            raise InvalidActionError(REASON_UNKNOWN_ACTION, f"unknown action {self.name!r}")
        tokens = [self.name]
        for key in _ACTION_PARAMS[self.name]:
            if key not in self.params:
                raise InvalidActionError(
                    REASON_MISSING_PARAM, f"{self.name} needs {key!r}"
                )
            value = self.params[key]
            if key == "mode":
                tokens.append(mode_to_letter(value))
            elif key == "layer":
                tokens.append(str(int(value)))
            elif key == "net_id":
                tokens.append(str(int(value)))
            else:
                tokens.append(f"{float(value):g}")
        return "<action>" + " ".join(tokens) + "</action>"

    @classmethod
    def from_env_action(cls, action: Mapping[str, Any]) -> "StructuredAction":
        """Rebuild a structured action from a legacy env dict.

        ``idle`` and unknown action types are refused (``unknown_action``): the
        agent layer has no idle slot, and pretending otherwise would let a parse
        failure look like a deliberate step.
        """
        action_type = int(action.get("action_type", -1))
        if action_type == ACT_IDLE:
            raise InvalidActionError(REASON_UNKNOWN_ACTION, "idle is not a structured action")
        for name, index in _ACTION_NAME_TO_TYPE.items():
            if index == action_type:
                params: dict[str, Any] = {}
                for key in _ACTION_PARAMS[name]:
                    if key == "mode":
                        if "routing_mode" in action:
                            params["mode"] = action["routing_mode"]
                    elif key in action:
                        params[key] = action[key]
                return cls(name, params)
        raise InvalidActionError(
            REASON_UNKNOWN_ACTION,
            f"action_type {action_type} is not one of {sorted(_ACTION_NAME_TO_TYPE.values())}",
        )


def canonical_action_names() -> tuple[str, ...]:
    """Names this layer can express, from the canonical registry."""
    return tuple(name for name in ACTION_NAMES if name in _ACTION_NAME_TO_TYPE)


# ---------------------------------------------------------------------------
# Validator
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BoardLimits:
    """Physical limits used for pre-dispatch validation.

    ``bbox`` is ``(x_min, y_min, x_max, y_max)`` in mm, or None when the caller
    has no outline (bounds are then not enforced rather than guessed).
    ``copper_layers`` is the number of copper layers (max human layer id).
    """

    bbox: tuple[float, float, float, float] | None = None
    copper_layers: int = 2
    # Slack applied to the outline test, in mm: the board outline is often not
    # the copper extent, and a pad can legitimately sit on the edge.
    bbox_slack_mm: float = 0.0


class ActionValidator:
    """Validates structured actions against board limits and the current phase.

    The validator never touches the engine. A session calls it *before*
    dispatching so that an invalid action cannot mutate the board, and so the
    failure is reported as ``invalid_action`` rather than as an engine error.
    """

    def __init__(self, limits: BoardLimits | None = None) -> None:
        self.limits = limits or BoardLimits()

    # -- helpers -----------------------------------------------------------

    def coord(self, value: Any, *, field_name: str) -> float:
        if isinstance(value, bool):
            raise InvalidActionError(
                REASON_MALFORMED_COORDINATE, f"{field_name} must be a number, got {value!r}",
                field=field_name,
            )
        try:
            out = float(value)
        except (TypeError, ValueError):
            raise InvalidActionError(
                REASON_MALFORMED_COORDINATE, f"{field_name} must be a number, got {value!r}",
                field=field_name, value=repr(value),
            ) from None
        if not math.isfinite(out):
            raise InvalidActionError(
                REASON_NONFINITE_COORDINATE,
                f"{field_name} must be finite, got {out!r}",
                field=field_name, value=out,
            )
        return out

    def layer(self, value: Any) -> int:
        try:
            layer = _as_int(value, "layer")
        except InvalidActionError as exc:
            raise InvalidActionError(
                REASON_UNKNOWN_LAYER, str(exc), layer=repr(value)
            ) from None
        if layer < 1:
            raise InvalidActionError(
                REASON_NON_COPPER_LAYER,
                f"layer {layer} is not a copper layer (1..{self.limits.copper_layers})",
                layer=layer, copper_layers=self.limits.copper_layers,
            )
        if layer > self.limits.copper_layers:
            raise InvalidActionError(
                REASON_UNKNOWN_LAYER,
                f"layer {layer} is outside this board's 1..{self.limits.copper_layers}",
                layer=layer, copper_layers=self.limits.copper_layers,
            )
        return layer

    def bounds(self, x_mm: float, y_mm: float) -> None:
        bbox = self.limits.bbox
        if bbox is None:
            return
        x0, y0, x1, y1 = bbox
        slack = self.limits.bbox_slack_mm
        if not (x0 - slack <= x_mm <= x1 + slack and y0 - slack <= y_mm <= y1 + slack):
            raise InvalidActionError(
                REASON_OUT_OF_BOUNDS,
                f"({x_mm}, {y_mm}) is outside the board outline "
                f"({x0}..{x1}, {y0}..{y1}) +/- {slack} mm",
                x_mm=x_mm, y_mm=y_mm, bbox=list(bbox), slack_mm=slack,
            )

    # -- entry point -------------------------------------------------------

    def validate(
        self, action: StructuredAction, *, phase: ActionPhase | None = None,
    ) -> dict[str, Any]:
        """Return the legacy env dict for ``action`` or raise InvalidActionError.

        The returned dict is what :meth:`pcb_world.engine.kicad_engine.KiCadEngine`
        and ``PCBWorld.step`` consume, so a validated action can be dispatched
        without any further translation.
        """
        phase = phase or ActionPhase()
        name = action.name
        if name not in _ACTION_PARAMS:
            raise InvalidActionError(
                REASON_UNKNOWN_ACTION,
                f"unknown action {name!r}; known: {sorted(_ACTION_PARAMS)}",
            )

        required = _ACTION_PARAMS[name]
        missing = [key for key in required if key not in action.params]
        if missing:
            raise InvalidActionError(
                REASON_MISSING_PARAM, f"{name} is missing {', '.join(missing)}",
                action=name, missing=missing,
            )

        # Wrong-phase first: it is about the operation, not the values, and the
        # caller can fix it without re-measuring anything.
        if name in ("make_line", "make_via", "finish") and not phase.route_active:
            raise InvalidActionError(
                REASON_WRONG_PHASE,
                f"{name} needs an active route; start_route first",
                action=name, route_active=False,
            )
        if name == "start_route" and phase.route_active:
            raise InvalidActionError(
                REASON_WRONG_PHASE,
                "start_route is already active; finish, make_line/make_via or net_end first",
                action=name, route_active=True,
            )
        if name == "net_select":
            net_id = _as_int(action.params["net_id"], "net_id")
            if net_id <= 0:
                raise InvalidActionError(
                    REASON_UNKNOWN_ACTION,
                    f"net_id must be a positive net code, got {net_id}", field="net_id",
                )
            return {"action_type": ACT_NET_SELECT, "net_id": net_id}

        if name == "net_end":
            return {"action_type": ACT_NET_END}

        if name == "finish":
            return {"action_type": ACT_FINISH, "routing_mode": mode_to_int(action.params["mode"])}

        x_mm = self.coord(action.params["x_mm"], field_name="x_mm")
        y_mm = self.coord(action.params["y_mm"], field_name="y_mm")
        self.bounds(x_mm, y_mm)

        if name == "start_route":
            layer = self.layer(action.params["layer"])
            return {
                "action_type": ACT_START_ROUTE,
                "x_mm": x_mm, "y_mm": y_mm, "layer": layer,
            }
        if name == "make_line":
            return {
                "action_type": ACT_MAKE_LINE,
                "x_mm": x_mm, "y_mm": y_mm,
                "routing_mode": mode_to_int(action.params["mode"]),
            }
        if name == "make_via":
            return {
                "action_type": ACT_MAKE_VIA,
                "x_mm": x_mm, "y_mm": y_mm,
                "routing_mode": mode_to_int(action.params["mode"]),
            }

        raise InvalidActionError(  # pragma: no cover - guarded by _ACTION_PARAMS
            REASON_UNKNOWN_ACTION, f"unhandled action {name!r}"
        )

    def validate_sequence(
        self, actions: Iterable[StructuredAction], *, phase: ActionPhase | None = None,
    ) -> list[dict[str, Any]]:
        """Validate a sequence, advancing a *predicted* phase as it goes.

        Used by the transactional API to reject a whole plan before the first
        mutation, while still allowing the plan to start from an idle or an
        already-open route.
        """
        predicted = phase or ActionPhase()
        out: list[dict[str, Any]] = []
        for action in actions:
            env_action = self.validate(action, phase=predicted)
            out.append(env_action)
            if action.name == "start_route":
                predicted = ActionPhase(route_active=True)
            elif action.name in ("net_end",):
                predicted = ActionPhase(route_active=False)
        return out


def coerce_action(action: StructuredAction | Mapping[str, Any]) -> StructuredAction:
    """Accept a structured action, the structured JSON form, or a legacy env dict.

    The structured JSON form is exactly what the tool adapter advertises::

        {"name": "make_line", "params": {"x_mm": 1, "y_mm": 2, "mode": "shove"},
         "schema_version": "1.0"}

    ``schema_version`` is required in that form and must be supported; unknown
    top-level keys are refused rather than ignored.
    """
    if isinstance(action, StructuredAction):
        return action
    if not isinstance(action, Mapping):
        raise InvalidActionError(
            REASON_MALFORMED_COORDINATE,
            f"action must be a StructuredAction or a mapping, got {type(action).__name__}",
        )
    if "action_type" in action:
        return StructuredAction.from_env_action(action)
    if "name" in action:
        allowed = {"name", "params", "schema_version"}
        unknown = sorted(set(action) - allowed)
        if unknown:
            raise InvalidActionError(
                REASON_UNKNOWN_FIELD,
                f"unknown top-level field(s) {unknown}; allowed: {sorted(allowed)}",
                unknown=unknown,
            )
        if "schema_version" not in action:
            raise InvalidActionError(
                REASON_SCHEMA_UNSUPPORTED,
                "schema_version is required in the structured action form",
                supported=list(SUPPORTED_SCHEMA_VERSIONS),
            )
        version = action["schema_version"]
        if version not in SUPPORTED_SCHEMA_VERSIONS:
            raise InvalidActionError(
                REASON_SCHEMA_UNSUPPORTED,
                f"schema_version {version!r} is not supported "
                f"(supported: {list(SUPPORTED_SCHEMA_VERSIONS)})",
                schema_version=version, supported=list(SUPPORTED_SCHEMA_VERSIONS),
            )
        params = action.get("params", {})
        if not isinstance(params, Mapping):
            raise InvalidActionError(
                REASON_MALFORMED_COORDINATE,
                f"params must be a mapping, got {type(params).__name__}",
            )
        return StructuredAction(str(action["name"]), dict(params), version)
    raise InvalidActionError(
        REASON_UNKNOWN_ACTION,
        "action mapping must carry 'action_type' (legacy) or 'name' (structured)",
    )
