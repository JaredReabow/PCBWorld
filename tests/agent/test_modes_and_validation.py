"""Named-mode equivalence, legacy back-compat, and pre-dispatch validation."""

from __future__ import annotations

import math

import pytest

from pcb_world.core.action_schema import MODE_INT_TO_LETTER, MODE_LETTER_TO_INT
from pcb_world.agent import (
    ActionPhase,
    ActionValidator,
    BoardLimits,
    InvalidActionError,
    MODE_INT_TO_NAME,
    MODE_NAME_TO_INT,
    StructuredAction,
    canonical_action_names,
    coerce_action,
    mode_to_int,
    mode_to_letter,
)
from pcb_world.core.action_schema import (
    ACT_FINISH,
    ACT_MAKE_LINE,
    ACT_MAKE_VIA,
    ACT_NET_END,
    ACT_NET_SELECT,
    ACT_START_ROUTE,
)


# ---------------------------------------------------------------------------
# Modes
# ---------------------------------------------------------------------------


def test_named_modes_match_the_canonical_letter_table():
    """The names are ours; the numbers come from action_schema, so they agree."""
    assert MODE_NAME_TO_INT == {
        "mark_obstacles": MODE_LETTER_TO_INT["m"],
        "shove": MODE_LETTER_TO_INT["p"],
        "walkaround": MODE_LETTER_TO_INT["w"],
    }
    assert MODE_NAME_TO_INT == {"mark_obstacles": 0, "shove": 1, "walkaround": 2}
    assert MODE_INT_TO_NAME == {v: k for k, v in MODE_NAME_TO_INT.items()}


@pytest.mark.parametrize("name,index,letter", [
    ("mark_obstacles", 0, "m"),
    ("shove", 1, "p"),
    ("walkaround", 2, "w"),
])
def test_mode_round_trip(name, index, letter):
    assert mode_to_int(name) == index
    assert mode_to_int(letter) == index
    assert mode_to_int(str(index)) == index
    assert mode_to_letter(name) == letter
    assert MODE_INT_TO_LETTER[index] == letter


def test_unknown_mode_is_refused_not_defaulted():
    for bad in ("shovee", "x", 7, 3.5, True, None, ""):
        with pytest.raises(InvalidActionError) as exc:
            mode_to_int(bad)
        assert exc.value.reason == "unknown_mode"


# ---------------------------------------------------------------------------
# Legacy back-compat
# ---------------------------------------------------------------------------


def test_structured_action_emits_the_legacy_env_dict():
    assert StructuredAction.net_select(3).to_env_action() == {
        "action_type": ACT_NET_SELECT, "net_id": 3,
    }
    assert StructuredAction.start_route(1.5, 2.5, 2).to_env_action() == {
        "action_type": ACT_START_ROUTE, "x_mm": 1.5, "y_mm": 2.5, "layer": 2,
    }
    # A mode name becomes the legacy *integer* the env/RL path expects.
    assert StructuredAction.make_line(1.0, 2.0, "shove").to_env_action() == {
        "action_type": ACT_MAKE_LINE, "x_mm": 1.0, "y_mm": 2.0, "routing_mode": 1,
    }
    assert StructuredAction.make_via(1.0, 2.0, "mark_obstacles").to_env_action() == {
        "action_type": ACT_MAKE_VIA, "x_mm": 1.0, "y_mm": 2.0, "routing_mode": 0,
    }
    assert StructuredAction.finish("walkaround").to_env_action() == {
        "action_type": ACT_FINISH, "routing_mode": 2,
    }
    assert StructuredAction.net_end().to_env_action() == {"action_type": ACT_NET_END}


def test_structured_action_text_matches_the_llm_projection_format():
    """The legacy text path parses positionally; keep the exact order/letters."""
    assert StructuredAction.net_select(4).to_text() == "<action>net_select 4</action>"
    assert (
        StructuredAction.start_route(1.25, 2.5, 1).to_text()
        == "<action>start_route 1.25 2.5 1</action>"
    )
    assert (
        StructuredAction.make_line(1.0, 2.0, "walkaround").to_text()
        == "<action>make_line 1 2 w</action>"
    )
    assert StructuredAction.finish("shove").to_text() == "<action>finish p</action>"
    assert StructuredAction.net_end().to_text() == "<action>net_end</action>"


def test_structured_text_is_accepted_by_the_llm_projection_parser():
    """Back-compat with the shipping LLM path: its parser reads our text as-is."""
    from methods.llm_agent.wrappers.action_converter import _parse_action_text

    cases = [
        (StructuredAction.net_select(4), {"action_type": ACT_NET_SELECT, "net_id": 4}),
        (StructuredAction.start_route(1.25, 2.5, 1),
         {"action_type": ACT_START_ROUTE, "x_mm": 1.25, "y_mm": 2.5, "layer": 1}),
        (StructuredAction.make_line(3.0, 4.0, "shove"),
         {"action_type": ACT_MAKE_LINE, "x_mm": 3.0, "y_mm": 4.0, "routing_mode": 1}),
        (StructuredAction.make_via(3.0, 4.0, "mark_obstacles"),
         {"action_type": ACT_MAKE_VIA, "x_mm": 3.0, "y_mm": 4.0, "routing_mode": 0}),
        (StructuredAction.finish("walkaround"),
         {"action_type": ACT_FINISH, "routing_mode": 2}),
        (StructuredAction.net_end(), {"action_type": ACT_NET_END}),
    ]
    for action, expected in cases:
        assert _parse_action_text(action.to_text()) == expected


def test_adapter_accepts_the_llm_parser_output():
    """The other direction: a dict from the LLM path becomes a structured action."""
    for env in (
        {"action_type": ACT_START_ROUTE, "x_mm": 1.0, "y_mm": 2.0, "layer": 1},
        {"action_type": ACT_MAKE_LINE, "x_mm": 1.0, "y_mm": 2.0, "routing_mode": 2},
        {"action_type": ACT_FINISH, "routing_mode": 1},
    ):
        structured = coerce_action(env)
        assert structured.to_env_action() == env


def test_legacy_env_dict_round_trips():
    """env dict -> structured -> env dict is stable (the mode lands as its int)."""
    for action in (
        StructuredAction.net_select(2),
        StructuredAction.start_route(1.0, 2.0, 1),
        StructuredAction.make_line(1.0, 2.0, "shove"),
        StructuredAction.make_via(1.0, 2.0, 2),
        StructuredAction.finish(0),
        StructuredAction.net_end(),
    ):
        env = action.to_env_action()
        assert coerce_action(env).to_env_action() == env
        assert coerce_action(env).name == action.name


def test_idle_and_unknown_action_types_are_refused():
    from pcb_world.core.action_schema import ACT_IDLE

    with pytest.raises(InvalidActionError) as exc:
        coerce_action({"action_type": ACT_IDLE})
    assert exc.value.reason == "unknown_action"
    with pytest.raises(InvalidActionError):
        coerce_action({"action_type": 99})


def test_canonical_names_cover_the_registry_minus_idle():
    assert set(canonical_action_names()) == {
        "net_select", "start_route", "net_end", "make_line", "make_via", "finish",
    }


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


@pytest.fixture
def validator() -> ActionValidator:
    return ActionValidator(BoardLimits(bbox=(0.0, 0.0, 50.0, 30.0), copper_layers=2))


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf"), "abc", None, True])
def test_bad_coordinates_are_refused(validator, bad):
    with pytest.raises(InvalidActionError) as exc:
        validator.validate(StructuredAction.start_route(bad, 1.0, 1), phase=ActionPhase())
    assert exc.value.reason in ("nonfinite_coordinate", "malformed_coordinate")


def test_nonfinite_y_is_refused(validator):
    with pytest.raises(InvalidActionError) as exc:
        validator.validate(
            StructuredAction.start_route(1.0, math.inf, 1), phase=ActionPhase()
        )
    assert exc.value.reason == "nonfinite_coordinate"
    assert exc.value.detail["field"] == "y_mm"


def test_out_of_bounds_is_refused(validator):
    with pytest.raises(InvalidActionError) as exc:
        validator.validate(StructuredAction.start_route(500.0, 10.0, 1), phase=ActionPhase())
    assert exc.value.reason == "out_of_bounds"


def test_layer_bounds_are_refused(validator):
    with pytest.raises(InvalidActionError) as exc:
        validator.validate(StructuredAction.start_route(1.0, 1.0, 5), phase=ActionPhase())
    assert exc.value.reason == "unknown_layer"
    with pytest.raises(InvalidActionError) as exc:
        validator.validate(StructuredAction.start_route(1.0, 1.0, 0), phase=ActionPhase())
    assert exc.value.reason == "non_copper_layer"
    with pytest.raises(InvalidActionError):
        validator.validate(StructuredAction.start_route(1.0, 1.0, -1), phase=ActionPhase())


def test_unknown_mode_is_refused_before_dispatch(validator):
    with pytest.raises(InvalidActionError) as exc:
        validator.validate(
            StructuredAction.make_line(1.0, 1.0, "nope"), phase=ActionPhase(route_active=True)
        )
    assert exc.value.reason == "unknown_mode"


def test_wrong_phase_operations_are_refused(validator):
    # No active route: the movement actions make no sense.
    for action in (
        StructuredAction.make_line(1.0, 1.0, "walkaround"),
        StructuredAction.make_via(1.0, 1.0, "walkaround"),
        StructuredAction.finish("walkaround"),
    ):
        with pytest.raises(InvalidActionError) as exc:
            validator.validate(action, phase=ActionPhase(route_active=False))
        assert exc.value.reason == "wrong_phase"
    # Already routing: starting another route is the wrong phase.
    with pytest.raises(InvalidActionError) as exc:
        validator.validate(
            StructuredAction.start_route(1.0, 1.0, 1), phase=ActionPhase(route_active=True)
        )
    assert exc.value.reason == "wrong_phase"


def test_missing_parameter_is_reported(validator):
    with pytest.raises(InvalidActionError) as exc:
        validator.validate(StructuredAction("make_line", {"x_mm": 1.0, "y_mm": 2.0}))
    assert exc.value.reason == "missing_param"
    assert "mode" in exc.value.detail["missing"]


def test_unknown_action_name_is_refused(validator):
    with pytest.raises(InvalidActionError) as exc:
        validator.validate(StructuredAction("teleport", {}))
    assert exc.value.reason == "unknown_action"


def test_valid_action_returns_the_env_dict(validator):
    env = validator.validate(
        StructuredAction.make_via(3.0, 4.0, "shove"), phase=ActionPhase(route_active=True)
    )
    assert env == {
        "action_type": ACT_MAKE_VIA, "x_mm": 3.0, "y_mm": 4.0, "routing_mode": 1,
    }


def test_validate_sequence_rejects_a_plan_that_starts_mid_route(validator):
    plan = [
        StructuredAction.start_route(1.0, 1.0, 1),
        StructuredAction.make_line(2.0, 1.0, "walkaround"),
    ]
    env_actions = validator.validate_sequence(plan, phase=ActionPhase())
    assert [a["action_type"] for a in env_actions] == [ACT_START_ROUTE, ACT_MAKE_LINE]

    with pytest.raises(InvalidActionError) as exc:
        validator.validate_sequence(
            [StructuredAction.make_line(2.0, 1.0, "walkaround")], phase=ActionPhase()
        )
    assert exc.value.reason == "wrong_phase"


def test_bounds_are_optional_not_guessed():
    """No outline -> no bounds test; the validator must not invent a board box."""
    v = ActionValidator(BoardLimits(bbox=None, copper_layers=2))
    assert v.validate(StructuredAction.start_route(9999.0, 9999.0, 1), phase=ActionPhase())


# ---------------------------------------------------------------------------
# Strict contracts (schema version, integer strictness, structured JSON)
# ---------------------------------------------------------------------------


def test_unsupported_schema_version_is_refused():
    with pytest.raises(InvalidActionError) as exc:
        StructuredAction("net_select", {"net_id": 1}, "99")
    assert exc.value.reason == "schema_unsupported"
    with pytest.raises(InvalidActionError) as exc:
        coerce_action({"name": "net_select", "params": {"net_id": 1},
                       "schema_version": "2.0"})
    assert exc.value.reason == "schema_unsupported"


def test_structured_json_form_requires_and_accepts_the_schema_version():
    action = coerce_action({
        "name": "make_line",
        "params": {"x_mm": 1.0, "y_mm": 2.0, "mode": "shove"},
        "schema_version": "1.0",
    })
    assert action == StructuredAction.make_line(1.0, 2.0, "shove")
    with pytest.raises(InvalidActionError) as exc:
        coerce_action({"name": "make_line", "params": {"x_mm": 1.0, "y_mm": 2.0,
                                                      "mode": "shove"}})
    assert exc.value.reason == "schema_unsupported"


def test_structured_json_form_refuses_unknown_fields():
    with pytest.raises(InvalidActionError) as exc:
        coerce_action({"name": "net_end", "params": {}, "schema_version": "1.0",
                       "surprise": 1})
    assert exc.value.reason == "unknown_field"
    with pytest.raises(InvalidActionError) as exc:
        coerce_action({"name": "make_line", "schema_version": "1.0",
                       "params": {"x_mm": 1.0, "y_mm": 2.0, "mode": "shove", "z": 3}})
    assert exc.value.reason == "unknown_field"


def test_structured_json_form_refuses_an_unknown_action_name():
    with pytest.raises(InvalidActionError) as exc:
        coerce_action({"name": "teleport", "params": {}, "schema_version": "1.0"})
    assert exc.value.reason == "unknown_action"


def test_structured_json_form_refuses_non_mapping_params():
    with pytest.raises(InvalidActionError):
        coerce_action({"name": "net_end", "params": [1, 2], "schema_version": "1.0"})


@pytest.mark.parametrize("layer", [1.8, True, "1.5", None, [1]])
def test_fractional_or_non_integer_layers_are_refused(layer):
    """int(1.8) is 1 and int(True) is 1: both would silently change the action."""
    with pytest.raises(InvalidActionError):
        StructuredAction.start_route(1.0, 1.0, layer)
    v = ActionValidator(BoardLimits(bbox=None, copper_layers=4))
    with pytest.raises(InvalidActionError):
        v.validate(StructuredAction("start_route", {"x_mm": 1.0, "y_mm": 1.0, "layer": layer}))


def test_integral_floats_are_accepted_for_ints():
    assert StructuredAction.start_route(1.0, 1.0, 2.0).params["layer"] == 2
    assert StructuredAction.net_select(3.0).params["net_id"] == 3
    v = ActionValidator(BoardLimits(bbox=None, copper_layers=4))
    env = v.validate(StructuredAction("start_route", {"x_mm": 1.0, "y_mm": 1.0,
                                                      "layer": 3.0}))
    assert env["layer"] == 3


@pytest.mark.parametrize("net_id", [True, 1.5, "abc", None])
def test_non_integer_net_ids_are_refused(net_id):
    v = ActionValidator(BoardLimits(bbox=None, copper_layers=2))
    with pytest.raises(InvalidActionError):
        v.validate(StructuredAction("net_select", {"net_id": net_id}))
