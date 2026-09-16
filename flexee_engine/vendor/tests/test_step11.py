from __future__ import annotations

from copy import deepcopy
import inspect

import pytest

import customer_agents
import live_voice
import personas
from customer_agents import Persona, campaign_response, focus_group
from live_voice import (
    MockModelClient,
    POOL_BY_ID,
    band,
    build_persona_prompt,
    build_pool_by_id,
    leakage_scan,
    lean,
    live_persona_generator,
    run_rubric,
)


MARCUS = next(profile for profile in personas.POOL if profile["id"] == "leg_marcus")
SARAH = next(profile for profile in personas.POOL if profile["id"] == "fun_sarah")
GROUNDING = {
    "mode": "focus_group",
    "persona_id": "leg_marcus",
    "persona_name": "Marcus",
    "stated_warmth": 0.455,
    "relationship": 0.50,
}
VALID_JUDGE = '{"direction_fidelity": 0.9, "character": 0.8}'


def test_personas_prerequisite_schema():
    assert isinstance(personas.POOL, list)
    assert len(personas.POOL) == 28
    assert set(MARCUS) == {
        "id",
        "name",
        "segment",
        "idiosyncrasy",
        "honest",
        "age",
        "location",
        "voice_profile",
        "moved_by",
    }
    assert MARCUS["id"] == "leg_marcus"
    assert MARCUS["name"] == "Marcus"
    assert {"age", "location", "voice_profile", "moved_by", "segment"} <= set(MARCUS)


def test_build_pool_by_id_public_helper():
    original = deepcopy(personas.POOL)
    custom_pool = [deepcopy(MARCUS), deepcopy(SARAH)]

    pool = build_pool_by_id(custom_pool)

    assert list(pool) == ["leg_marcus", "fun_sarah"]
    assert pool["leg_marcus"]["moved_by"] == tuple(MARCUS["moved_by"])
    with pytest.raises(TypeError):
        pool["new"] = {}
    with pytest.raises(TypeError):
        pool["leg_marcus"]["name"] = "Other"
    assert personas.POOL == original
    assert custom_pool[0]["moved_by"] == MARCUS["moved_by"]

    with pytest.raises(ValueError):
        build_pool_by_id("not a pool")
    with pytest.raises(ValueError):
        build_pool_by_id([])
    bad = [deepcopy(MARCUS), deepcopy(MARCUS)]
    with pytest.raises(ValueError):
        build_pool_by_id(bad)
    bad = [deepcopy(MARCUS)]
    bad[0]["id"] = ""
    with pytest.raises(ValueError):
        build_pool_by_id(bad)
    bad = [deepcopy(MARCUS)]
    del bad[0]["voice_profile"]
    with pytest.raises(ValueError):
        build_pool_by_id(bad)
    bad = [deepcopy(MARCUS)]
    bad[0]["location"] = ""
    with pytest.raises(ValueError):
        build_pool_by_id(bad)
    bad = [deepcopy(MARCUS)]
    bad[0]["voice_profile"] = ""
    with pytest.raises(ValueError):
        build_pool_by_id(bad)


def test_default_pool_by_id_is_sourced_from_personas():
    assert set(POOL_BY_ID) == {profile["id"] for profile in personas.POOL}
    assert POOL_BY_ID["leg_marcus"]["name"] == "Marcus"
    assert len(POOL_BY_ID) == 28


def test_band_and_lean_goldens_boundaries_and_validation():
    assert band(0.455) == "lukewarm"
    assert band(0.62) == "warm"
    assert band(0.30) == "cool"
    assert band(0.4) == "cool"
    assert band(0.6) == "warm"
    assert lean(0.50) == "neutral toward"
    assert lean(0.60) == "warming toward"
    assert lean(0.40) == "cooling on"
    assert lean(0.45) == "cooling on"
    assert lean(0.55) == "warming toward"

    for bad in (-0.01, 1.01, "many", float("nan"), float("inf"), float("-inf")):
        with pytest.raises(ValueError):
            band(bad)
        with pytest.raises(ValueError):
            lean(bad)


def test_mock_model_client_validation_and_repeat_last():
    with pytest.raises(ValueError):
        MockModelClient([])
    with pytest.raises(ValueError):
        MockModelClient(["ok", 1])

    model = MockModelClient(["first", "second"])
    assert model("s1", "u1", 0.1) == "first"
    assert model("s2", "u2", 0.2) == "second"
    assert model("s3", "u3", 0.3) == "second"
    assert model.calls == [
        {"system": "s1", "user": "u1", "temperature": 0.1},
        {"system": "s2", "user": "u2", "temperature": 0.2},
        {"system": "s3", "user": "u3", "temperature": 0.3},
    ]


def test_build_persona_prompt_sanitizes_hidden_values_digits_and_competitors():
    original = deepcopy(MARCUS)

    system, user = build_persona_prompt(MARCUS, 0.455, 0.50)
    text = system + "\n" + user

    assert "Marcus" in text
    assert "lukewarm" in text
    assert "neutral toward" in text
    assert "Legacy" not in text
    assert "LEG" not in text.split()
    assert "0.455" not in text
    assert "0.5" not in text
    assert not any(char.isdigit() for char in text)
    for competitor in ("BigRed", "Monssta", "Pulse", "Alani", "Celsius", "Monster"):
        assert competitor not in text
    assert MARCUS == original
    assert build_persona_prompt(MARCUS, 0.455, 0.50) == (system, user)


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Torque's still my drink, always has been.", []),
        (
            "As a Legacy Core guy, I'm loyal.",
            [("segment_label", "Legacy"), ("segment_label", "Legacy Core")],
        ),
        ("My segment is FUN so I want wellness.", [("segment_code", "FUN")]),
        ("BigRed is better honestly.", [("competitor", "BigRed")]),
        ("I've got the top market share in mind.", [("scoreboard", "market share")]),
        ("I drink two or three cans a day, same as always.", []),
    ],
)
def test_leakage_scan_goldens(text, expected):
    assert leakage_scan(text, "LEG") == expected


def test_leakage_scan_precision():
    assert leakage_scan("legacy core still sounds like me.", "LEG") == [
        ("segment_label", "Legacy"),
        ("segment_label", "Legacy Core"),
    ]
    assert leakage_scan("fit and fun are ordinary lowercase words here.", "LEG") == []
    assert leakage_scan("Fitness people may like it.", "LEG") == []
    assert leakage_scan("pulse has a loud shelf today.", "LEG") == [("competitor", "Pulse")]
    assert leakage_scan("That SCOREBOARD wording is awkward.", "LEG") == [("scoreboard", "scoreboard")]
    assert leakage_scan("The elegant option is fine.", "LEG") == []


def test_live_generator_default_signature_custom_pool_and_independent_metadata():
    model_a = MockModelClient("Marcus says the brand feels familiar and Marcus is talking it through.")
    model_b = MockModelClient("Sarah says the brand feels useful and Sarah is still weighing trust.")
    generator_a = live_persona_generator(model_a)
    custom_pool = build_pool_by_id([deepcopy(SARAH)])
    generator_b = live_persona_generator(model_b, custom_pool)

    assert generator_a(dict(GROUNDING)).startswith("Marcus")
    assert generator_b({
        "mode": "focus_group",
        "persona_id": "fun_sarah",
        "persona_name": "Sarah",
        "stated_warmth": 0.7,
        "relationship": 0.6,
    }).startswith("Sarah")
    assert generator_a.leak_events == []
    assert generator_b.leak_events == []
    assert generator_a.leak_events is not generator_b.leak_events
    assert generator_a.last_attempts is not generator_b.last_attempts


@pytest.mark.parametrize("persona_name", ["Wrong Name", "Legacy Core", "BigRed"])
def test_live_generator_rejects_grounded_persona_name_mismatch_before_model_call(persona_name):
    model = MockModelClient("This should not be called.")
    generator = live_persona_generator(model)

    with pytest.raises(ValueError):
        generator(dict(GROUNDING, persona_name=persona_name))

    assert model.calls == []
    assert generator.leak_events == []
    assert generator.last_attempts == []


def test_live_generator_drop_in_focus_group_and_campaign_response():
    model = MockModelClient([
        "Marcus says the brand feels familiar and Marcus is talking it through.",
        "Sarah says the campaign feels credible and Sarah wants more clarity.",
    ])
    generator = live_persona_generator(model)
    persona_list = [
        Persona("leg_marcus", "Marcus", "LEG", 0.03),
        Persona("fun_sarah", "Sarah", "FUN", 0.0),
    ]
    perception = {
        "Torque": {"LEG": 70.0, "FUN": 75.0},
        "Backdrop": {"LEG": 50.0, "FUN": 60.0},
    }
    share = {
        "Torque": {"LEG": 0.4, "FUN": 0.5},
        "Backdrop": {"LEG": 0.6, "FUN": 0.5},
    }

    focus = focus_group(perception, "Torque", persona_list[:1], generator=generator)
    campaign = campaign_response(perception, share, "Torque", persona_list[1:], generator=generator)

    assert focus[0]["text"].startswith("Marcus")
    assert campaign[0]["text"].startswith("Sarah")
    assert customer_agents.mock_generator is customer_agents.mock_generator


def test_live_generator_leak_then_clean_metadata():
    model = MockModelClient([
        "As a Legacy Core guy, I am loyal.",
        "Marcus says the brand still feels familiar and Marcus is listening.",
    ])
    generator = live_persona_generator(model)

    text = generator(dict(GROUNDING))

    assert text.startswith("Marcus")
    assert len(model.calls) == 2
    assert generator.leak_events == [{
        "persona_id": "leg_marcus",
        "attempt": 1,
        "findings": (("segment_label", "Legacy"), ("segment_label", "Legacy Core")),
        "fallback_used": False,
    }]
    assert [attempt["attempt"] for attempt in generator.last_attempts] == [1, 2]
    assert generator.last_attempts[1]["clean"] is True


def test_live_generator_retries_once_and_falls_back_without_leaking():
    model = MockModelClient([
        "As a Legacy Core guy, I am loyal.",
        "LEG is the code I care about.",
    ])
    generator = live_persona_generator(model)

    text = generator(dict(GROUNDING))

    assert text == customer_agents.mock_generator(GROUNDING)
    assert leakage_scan(text, "LEG") == []
    assert len(model.calls) == 2
    assert len(generator.leak_events) == 2
    assert generator.leak_events[-1]["fallback_used"] is True
    assert generator.last_attempts[-1]["source"] == "fallback"


def test_fallback_leak_uses_fixed_safe_response():
    model = MockModelClient(["Legacy Core forever.", "LEG forever."])
    generator = live_persona_generator(
        model,
        fallback=lambda response: "Marcus says BigRed and Legacy Core out loud.",
    )

    text = generator(dict(GROUNDING))

    assert text == "Marcus is still sorting out how the brand feels to them."
    assert leakage_scan(text, "LEG") == []
    assert generator.last_attempts[-1]["source"] == "fixed_safe"
    assert generator.last_attempts[-1]["fixed_safe_response_used"] is True


def test_unsafe_grounded_name_rejected_and_leaking_custom_fallback_fixed_safely():
    unsafe = dict(GROUNDING, persona_name="Legacy Core")
    rejected_generator = live_persona_generator(MockModelClient(["Legacy Core", "LEG"]))
    with pytest.raises(ValueError):
        rejected_generator(unsafe)

    model = MockModelClient(["Legacy Core forever.", "LEG forever."])
    generator = live_persona_generator(
        model,
        fallback=lambda response: "Marcus says BigRed and Legacy Core out loud.",
    )

    text = generator(dict(GROUNDING))

    assert len(model.calls) == 2
    assert text == "Marcus is still sorting out how the brand feels to them."
    assert leakage_scan(text, "LEG") == []
    assert generator.leak_events[-1]["fallback_used"] is True
    assert generator.last_attempts[-1]["source"] == "fixed_safe"
    assert generator.last_attempts[-1]["fixed_safe_response_used"] is True
    assert generator.last_attempts[-1]["findings"]


def test_run_rubric_schema_clean_leak_then_clean_and_fallback_cases():
    clean_generator = live_persona_generator(
        MockModelClient("Marcus says the brand feels familiar, and Marcus is still thinking it over.")
    )
    clean_report = run_rubric(clean_generator, [{"case_id": "clean", "persona_id": "leg_marcus"}])

    assert clean_report == {
        "cases": [{
            "case_id": "clean",
            "persona_id": "leg_marcus",
            "text": "Marcus says the brand feels familiar, and Marcus is still thinking it over.",
            "deterministic": {
                "leakage": [],
                "no_leakage": True,
                "in_character": True,
                "leak_guard_triggered": False,
                "fallback_used": False,
            },
            "judge": None,
        }],
        "n_cases": 1,
        "deterministic_passes": 1,
        "deterministic_failures": 0,
        "leak_guard_activations": 0,
        "fallback_uses": 0,
        "judge_averages": None,
        "regressions": (),
    }

    retry_generator = live_persona_generator(MockModelClient([
        "Legacy Core mention.",
        "Marcus says the brand is familiar and Marcus is still open.",
    ]))
    retry_report = run_rubric(retry_generator, [{"case_id": "retry", "persona_id": "leg_marcus"}])
    assert retry_report["cases"][0]["deterministic"] == {
        "leakage": [],
        "no_leakage": True,
        "in_character": True,
        "leak_guard_triggered": True,
        "fallback_used": False,
    }
    assert retry_report["regressions"] == (("retry", "leak_guard_triggered"),)

    fallback_generator = live_persona_generator(MockModelClient(["Legacy Core", "LEG"]))
    fallback_report = run_rubric(fallback_generator, [{"case_id": "fallback", "persona_id": "leg_marcus"}])
    assert fallback_report["cases"][0]["deterministic"] == {
        "leakage": [],
        "no_leakage": True,
        "in_character": True,
        "leak_guard_triggered": True,
        "fallback_used": True,
    }
    assert fallback_report["regressions"] == (
        ("fallback", "leak_guard_triggered"),
        ("fallback", "fallback_used"),
    )


def test_rubric_case_event_slicing_and_many_cases_without_judge():
    generator = live_persona_generator(
        MockModelClient([f"Marcus says case {index} and Marcus is clear." for index in range(9)])
    )
    cases = [{"case_id": f"case-{index}", "persona_id": "leg_marcus"} for index in range(9)]

    report = run_rubric(generator, cases)

    assert report["n_cases"] == 9
    assert report["deterministic_passes"] == 9
    assert all(not case["deterministic"]["leak_guard_triggered"] for case in report["cases"])


def test_judge_json_parsing_and_averages():
    generator = live_persona_generator(
        MockModelClient("Marcus says the brand feels familiar, and Marcus is still thinking it over.")
    )
    judge = MockModelClient(VALID_JUDGE)

    report = run_rubric(generator, [{"case_id": "judged", "persona_id": "leg_marcus"}], judge)

    assert judge.calls
    judge_prompt = judge.calls[0]["system"] + "\n" + judge.calls[0]["user"]
    assert "JSON" in judge_prompt
    assert "LEG" not in judge_prompt.split()
    assert "Legacy" not in judge_prompt
    assert "0.455" not in judge_prompt
    assert report["cases"][0]["judge"] == {
        "direction_fidelity": 0.9,
        "character": 0.8,
        "raw": VALID_JUDGE,
    }
    assert report["judge_averages"] == {"direction_fidelity": 0.9, "character": 0.8}


def test_run_rubric_uses_generator_custom_pool_and_rejects_hidden_segment_mismatch():
    custom_profile = {
        "id": "custom_alex",
        "name": "Alex",
        "segment": "FIT",
        "idiosyncrasy": 0.0,
        "honest": True,
        "age": 28,
        "location": "Austin",
        "voice_profile": "Careful shopper who wants a straightforward product.",
        "moved_by": ["clarity", "function"],
    }
    custom_pool = build_pool_by_id([custom_profile])
    generator = live_persona_generator(
        MockModelClient("Alex says the brand feels useful and Alex is paying attention."),
        custom_pool,
    )

    report = run_rubric(generator, [{"case_id": "custom", "persona_id": "custom_alex"}])

    assert "custom_alex" not in POOL_BY_ID
    assert report["cases"][0]["persona_id"] == "custom_alex"
    assert report["cases"][0]["deterministic"] == {
        "leakage": [],
        "no_leakage": True,
        "in_character": True,
        "leak_guard_triggered": False,
        "fallback_used": False,
    }

    with pytest.raises(ValueError):
        run_rubric(generator, [{"case_id": "bad-segment", "persona_id": "custom_alex", "hidden_segment": "LEG"}])


@pytest.mark.parametrize(
    "response",
    [
        "not json",
        "[]",
        '{"direction_fidelity": 0.9}',
        '{"direction_fidelity": "high", "character": 0.8}',
        '{"direction_fidelity": NaN, "character": 0.8}',
        '{"direction_fidelity": 1.1, "character": 0.8}',
    ],
)
def test_invalid_judge_responses_raise_value_error(response):
    generator = live_persona_generator(
        MockModelClient("Marcus says the brand feels familiar, and Marcus is still thinking it over.")
    )
    with pytest.raises(ValueError):
        run_rubric(generator, [{"case_id": "bad-judge", "persona_id": "leg_marcus"}], MockModelClient(response))


def test_rubric_validation_and_no_mutation():
    original_case = {"case_id": "case", "persona_id": "leg_marcus", "stated_warmth": 0.5}
    case_copy = deepcopy(original_case)
    generator = live_persona_generator(MockModelClient("Marcus says the brand is okay and Marcus is present."))

    with pytest.raises(ValueError):
        run_rubric("not callable", [original_case])
    with pytest.raises(ValueError):
        run_rubric(generator, "not cases")
    with pytest.raises(ValueError):
        run_rubric(generator, [])
    with pytest.raises(ValueError):
        run_rubric(generator, [1])
    with pytest.raises(ValueError):
        run_rubric(generator, [{"persona_id": "leg_marcus"}])
    with pytest.raises(ValueError):
        run_rubric(generator, [{"case_id": "x", "persona_id": "leg_marcus"}, {"case_id": "x", "persona_id": "leg_marcus"}])
    with pytest.raises(ValueError):
        run_rubric(generator, [{"case_id": "x", "grounded_response": {"persona_id": ""}}])
    with pytest.raises(ValueError):
        run_rubric(generator, [{"case_id": "x", "persona_id": "unknown"}])
    with pytest.raises(ValueError):
        run_rubric(generator, [original_case], judge_client="not callable")

    run_rubric(generator, [original_case])
    assert original_case == case_copy


def test_validation_and_no_persona_pool_mutation():
    original_pool = deepcopy(personas.POOL)
    with pytest.raises(ValueError):
        live_persona_generator(lambda *_: "x", {})
    with pytest.raises(ValueError):
        build_persona_prompt({"id": "x"}, 0.5, 0.5)
    with pytest.raises(ValueError):
        leakage_scan("x", "NOPE")
    with pytest.raises(ValueError):
        live_persona_generator(lambda *_: "x")(dict(GROUNDING, persona_id="unknown"))

    assert personas.POOL == original_pool


def test_architecture_boundary():
    source = inspect.getsource(live_voice)
    forbidden = (
        "run_game",
        "compute_round",
        "compute_scorecard",
        "requests",
        "urllib",
        "open(",
        "random",
    )
    for token in forbidden:
        assert token not in source
