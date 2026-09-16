from __future__ import annotations

from copy import deepcopy
from dataclasses import FrozenInstanceError

import pytest

from customer_agents import (
    GAP_GAIN,
    K_PERCEP,
    REL_ALPHA,
    REL_START,
    SAYDO_GAP,
    Persona,
    campaign_response,
    focus_group,
    mock_generator,
    revealed_warmth,
    run_focus_groups_over_history,
    stated_warmth,
    update_relationship,
)
from game import run_game


TEAMS = ("A", "B", "C", "D")
SEGMENTS = ("LEG", "FIT", "FUN", "GENZ", "MAIN")
PERSONAS = (
    Persona("derek", "Derek", "LEG", 0.03),
    Persona("marcus", "Marcus", "LEG", -0.02),
    Persona("priya", "Priya", "FIT", 0.00),
    Persona("sam", "Sam", "FIT", 0.01),
    Persona("chloe", "Chloe", "FUN", 0.02),
    Persona("ava", "Ava", "FUN", -0.03),
    Persona("nia", "Nia", "GENZ", 0.00),
    Persona("leo", "Leo", "GENZ", 0.01),
    Persona("rosa", "Rosa", "MAIN", -0.01),
    Persona("tom", "Tom", "MAIN", 0.02),
)
BASE_DECISIONS = {
    "A": {
        "brand": 3.0,
        "perf": 6.0,
        "research": 1.0,
        "positioning": 20.0,
        "emphasis": (0.45, 0.15, 0.05, 0.05, 0.30),
    },
    "B": {
        "brand": 6.0,
        "perf": 2.0,
        "research": 2.0,
        "positioning": 82.0,
        "emphasis": (0.10, 0.20, 0.40, 0.25, 0.05),
    },
    "C": {
        "brand": 4.0,
        "perf": 4.0,
        "research": 2.0,
        "positioning": 52.0,
        "emphasis": (0.25, 0.20, 0.25, 0.15, 0.15),
    },
    "D": {
        "brand": 5.0,
        "perf": 3.0,
        "research": 2.0,
        "positioning": 72.0,
        "emphasis": (0.15, 0.10, 0.30, 0.40, 0.05),
    },
}
GOLDEN_R1 = {
    "derek": (0.2251, 0.4551),
    "marcus": (0.2251, 0.4051),
    "priya": (0.3131, 0.3131),
    "sam": (0.3131, 0.3231),
    "chloe": (0.6025, 0.4225),
    "ava": (0.6025, 0.3725),
    "nia": (0.5262, 0.3762),
    "leo": (0.5262, 0.3862),
    "rosa": (0.3040, 0.3440),
    "tom": (0.3040, 0.3740),
}
GOLDEN_R3_SEGMENT = {
    "LEG": (0.2471, 0.3636),
    "FIT": (0.3220, 0.4062),
    "FUN": (0.6157, 0.5545),
    "GENZ": (0.5306, 0.5141),
    "MAIN": (0.3128, 0.4016),
}


def _history():
    return run_game([deepcopy(BASE_DECISIONS) for _ in range(3)])


def _perception(record):
    return {
        firm: firm_result["perception"]
        for firm, firm_result in record["firms"].items()
    }


def _share(record):
    return {
        firm: firm_result["share"]
        for firm, firm_result in record["firms"].items()
    }


def _by_id(responses):
    return {response["persona_id"]: response for response in responses}


def test_persona_is_frozen():
    with pytest.raises(FrozenInstanceError):
        PERSONAS[0].name = "Changed"


def test_saydo_gap_is_immutable():
    with pytest.raises(TypeError):
        SAYDO_GAP["LEG"] = 0.0


def test_round1_focus_group_golden_revealed_and_stated_values():
    history = _history()
    perception = _perception(history[0])
    responses = _by_id(focus_group(perception, "B", PERSONAS))

    for persona in PERSONAS:
        expected_revealed, expected_stated = GOLDEN_R1[persona.id]
        assert revealed_warmth(perception, "B", persona.segment) == pytest.approx(expected_revealed, abs=0.001)
        assert responses[persona.id]["stated_warmth"] == pytest.approx(expected_stated, abs=0.001)
        assert responses[persona.id]["relationship"] == REL_START


def test_round3_focus_group_golden_revealed_and_entering_relationships():
    history = _history()
    groups = run_focus_groups_over_history(history, "B", PERSONAS)
    round3 = _by_id(groups[2]["responses"])
    round3_perception = _perception(history[2])

    for segment, expected in GOLDEN_R3_SEGMENT.items():
        expected_revealed, expected_relationship = expected
        segment_personas = [persona for persona in PERSONAS if persona.segment == segment]
        for persona in segment_personas:
            assert revealed_warmth(round3_perception, "B", segment) == pytest.approx(expected_revealed, abs=0.001)
            assert round3[persona.id]["relationship"] == pytest.approx(expected_relationship, abs=0.001)


def test_history_helper_public_schema_and_relationship_carry_forward():
    history = _history()
    groups = run_focus_groups_over_history(history, "B", PERSONAS)

    for record in groups:
        assert set(record) == {"round", "responses", "relationships_entering", "relationships_next"}
        assert set(record["relationships_entering"]) == {persona.id for persona in PERSONAS}
        assert set(record["relationships_next"]) == {persona.id for persona in PERSONAS}
        assert record["relationships_entering"] is not record["relationships_next"]
        assert "firm" not in record
        assert "segment" not in record
        assert "revealed_warmth" not in record
        assert "perception" not in record
        assert "share" not in record

    assert all(value == REL_START for value in groups[0]["relationships_entering"].values())
    assert groups[1]["relationships_entering"] == groups[0]["relationships_next"]
    assert groups[2]["relationships_entering"] == groups[1]["relationships_next"]

    for round_index, source_record in enumerate(history):
        entering = groups[round_index]["relationships_entering"]
        expected_next = {
            persona.id: update_relationship(
                entering[persona.id],
                revealed_warmth(_perception(source_record), "B", persona.segment),
            )
            for persona in PERSONAS
        }
        assert groups[round_index]["relationships_next"] == pytest.approx(expected_next)
        for response in groups[round_index]["responses"]:
            assert response["relationship"] == entering[response["persona_id"]]

    for segment, (_, expected_relationship) in GOLDEN_R3_SEGMENT.items():
        for persona in [persona for persona in PERSONAS if persona.segment == segment]:
            assert groups[2]["relationships_entering"][persona.id] == pytest.approx(
                expected_relationship,
                abs=0.001,
            )

    for segment in SEGMENTS:
        segment_personas = [persona for persona in PERSONAS if persona.segment == segment]
        entering_values = {groups[2]["relationships_entering"][persona.id] for persona in segment_personas}
        next_values = {groups[2]["relationships_next"][persona.id] for persona in segment_personas}
        assert len(entering_values) == 1
        assert len(next_values) == 1


def test_say_do_gap_direction_and_formula():
    history = _history()
    perception = _perception(history[0])

    for persona in PERSONAS:
        revealed = revealed_warmth(perception, "B", persona.segment)
        stated = stated_warmth(revealed, persona)
        unclipped_gap = GAP_GAIN * SAYDO_GAP[persona.segment] + persona.idiosyncrasy
        assert stated - revealed == pytest.approx(unclipped_gap)
        if persona.segment == "LEG":
            assert stated > revealed
        if persona.segment in {"FUN", "GENZ"}:
            assert stated < revealed
        if persona.segment == "FIT":
            assert stated - revealed == pytest.approx(persona.idiosyncrasy)

    chloe = next(persona for persona in PERSONAS if persona.id == "chloe")
    derek = next(persona for persona in PERSONAS if persona.id == "derek")
    assert revealed_warmth(perception, "B", "FUN") > 0.5
    assert stated_warmth(revealed_warmth(perception, "B", "FUN"), chloe) < 0.5
    assert revealed_warmth(perception, "B", "LEG") < 0.5
    assert stated_warmth(revealed_warmth(perception, "B", "LEG"), derek) > 0.45


def test_stated_warmth_clips_at_zero_and_one():
    high = Persona("high", "High", "LEG", 0.90)
    low = Persona("low", "Low", "FUN", -0.90)

    assert stated_warmth(0.95, high) == 1.0
    assert stated_warmth(0.05, low) == 0.0


def test_grounding_same_segment_and_mean_firm_cases():
    perception = {
        "A": {"LEG": 10.0},
        "B": {"LEG": 20.0},
        "C": {"LEG": 30.0},
    }

    assert revealed_warmth(perception, "B", "LEG") == 0.5
    assert revealed_warmth(_perception(_history()[0]), "B", "FUN") == revealed_warmth(
        _perception(_history()[0]),
        "B",
        "FUN",
    )
    chloe = next(persona for persona in PERSONAS if persona.id == "chloe")
    ava = next(persona for persona in PERSONAS if persona.id == "ava")
    revealed = revealed_warmth(_perception(_history()[0]), "B", "FUN")
    assert stated_warmth(revealed, chloe) != stated_warmth(revealed, ava)


def test_relationship_updates_start_and_move_with_revealed_warmth():
    history = _history()
    groups = run_focus_groups_over_history(history, "B", PERSONAS)
    leg = next(persona for persona in PERSONAS if persona.segment == "LEG")
    fun = next(persona for persona in PERSONAS if persona.segment == "FUN")

    assert _by_id(groups[0]["responses"])[leg.id]["relationship"] == REL_START
    assert _by_id(groups[0]["responses"])[fun.id]["relationship"] == REL_START
    assert _by_id(groups[2]["responses"])[fun.id]["relationship"] > REL_START
    assert _by_id(groups[2]["responses"])[leg.id]["relationship"] < REL_START

    relationship = REL_START
    for round_index, record in enumerate(history):
        response = _by_id(groups[round_index]["responses"])[fun.id]
        assert response["relationship"] == pytest.approx(relationship)
        relationship = update_relationship(
            relationship,
            revealed_warmth(_perception(record), "B", fun.segment),
        )


def test_student_facing_responses_do_not_leak_hidden_grounding():
    history = _history()
    responses = focus_group(_perception(history[0]), "B", PERSONAS)

    for response in responses:
        assert set(response) == {"mode", "persona_id", "persona_name", "stated_warmth", "relationship", "text"}
        assert "segment" not in response
        assert "revealed_warmth" not in response
        text = response["text"]
        for segment in SEGMENTS:
            assert segment not in text
        assert "0.2251" not in text
        assert "0.6025" not in text
        assert "perception" not in text.lower()
        assert "share" not in text.lower()
        assert str(response["stated_warmth"]) not in text
        assert str(response["relationship"]) not in text


def test_campaign_response_uses_same_student_facing_contract():
    record = _history()[0]
    responses = campaign_response(_perception(record), _share(record), "B", PERSONAS[:2])
    focus = focus_group(_perception(record), "B", PERSONAS[:2])

    assert len(responses) == 2
    assert [response["persona_id"] for response in responses] == [persona.id for persona in PERSONAS[:2]]
    for response, focus_response in zip(responses, focus, strict=True):
        assert response["mode"] == "campaign_response"
        assert set(response) == {"mode", "persona_id", "persona_name", "stated_warmth", "relationship", "text"}
        assert response["stated_warmth"] == focus_response["stated_warmth"]
        assert response["relationship"] == focus_response["relationship"]
        assert response["text"] != focus_response["text"]
        for segment in SEGMENTS:
            assert segment not in response["text"]
        for share in _share(record)["B"].values():
            assert str(share) not in response["text"]
        assert "share" not in response["text"].lower()


def test_campaign_response_validates_share_and_keeps_it_hidden():
    record = _history()[0]
    captured = []

    def generator(grounded):
        captured.append(grounded)
        return f"texture:{grounded['campaign_strength']}"

    responses = campaign_response(_perception(record), _share(record), "B", PERSONAS[:1], generator=generator)

    assert responses[0]["text"].startswith("texture:")
    assert "campaign_strength" not in responses[0]
    assert "campaign_strength" in captured[0]
    assert "share" not in captured[0]


def test_overlay_does_not_mutate_history_or_inputs_and_is_deterministic():
    history = _history()
    original_history = deepcopy(history)
    personas = list(PERSONAS)
    original_personas = deepcopy(personas)

    first = run_focus_groups_over_history(history, "B", personas)
    second = run_focus_groups_over_history(deepcopy(history), "B", deepcopy(personas))
    third = run_focus_groups_over_history(history, "B", personas)

    assert history == original_history
    assert personas == original_personas
    assert first == second == third


def test_focus_group_and_campaign_response_do_not_mutate_inputs_or_relationships():
    record = _history()[0]
    perception = _perception(record)
    share = _share(record)
    relationships = {persona.id: 0.5 for persona in PERSONAS[:2]}
    original_perception = deepcopy(perception)
    original_share = deepcopy(share)
    original_relationships = deepcopy(relationships)

    focus_group(perception, "B", PERSONAS[:2], relationships)
    campaign_response(perception, share, "B", PERSONAS[:2], relationships)

    assert perception == original_perception
    assert share == original_share
    assert relationships == original_relationships


def test_returned_round_and_response_dictionaries_are_independent():
    history = _history()
    groups = run_focus_groups_over_history(history, "B", PERSONAS[:2])
    fresh = run_focus_groups_over_history(history, "B", PERSONAS[:2])
    original_round2_entering = deepcopy(groups[1]["relationships_entering"])

    groups[0]["responses"][0]["stated_warmth"] = -1.0
    groups[0]["responses"][0]["text"] = "changed"
    groups[0]["relationships_entering"]["derek"] = -2.0
    groups[0]["relationships_next"]["derek"] = -3.0

    assert groups[0]["responses"][1]["stated_warmth"] != -1.0
    assert groups[0]["responses"][1]["text"] != "changed"
    assert groups[0]["responses"][0]["relationship"] != -2.0
    assert groups[1]["relationships_entering"] == original_round2_entering
    assert fresh[0]["relationships_entering"]["derek"] == REL_START


def test_mock_generator_is_pure_and_injected_generator_changes_only_text():
    response = {
        "mode": "focus_group",
        "persona_id": "derek",
        "persona_name": "Derek",
        "stated_warmth": 0.45,
        "relationship": 0.5,
    }
    assert mock_generator(response) == mock_generator(deepcopy(response))

    default = focus_group(_perception(_history()[0]), "B", PERSONAS[:1])
    custom = focus_group(_perception(_history()[0]), "B", PERSONAS[:1], generator=lambda grounded: f"custom:{grounded['persona_id']}")

    default_without_text = [{key: value for key, value in item.items() if key != "text"} for item in default]
    custom_without_text = [{key: value for key, value in item.items() if key != "text"} for item in custom]
    assert default_without_text == custom_without_text
    assert default[0]["text"] != custom[0]["text"]
    assert custom[0]["text"] == "custom:derek"


def test_generator_contract_validation():
    record = _history()[0]

    with pytest.raises(ValueError):
        focus_group(_perception(record), "B", PERSONAS[:1], generator="not callable")
    with pytest.raises(ValueError):
        focus_group(_perception(record), "B", PERSONAS[:1], generator=lambda grounded: 123)


def test_open_ended_personas_history_and_target_firm():
    personas = tuple(
        Persona(f"persona-{index}", f"Person {index}", SEGMENTS[index % len(SEGMENTS)], 0.0)
        for index in range(9)
    )
    decisions = []
    teams = ("North", "South", "East", "West", "Central")
    for _ in range(2):
        decisions.append({
            team: {
                "brand": 3.0 + (index % 4),
                "perf": 6.0 - (index % 4),
                "research": 1.0,
                "positioning": 20.0 + index * 12.0,
                "emphasis": (0.20, 0.20, 0.20, 0.20, 0.20),
            }
            for index, team in enumerate(teams)
        })
    history = run_game(decisions)

    groups = run_focus_groups_over_history(history, "Central", personas)
    campaign = campaign_response(_perception(history[0]), _share(history[0]), "Central", personas[:1])

    assert len(groups) == 2
    assert len(groups[0]["responses"]) == 9
    assert set(groups[0]) == {"round", "responses", "relationships_entering", "relationships_next"}
    assert campaign[0]["persona_id"] == "persona-0"


@pytest.mark.parametrize(
    "bad_call",
    [
        lambda: Persona([], "Name", "LEG", 0.0) and focus_group(_perception(_history()[0]), "B", [Persona([], "Name", "LEG", 0.0)]),
        lambda: focus_group(_perception(_history()[0]), "B", [Persona("x", [], "LEG", 0.0)]),
        lambda: focus_group(_perception(_history()[0]), "B", [Persona("x", "X", "LEG", "bad")]),
        lambda: focus_group(_perception(_history()[0]), "B", [Persona("x", "X", "LEG", float("nan"))]),
        lambda: focus_group(_perception(_history()[0]), "B", [Persona("x", "X", "LEG", float("inf"))]),
        lambda: revealed_warmth({}, "B", "LEG"),
        lambda: revealed_warmth([], "B", "LEG"),
        lambda: revealed_warmth({"A": {"LEG": 1.0}}, "B", "LEG"),
        lambda: revealed_warmth({"A": []}, "A", "LEG"),
        lambda: revealed_warmth({"A": {}}, "A", "LEG"),
        lambda: revealed_warmth({"A": {"LEG": float("nan")}}, "A", "LEG"),
        lambda: revealed_warmth({"A": {"LEG": float("inf")}}, "A", "LEG"),
        lambda: revealed_warmth({"A": {"LEG": 1.0}}, "", "LEG"),
        lambda: revealed_warmth({"A": {"LEG": 1.0}}, "A", "BAD"),
        lambda: revealed_warmth({"A": {"LEG": 1.0}}, "A", "LEG", k_percep=-0.1),
        lambda: revealed_warmth({"A": {"LEG": 1.0}}, "A", "LEG", k_percep=float("nan")),
        lambda: stated_warmth(-0.1, PERSONAS[0]),
        lambda: stated_warmth(1.1, PERSONAS[0]),
        lambda: stated_warmth("bad", PERSONAS[0]),
        lambda: stated_warmth(0.5, Persona("x", "X", "BAD", 0.0)),
        lambda: stated_warmth(0.5, PERSONAS[0], gap_gain=-0.1),
        lambda: stated_warmth(0.5, PERSONAS[0], gap_gain=float("inf")),
        lambda: update_relationship(-0.1, 0.5),
        lambda: update_relationship(float("nan"), 0.5),
        lambda: update_relationship(0.5, 1.1),
        lambda: update_relationship(0.5, "bad"),
        lambda: update_relationship(0.5, 0.5, 2.0),
        lambda: campaign_response([], {}, "B", PERSONAS[:1]),
        lambda: campaign_response(_perception(_history()[0]), [], "B", PERSONAS[:1]),
        lambda: campaign_response(_perception(_history()[0]), {}, "B", PERSONAS[:1]),
        lambda: campaign_response(_perception(_history()[0]), {"A": {"LEG": 0.1}}, "B", PERSONAS[:1]),
        lambda: campaign_response(_perception(_history()[0]), {"B": []}, "B", PERSONAS[:1]),
        lambda: campaign_response(_perception(_history()[0]), {"B": {}}, "B", PERSONAS[:1]),
        lambda: campaign_response(_perception(_history()[0]), {"B": {"LEG": "bad"}}, "B", PERSONAS[:1]),
        lambda: campaign_response(_perception(_history()[0]), {"B": {"LEG": float("nan")}}, "B", PERSONAS[:1]),
        lambda: campaign_response(_perception(_history()[0]), {"B": {"LEG": float("inf")}}, "B", PERSONAS[:1]),
        lambda: focus_group(_perception(_history()[0]), "B", []),
        lambda: focus_group(_perception(_history()[0]), "B", [Persona("x", "X", "LEG", 0.0), Persona("x", "Y", "FIT", 0.0)]),
        lambda: focus_group(_perception(_history()[0]), "B", [Persona("", "X", "LEG", 0.0)]),
        lambda: focus_group(_perception(_history()[0]), "B", PERSONAS[:1], relationships=[]),
        lambda: focus_group(_perception(_history()[0]), "B", PERSONAS[:1], relationships={"missing": 0.5}),
        lambda: focus_group(_perception(_history()[0]), "B", PERSONAS[:1], relationships={"derek": "bad"}),
        lambda: focus_group(_perception(_history()[0]), "B", PERSONAS[:1], relationships={"derek": 2.0}),
        lambda: focus_group(_perception(_history()[0]), "B", PERSONAS[:1], relationships={"derek": -0.1}),
        lambda: focus_group(_perception(_history()[0]), "B", PERSONAS[:1], relationships={"derek": float("nan")}),
        lambda: focus_group(_perception(_history()[0]), "B", PERSONAS[:1], relationships={"derek": float("inf")}),
        lambda: run_focus_groups_over_history("not history", "B", PERSONAS),
        lambda: run_focus_groups_over_history([], "B", PERSONAS),
        lambda: run_focus_groups_over_history([1], "B", PERSONAS),
        lambda: run_focus_groups_over_history([{"firms": {}}], "B", PERSONAS),
        lambda: run_focus_groups_over_history([{"round": 1, "firms": {}}, {"round": 1, "firms": {}}], "B", PERSONAS),
        lambda: run_focus_groups_over_history([{"round": 1}], "B", PERSONAS),
        lambda: run_focus_groups_over_history([{"round": 1, "firms": []}], "B", PERSONAS),
        lambda: run_focus_groups_over_history([{"round": 1, "firms": {"B": []}}], "B", PERSONAS),
        lambda: run_focus_groups_over_history([{"round": 1, "firms": {"B": {}}}], "B", PERSONAS),
        lambda: run_focus_groups_over_history([{"round": 1, "firms": {"A": {"perception": {"LEG": 1.0}}}}], "B", PERSONAS[:1]),
    ],
)
def test_customer_agent_validation_errors_raise_value_error(bad_call):
    with pytest.raises(ValueError):
        bad_call()
