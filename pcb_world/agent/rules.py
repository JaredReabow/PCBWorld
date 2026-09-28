"""Design-rule context: prove which rules apply before any copper is committed.

The engine reads rules on two paths:

* ``run_drc(rules_path)`` — validation, which loads whatever file it is given;
* the routing-time ``DRC_ENGINE`` built inside the router, which (with the
  engine patch in ``patches/engine/``) loads the project's own
  ``<board>.kicad_dru``.

Measured on this build: **loading is not enforcing.** The routing-time engine
holds the file and the *validator* honours it, but the router still places
clearance-violating copper through a corridor the file forbids (see
``tests/agent/test_native_rules.py`` and RESULT.md §5). So this module never
claims "the router obeys the rules". It proves a weaker, verifiable statement —
*which* context applies — and the session then refuses to accept any mutation
that adds a violation under that exact context (``pcb_world.agent.drc_gate``).

No caller-supplied boolean can stand in for that evidence, and an unanswerable
context is a refusal, not a warning.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass, replace
from typing import Any


RULES_EXTENSION = ".kicad_dru"
PROJECT_EXTENSION = ".kicad_pro"


class RulesUnavailableError(RuntimeError):
    """The applicable rule context could not be proven; no mutation is allowed."""

    def __init__(self, message: str, **detail: Any) -> None:
        super().__init__(message)
        self.detail: dict[str, Any] = {
            "reason": "rules_unavailable", "message": message, **detail,
        }


#: Design-rule minima a via has to satisfy, in millimetres.
VIA_FLOOR_FIELDS = (
    "min_through_hole_mm",
    "min_via_diameter_mm",
    "min_via_annular_width_mm",
    "min_hole_to_hole_mm",
)


def _finite(value: Any) -> float | None:
    """``value`` as a finite positive float, or ``None`` when it is unusable.

    A design file can declare ``nan``, ``inf``, a string or a negative sentinel.
    None of those is a size, and silently coercing one would put an unmeasurable
    value into the router.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, str) or not isinstance(value, (int, float)):
        # A design file that puts text where a size belongs is malformed; coercing
        # it would hide that behind whatever ``float()`` happens to accept.
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number <= 0.0:
        return None
    return number


def _floor(value: Any) -> tuple[float | None, str]:
    """A design-rule minimum, or ``(None, reason)`` when it cannot be trusted.

    ``0.0`` is a real answer - KiCad uses it for "no constraint" - and is kept as
    zero. Anything else that is not a finite, non-negative number is an *invalid
    constraint*: reading it as zero would silently drop a floor the board may
    still enforce, so it makes the size unusable instead.
    """
    if value is None:
        return None, "absent"
    if isinstance(value, bool) or isinstance(value, str) or not isinstance(
        value, (int, float)
    ):
        return None, "not a number"
    number = float(value)
    if not math.isfinite(number):
        return None, "not finite"
    if number < 0.0:
        return None, "negative"
    return number, ""


@dataclass(frozen=True)
class ViaSizeResolution:
    """The via size a *specific net* may lawfully use, and what it was derived from.

    Three numbers matter and they are not the same number: what the project
    *declares* for the net's class, the board setup's *floors*, and the size that
    will actually be handed to the router. All three are kept so evidence never
    has to guess which one a field means.

    The adoption only applies the declared size and the *minimum* constraints
    visible through :meth:`get_design_rules`; it is not a proof that the router
    will honour every applicable rule. That proof is the transaction's native DRC
    acceptance, and the saved artifact's fresh whole-board gate.
    """

    net_code: int
    netclass: str = ""
    declared_diameter_mm: float | None = None
    declared_drill_mm: float | None = None
    adopted_diameter_mm: float | None = None
    adopted_drill_mm: float | None = None
    floors: dict[str, float] = None          # type: ignore[assignment]
    source: str = "unavailable"
    reason: str = ""
    #: Floors that could not be trusted, as ``"field=reason"``. Non-empty means
    #: ``usable`` is False: an unreadable constraint is not "no constraint".
    invalid_constraints: tuple[str, ...] = ()

    @property
    def usable(self) -> bool:
        return (
            self.source != "unavailable"
            and self.adopted_diameter_mm is not None
            and self.adopted_drill_mm is not None
        )

    def to_evidence(self) -> dict[str, Any]:
        return {
            "net_code": int(self.net_code),
            "netclass": self.netclass,
            "declared_via_diameter_mm": self.declared_diameter_mm,
            "declared_via_drill_mm": self.declared_drill_mm,
            "adopted_via_diameter_mm": self.adopted_diameter_mm,
            "adopted_via_drill_mm": self.adopted_drill_mm,
            "floors": dict(self.floors or {}),
            "invalid_constraints": list(self.invalid_constraints),
            "source": self.source,
            "reason": self.reason,
            "usable": self.usable,
        }


def resolve_via_size(engine: Any, net_code: int) -> ViaSizeResolution:
    """The lawful via size for ``net_code``, from that net's own effective class.

    Generic: the net's class is asked of the engine for the net actually being
    routed, so a board with several classes gets each net's own value. A net whose
    class is empty (the engine's "unknown net" answer) or whose values are not
    finite is reported unusable rather than defaulted to another net's numbers.
    """
    floors: dict[str, float] = {}
    try:
        rules = engine.get_design_rules()
    except Exception as exc:            # noqa: BLE001 - absent accessor
        return ViaSizeResolution(
            net_code=int(net_code), source="unavailable",
            reason=f"design rules unavailable: {type(exc).__name__}",
        )
    invalid: list[str] = []
    for field in VIA_FLOOR_FIELDS:
        value, reason = _floor(getattr(rules, field, None))
        if value is None:
            invalid.append(f"{field}={reason}")
            continue
        floors[field] = value
    if invalid:
        return ViaSizeResolution(
            net_code=int(net_code), floors=floors, source="unavailable",
            invalid_constraints=tuple(invalid),
            reason=(
                "the board's via constraints cannot be trusted, so no via size "
                "can be called lawful: " + ", ".join(invalid)
            ),
        )

    try:
        netclass = engine.get_netclass_for_net(int(net_code))
    except Exception as exc:            # noqa: BLE001
        return ViaSizeResolution(
            net_code=int(net_code), floors=floors, source="unavailable",
            reason=f"netclass lookup failed: {type(exc).__name__}",
        )
    name = str(getattr(netclass, "name", "") or "")
    if not name:
        return ViaSizeResolution(
            net_code=int(net_code), floors=floors, source="unavailable",
            reason="the engine returned no netclass for this net",
        )
    declared_diameter = _finite(getattr(netclass, "via_diameter_mm", None))
    declared_drill = _finite(getattr(netclass, "via_drill_mm", None))
    if declared_diameter is None or declared_drill is None:
        return ViaSizeResolution(
            net_code=int(net_code), netclass=name, floors=floors,
            declared_diameter_mm=declared_diameter,
            declared_drill_mm=declared_drill,
            source="unavailable",
            reason="the netclass declares no finite via diameter/drill",
        )
    min_hole = floors.get("min_through_hole_mm", 0.0)
    min_diameter = floors.get("min_via_diameter_mm", 0.0)
    min_annular = floors.get("min_via_annular_width_mm", 0.0)
    adopted_drill = max(declared_drill, min_hole)
    adopted_diameter = max(declared_diameter, min_diameter,
                           adopted_drill + 2.0 * min_annular)
    if not (math.isfinite(adopted_diameter) and math.isfinite(adopted_drill)):
        return ViaSizeResolution(
            net_code=int(net_code), netclass=name, floors=floors,
            declared_diameter_mm=declared_diameter,
            declared_drill_mm=declared_drill, source="unavailable",
            reason="the adopted size is not finite",
        )
    if adopted_diameter <= adopted_drill:
        return ViaSizeResolution(
            net_code=int(net_code), netclass=name, floors=floors,
            declared_diameter_mm=declared_diameter,
            declared_drill_mm=declared_drill, source="unavailable",
            reason=(
                f"the adopted diameter {adopted_diameter} does not exceed the "
                f"adopted drill {adopted_drill}"
            ),
        )
    # Native precision, deliberately: rounding to a display-friendly number of
    # decimals can land *below* a declared minimum (a floor of 0.30000000004
    # rounded to 0.3, for instance), and a size that is one ulp under a rule is
    # still a size the rule forbids. The exact floats are what travel to the
    # engine and to the evidence.
    return ViaSizeResolution(
        net_code=int(net_code), netclass=name, floors=floors,
        declared_diameter_mm=declared_diameter,
        declared_drill_mm=declared_drill,
        adopted_diameter_mm=adopted_diameter,
        adopted_drill_mm=adopted_drill,
        source="netclass",
    )


@dataclass(frozen=True)
class RuleContext:
    """The rule file this board is expected to be routed and validated under.

    An empty :attr:`rules_path` is not "unknown": it means the engine reports no
    project rule file, so the project's own implicit (netclass / board-setup)
    rules are the applicable context, and that is what the DRC gate enforces.
    """

    board_path: str = ""
    rules_path: str = ""
    project_path: str = ""
    source: str = "resolved"          # board | project | engine | explicit

    @property
    def rules_path_exists(self) -> bool:
        return bool(self.rules_path) and os.path.isfile(self.rules_path)

    @property
    def project_path_exists(self) -> bool:
        return bool(self.project_path) and os.path.isfile(self.project_path)

    def to_dict(self) -> dict[str, Any]:
        return {
            "board_path": self.board_path,
            "rules_path": self.rules_path,
            "project_path": self.project_path,
            "rules_path_exists": self.rules_path_exists,
            "project_path_exists": self.project_path_exists,
            "source": self.source,
        }


def resolve_rule_context(
    board_path: str | None,
    *,
    engine: Any | None = None,
    project_path: str | None = None,
    rules_path: str | None = None,
    source: str = "resolved",
) -> RuleContext:
    """Resolve the project's rule file for a board (never returns ``None``).

    Priority: explicit ``rules_path`` > the board's own ``<stem>.kicad_dru`` >
    the engine's reported project path. Mirrors pcbnew's
    ``PCB_BASE_EDIT_FRAME::GetDesignRulesPath()``: take the board's path, swap the
    extension.
    """
    board = os.path.abspath(board_path) if board_path else ""
    stem = ""
    if board:
        stem, _ext = os.path.splitext(board)
    elif project_path:
        stem, _ext = os.path.splitext(os.path.abspath(project_path))
    elif engine is not None:
        # The engine always knows the project it attached to the board; using it
        # means a session constructed without a board path still has a context
        # instead of raising on first use.
        try:
            derived = str(engine.get_project_path())
        except Exception:  # noqa: BLE001 - a backend without it is handled by the gate
            derived = ""
        if derived:
            stem, _ext = os.path.splitext(os.path.abspath(derived))

    resolved_rules = (
        os.path.abspath(rules_path) if rules_path
        else (stem + RULES_EXTENSION if stem else "")
    )
    resolved_project = (
        os.path.abspath(project_path) if project_path
        else (stem + PROJECT_EXTENSION if stem else "")
    )
    return RuleContext(
        board_path=board,
        rules_path=resolved_rules,
        project_path=resolved_project,
        source=source,
    )


def engine_rule_status(engine: Any) -> dict[str, Any]:
    """What the *backend* says about the rules it routed under.

    ``native_validation_blocked`` is True when the engine build predates the
    routing-rule-context accessors: the question cannot be answered at all, which
    is reported as "unknown", never as "rules applied".
    """
    status: dict[str, Any] = {
        "routing_rules_path": None,
        "routing_rules_loaded_from_file": None,
        "last_drc_rules_load_error": None,
        "project_loaded_from_file": None,
        "native_validation_blocked": False,
        "probe_error": None,
    }
    try:
        status["routing_rules_path"] = engine.get_routing_rules_path()
        status["routing_rules_loaded_from_file"] = bool(
            engine.was_routing_rules_loaded_from_file()
        )
        status["last_drc_rules_load_error"] = engine.get_last_drc_rules_load_error()
        status["project_loaded_from_file"] = bool(engine.was_project_loaded_from_file())
    except AttributeError as exc:
        status["native_validation_blocked"] = True
        status["probe_error"] = (
            "engine build cannot report its routing rule context "
            f"(get_routing_rules_path / was_routing_rules_loaded_from_file / "
            f"was_project_loaded_from_file): {exc}"
        )
    except Exception as exc:  # noqa: BLE001 - reported, not swallowed
        status["native_validation_blocked"] = True
        status["probe_error"] = f"{type(exc).__name__}: {exc}"
    return status


def assert_rules_applicable(engine: Any, ctx: RuleContext) -> dict[str, Any]:
    """Prove the applicable rule context, or raise :class:`RulesUnavailableError`.

    Checks, in order (every failure is a refusal):

    1. the backend can report its rule context at all;
    2. the project file was actually read from disk (a blank in-memory project
       means the board is on KiCad's compile-time defaults, which is not a
       context anyone can validate against);
    3. no requested rule file failed to load;
    4. when a ``<board>.kicad_dru`` exists, the routing engine really loaded that
       exact file.

    Returns evidence including the rules path the *validator* must use
    (``drc_rules_path``; empty means the project's implicit rules).
    """
    status = engine_rule_status(engine)
    evidence: dict[str, Any] = {"rule_context": ctx.to_dict(), "engine_rules": status}

    if status["native_validation_blocked"]:
        raise RulesUnavailableError(
            "engine build cannot report its rule context; refusing to mutate copper",
            native_validation_blocked=True, **evidence,
        )
    if status["project_loaded_from_file"] is not True:
        raise RulesUnavailableError(
            "the board's .kicad_pro was not read from disk, so the applicable rules "
            "are KiCad defaults rather than the project's; refusing to mutate copper",
            project_loaded_from_file=status["project_loaded_from_file"], **evidence,
        )
    if status["last_drc_rules_load_error"]:
        raise RulesUnavailableError(
            f"a requested design-rules file failed to load: "
            f"{status['last_drc_rules_load_error']}",
            **evidence,
        )

    # The engine's reported rule file is the authority. A caller's context can
    # only *agree* with it or be refused -- it can never downgrade a loaded file
    # to "implicit rules" by pointing somewhere that does not exist.
    loaded = str(status["routing_rules_path"] or "")
    loaded_from_file = bool(status["routing_rules_loaded_from_file"])

    if loaded_from_file and loaded:
        if not os.path.isfile(loaded):
            raise RulesUnavailableError(
                f"the routing engine reports rule file {loaded!r}, which no longer "
                "exists; refusing to mutate copper",
                **evidence,
            )
        if ctx.rules_path and os.path.abspath(ctx.rules_path) != os.path.abspath(loaded):
            raise RulesUnavailableError(
                f"this board's rules are {ctx.rules_path!r} but the routing engine "
                f"loaded {loaded!r}; refusing to mutate copper",
                **evidence,
            )
        evidence["drc_rules_path"] = loaded
        evidence["note"] = "the DRC gate enforces the file the routing engine loaded"
        return evidence

    # No file loaded by the engine. That is only acceptable when no project rule
    # file is present for this board either: otherwise the engine ignored a file
    # that exists, which is exactly the case that must fail closed.
    if ctx.rules_path_exists:
        raise RulesUnavailableError(
            f"a design-rules file exists ({ctx.rules_path}) but the routing-time DRC "
            "engine is not using it; refusing to mutate copper",
            **evidence,
        )
    evidence["drc_rules_path"] = ""
    evidence["note"] = (
        "no <board>.kicad_dru found and the engine reports no rule file: the "
        "project's implicit (netclass / board setup) rules are the applicable "
        "context and are what the DRC gate enforces"
    )
    return evidence


def with_rules_path(ctx: RuleContext, rules_path: str) -> RuleContext:
    """A copy of ``ctx`` pointing at an explicit rule file."""
    return replace(ctx, rules_path=os.path.abspath(rules_path), source="explicit")


def rule_status_summary(status: dict[str, Any]) -> str:
    """One-line human summary of :func:`engine_rule_status` (docs/logs only)."""
    if status.get("native_validation_blocked"):
        return "routing rules: unknown (engine build cannot report its rule context)"
    path = status.get("routing_rules_path")
    if status.get("routing_rules_loaded_from_file") and path:
        return f"routing rules: {path}"
    return "routing rules: project implicit rules (no .kicad_dru loaded)"
