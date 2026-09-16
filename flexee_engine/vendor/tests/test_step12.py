from __future__ import annotations

from copy import deepcopy
import inspect
from types import MappingProxyType

import pytest

import advisor_voice
import advisors
from advisor_voice import (
    ADVISOR_PROFILES,
    MockModelClient,
    build_advisor_profiles,
    build_advisor_prompt,
    guardrail_scan,
    live_advisor_generator,
    run_advisor_rubric,
    run_rubric,
    strength,
)
from advisors import advise, evaluate_stances
from test_step9 import FUNCTIONAL_PIVOT, _context


STANCE = {
    "advisor_id": "elena",
    "advisor_name": "Elena Vasquez",
    "valence": "oppose",
    "intensity": 0.9,
    "rationale_tags": ("equity_at_risk_promotion", "positioning_incoherent"),
}
GOLDEN_VALENCES = {
    "elena": "oppose",
    "ray": "support",
    "jax": "support",
    "anita": "oppose",
    "bud": "support",
    "tom": "oppose",
}


def _functional_stances():
    return evaluate_stances(FUNCTIONAL_PIVOT, _context())


def _hook_payloads():
    seen = []

    def capture(stance):
        seen.append(dict(stance))
        return f"{stance['advisor_name']} says the final call remains with the CMO."

    public = advise(FUNCTIONAL_PIVOT, _context(), generator=capture)
    return public, seen


@pytest.mark.parametrize(
    "value,expected",
    [
        (0.9, "hard"),
        (0.5, "firmly"),
        (0.2, "lightly"),
        (0.25, "firmly"),
        (0.6, "hard"),
    ],
)
def test_strength_goldens(value, expected):
    assert strength(value) == expected


@pytest.mark.parametrize("bad", [-0.01, 1.01, "many", float("nan"), float("inf"), float("-inf")])
def test_strength_invalid_numeric_paths(bad):
    with pytest.raises(ValueError):
        strength(bad)


def test_build_advisor_prompt_locked_signature_and_elena_golden():
    assert str(inspect.signature(build_advisor_prompt)) == (
        "(profile, valence, intensity, rationale_tags) -> 'tuple[str, str]'"
    )
    system, user = build_advisor_prompt(
        ADVISOR_PROFILES["elena"],
        "oppose",
        0.9,
        ("equity_at_risk_promotion", "positioning_incoherent"),
    )
    combined = system + "\n" + user

    assert "Elena Vasquez" in combined
    assert "Brand Strategist" in combined
    assert "argue AGAINST" in combined
    assert "hard" in combined
    assert "equity and promotion" in combined
    assert "positioning" in combined
    assert "You advise; you do not decide." in combined
    assert "The CMO owns the call." in combined
    assert "Leave the final decision to the CMO." in combined
    assert "Never claim the decision is yours or already made." in combined
    assert "Stay in character." in combined
    assert "One or two sentences." in combined
    assert "Never quote an exact score, share, percentile, or ranking." in combined
    assert "Never reference the anonymized scoreboard or other teams." in combined
    assert "Never reveal competitor internal numbers." in combined
    assert not any(char.isdigit() for char in combined)
    assert "I decided" not in combined
    assert "My decision" not in combined


def test_build_advisor_prompt_valence_mapping_and_validation():
    support = "\n".join(build_advisor_prompt(ADVISOR_PROFILES["ray"], "support", 0.7, ("measurable_response",)))
    mixed = "\n".join(build_advisor_prompt(ADVISOR_PROFILES["tom"], "mixed", 0.5, ("trust_watch",)))
    assert "argue FOR" in support
    assert "give a mixed read" in mixed
    bad_profile = dict(ADVISOR_PROFILES["elena"], name="")
    with pytest.raises(ValueError):
        build_advisor_prompt(bad_profile, "oppose", 0.9, ("brand_coherence",))
    with pytest.raises(ValueError):
        build_advisor_prompt(ADVISOR_PROFILES["elena"], "bad", 0.9, ("brand_coherence",))
    with pytest.raises(ValueError):
        build_advisor_prompt(ADVISOR_PROFILES["elena"], "oppose", 1.2, ("brand_coherence",))
    with pytest.raises(ValueError):
        build_advisor_prompt(ADVISOR_PROFILES["elena"], "oppose", 0.9, ())


@pytest.mark.parametrize(
    "text,expected",
    [
        ("The equity's the crown jewel — I'd push back on chasing this. Your call.", []),
        ("I decided we're not doing this.", [("ownership_claim", "i decided")]),
        ("My decision is final on this one.", [("ownership_claim", "my decision")]),
        ("Honestly BigRed would never risk it.", [("competitor", "BigRed")]),
        (
            "You're behind on the anonymized scoreboard, so move.",
            [("scoreboard", "scoreboard"), ("scoreboard", "anonymized")],
        ),
        ("I'd argue hard against it, but you own the call.", []),
        ("My read scores this at -0.577 frankly.", [("hidden_score", "-0.577")]),
    ],
)
def test_guardrail_scan_locked_goldens(text, expected):
    hidden_score = -0.577 if "-0.577" in text else None
    assert guardrail_scan(text, hidden_score) == expected


def test_guardrail_scan_precision_cases():
    assert guardrail_scan("I DECIDED this.", None) == [("ownership_claim", "i decided")]
    assert guardrail_scan("This is ranked-choice voting.", None) == []
    assert guardrail_scan("pulse feels louder today.", None) == [("competitor", "Pulse")]
    assert guardrail_scan("advisor_id rationale_tags discount_pressure", None) == []
    assert guardrail_scan("My read scores this at -0.58 frankly.", -0.577) == [("hidden_score", "-0.58")]
    assert guardrail_scan("My read scores this at -0.6 frankly.", -0.577) == [("hidden_score", "-0.6")]
    assert guardrail_scan("My read scores this at -0.5779 frankly.", -0.577) == []
    assert guardrail_scan("My read scores this at -0.577 frankly.") == []
    with pytest.raises(ValueError):
        guardrail_scan(123)
    with pytest.raises(ValueError):
        guardrail_scan("ok", float("nan"))


def test_build_advisor_profiles_immutable_custom_profiles_and_validation():
    source = {"solo": {"name": "Solo One", "role": "Coach", "mandate": "advise clearly", "manner": "calm"}}
    profiles = build_advisor_profiles(source)

    assert isinstance(profiles, MappingProxyType)
    assert isinstance(profiles["solo"], MappingProxyType)
    assert profiles["solo"]["name"] == "Solo One"
    with pytest.raises(TypeError):
        profiles["new"] = {}
    with pytest.raises(TypeError):
        profiles["solo"]["name"] = "Other"
    assert source["solo"]["name"] == "Solo One"

    malformed = [
        {},
        {"": source["solo"]},
        {"solo": []},
        {"solo": dict(source["solo"], name="")},
        {"solo": dict(source["solo"], role="")},
        {"solo": dict(source["solo"], mandate="")},
        {"solo": dict(source["solo"], manner="")},
    ]
    for bad in malformed:
        with pytest.raises(ValueError):
            build_advisor_profiles(bad)


def test_existing_advise_generator_hook_contract_is_unchanged_and_scoreless():
    public, payloads = _hook_payloads()

    assert str(inspect.signature(advise)) == (
        "(decision_under_review: 'Mapping[str, Any]', context: 'Mapping[str, Any]', "
        "generator: 'TextGenerator | None' = None) -> 'list[dict[str, object]]'"
    )
    assert len(public) == 6
    assert len(payloads) == 6
    for payload in payloads:
        assert set(payload) == {
            "advisor_id",
            "advisor_name",
            "valence",
            "intensity",
            "rationale_tags",
        }
        assert "score" not in payload
    assert advisors.mock_generator(STANCE) == (
        "Elena Vasquez pushes back because brand equity may be strained; the call remains yours."
    )
    assert advise(FUNCTIONAL_PIVOT, _context())[0]["text"] == advisors.mock_generator(payloads[0])


def test_live_advisor_generator_clean_model_response():
    model = MockModelClient("Elena Vasquez would push back here; the final call remains with the CMO.")
    generator = live_advisor_generator(model)

    text = generator(dict(STANCE))

    assert text == "Elena Vasquez would push back here; the final call remains with the CMO."
    assert guardrail_scan(text) == []
    assert len(model.calls) == 1
    assert generator.guardrail_events == []
    assert generator.last_attempts[-1]["attempt"] == 1
    assert generator.last_attempts[-1]["clean"] is True


def test_live_advisor_generator_retry_then_clean():
    model = MockModelClient([
        "I decided this one already.",
        "Elena Vasquez would push back; the final call remains with the CMO.",
    ])
    generator = live_advisor_generator(model)

    text = generator(dict(STANCE))

    assert text.endswith("CMO.")
    assert len(model.calls) == 2
    assert generator.guardrail_events == [{
        "advisor_id": "elena",
        "attempt": 1,
        "findings": (("ownership_claim", "i decided"),),
        "fallback_used": False,
    }]
    assert [attempt["attempt"] for attempt in generator.last_attempts] == [1, 2]


def test_live_advisor_generator_retry_then_default_fallback():
    model = MockModelClient("I decided this one already.")
    generator = live_advisor_generator(model)

    text = generator(dict(STANCE))

    assert text == advisors.mock_generator(STANCE)
    assert len(model.calls) == 2
    assert generator.guardrail_events[-1]["fallback_used"] is True
    assert generator.last_attempts[-1]["attempt"] == "fallback"
    assert generator.last_attempts[-1]["fallback_used"] is True
    assert guardrail_scan(text) == []


def test_live_advisor_generator_unsafe_custom_fallback_uses_fixed_safe():
    model = MockModelClient("I decided this one already.")

    def bad_fallback(_stance):
        return "My decision is final on this one."

    generator = live_advisor_generator(model, fallback=bad_fallback)

    text = generator(dict(STANCE))

    assert text == "Elena Vasquez is still weighing the advice; the final call remains with the CMO."
    assert len(model.calls) == 2
    assert guardrail_scan(text) == []
    assert [attempt["attempt"] for attempt in generator.last_attempts] == [1, 2, "fallback", "fixed-safe"]
    assert generator.last_attempts[-2]["findings"] == (("ownership_claim", "my decision"),)
    assert generator.last_attempts[-1]["fixed_safe_response_used"] is True


def test_live_advisor_generator_final_constant_path_is_scanned(monkeypatch):
    model = MockModelClient("I decided this one already.")

    def bad_fallback(_stance):
        return "My decision is final on this one."

    original = advisor_voice.guardrail_scan

    def rigged_scan(text, hidden_score=None):
        if text == "Elena Vasquez is still weighing the advice; the final call remains with the CMO.":
            return [("ownership_claim", "rigged")]
        return original(text, hidden_score)

    monkeypatch.setattr(advisor_voice, "guardrail_scan", rigged_scan)
    generator = live_advisor_generator(model, fallback=bad_fallback)

    text = generator(dict(STANCE))

    assert text == "The final call remains with the CMO."
    assert generator.last_attempts[-1]["attempt"] == "final-constant"
    assert generator.last_attempts[-1]["final_constant_used"] is True


@pytest.mark.parametrize("advisor_name", ["Wrong Name", "BigRed", "I decided"])
def test_live_advisor_generator_rejects_identity_injection_before_model_call(advisor_name):
    model = MockModelClient("Should not be called.")
    generator = live_advisor_generator(model)
    with pytest.raises(ValueError):
        generator(dict(STANCE, advisor_name=advisor_name))
    assert model.calls == []


def test_live_advisor_generator_side_channel_independence_and_custom_profiles():
    custom_profiles = build_advisor_profiles({
        "solo": {"name": "Solo One", "role": "Coach", "mandate": "advise clearly", "manner": "calm"},
    })
    stance = {
        "advisor_id": "solo",
        "advisor_name": "Solo One",
        "valence": "mixed",
        "intensity": 0.5,
        "rationale_tags": ("custom_tag",),
    }
    generator_a = live_advisor_generator(MockModelClient("Solo One has a mixed read; the final call remains with the CMO."))
    generator_b = live_advisor_generator(
        MockModelClient("Solo One has a mixed read; the final call remains with the CMO."),
        profiles=custom_profiles,
    )

    assert generator_b(stance).startswith("Solo One")
    with pytest.raises(ValueError):
        generator_a(stance)
    assert generator_a.guardrail_events is not generator_b.guardrail_events
    assert generator_a.last_attempts is not generator_b.last_attempts
    assert generator_b.profiles["solo"]["name"] == "Solo One"


def test_live_advisor_generator_rejects_bad_model_fallback_and_payloads():
    with pytest.raises(ValueError):
        live_advisor_generator("not callable")
    with pytest.raises(ValueError):
        live_advisor_generator(MockModelClient("ok"), fallback="not callable")
    with pytest.raises(ValueError):
        live_advisor_generator(MockModelClient("ok"), profiles={})
    with pytest.raises(ValueError):
        live_advisor_generator(lambda *_args: 42)(dict(STANCE))
    with pytest.raises(ValueError):
        live_advisor_generator(MockModelClient("I decided."), fallback=lambda _stance: 42)(dict(STANCE))
    bad_stances = [
        {},
        {**STANCE, "score": 1.0},
        {**STANCE, "advisor_id": "unknown"},
        {**STANCE, "valence": "yes"},
        {**STANCE, "intensity": -0.1},
        {**STANCE, "intensity": float("nan")},
        {**STANCE, "rationale_tags": ()},
        {**STANCE, "rationale_tags": "discount_pressure"},
    ]
    for bad in bad_stances:
        with pytest.raises(ValueError):
            live_advisor_generator(MockModelClient("Should not call."))(bad)


def test_advise_integration_preserves_public_shape_and_values():
    model = MockModelClient("Elena Vasquez says the final call remains with the CMO.")
    generator = live_advisor_generator(model)
    public = advise(FUNCTIONAL_PIVOT, _context(), generator=generator)
    default_public = advise(FUNCTIONAL_PIVOT, _context())

    assert len(public) == 6
    assert len(model.calls) == 6
    for live_stance, default_stance in zip(public, default_public, strict=True):
        assert set(live_stance) == {"advisor_id", "advisor_name", "valence", "intensity", "text"}
        assert "score" not in live_stance
        assert "rationale_tags" not in live_stance
        assert live_stance["valence"] == default_stance["valence"]
        assert live_stance["intensity"] == default_stance["intensity"]
        assert isinstance(live_stance["text"], str)


def test_golden_six_advisor_rubric_clean_set():
    stances = _functional_stances()
    assert {stance["advisor_id"]: stance["valence"] for stance in stances} == GOLDEN_VALENCES
    responses = [
        f"{stance['advisor_name']} says the final call remains with the CMO."
        for stance in stances
    ]
    generator = live_advisor_generator(MockModelClient(responses))

    report = run_advisor_rubric(generator, stances)

    assert len(report["stances"]) == 6
    assert report["n_stances"] == 6
    assert report["deterministic_passes"] == 6
    assert report["deterministic_failures"] == 0
    assert report["guard_activations"] == 0
    assert report["fallback_uses"] == 0
    assert report["input_has_support"] is True
    assert report["input_has_oppose"] is True
    assert report["visible_disagreement_preserved"] is True
    assert report["judge_averages"] is None
    assert report["regressions"] == ()
    assert [record["valence"] for record in report["stances"]].count("support") == 3
    assert [record["valence"] for record in report["stances"]].count("oppose") == 3
    assert all(record["judge"] is None for record in report["stances"])
    assert all(record["deterministic"]["guardrails_clean"] for record in report["stances"])
    assert all(record["deterministic"]["in_character"] for record in report["stances"])


def test_rubric_ownership_leak_then_clean_regression_flag():
    generator = live_advisor_generator(MockModelClient([
        "I decided we are not doing this.",
        "Elena Vasquez would push back; the final call remains with the CMO.",
    ]))

    report = run_advisor_rubric(generator, [dict(STANCE)])

    assert report["stances"][0]["text"].endswith("CMO.")
    assert report["stances"][0]["deterministic"]["guard_triggered"] is True
    assert report["stances"][0]["deterministic"]["fallback_used"] is False
    assert ("elena", "guard_triggered") in report["regressions"]
    assert ("elena", "fallback_used") not in report["regressions"]


def test_rubric_ownership_leak_twice_then_fallback_regression_flags():
    generator = live_advisor_generator(MockModelClient("I decided we are not doing this."))

    report = run_advisor_rubric(generator, [dict(STANCE)])

    assert report["stances"][0]["text"] == advisors.mock_generator(STANCE)
    assert report["stances"][0]["deterministic"]["guardrails_clean"] is True
    assert report["stances"][0]["deterministic"]["guard_triggered"] is True
    assert report["stances"][0]["deterministic"]["fallback_used"] is True
    assert ("elena", "guard_triggered") in report["regressions"]
    assert ("elena", "fallback_used") in report["regressions"]


@pytest.mark.parametrize(
    "selected,expected_support,expected_oppose,expected_visible",
    [
        (["ray"], True, False, True),
        (["elena"], False, True, True),
        (["tom"], False, True, True),
        (["ray", "elena"], True, True, True),
    ],
)
def test_rubric_one_advisor_support_only_oppose_only_and_mixed_sets(
    selected,
    expected_support,
    expected_oppose,
    expected_visible,
):
    source = [stance for stance in _functional_stances() if stance["advisor_id"] in selected]
    generator = live_advisor_generator(MockModelClient([
        f"{stance['advisor_name']} says the final call remains with the CMO."
        for stance in source
    ]))

    report = run_advisor_rubric(generator, source)

    assert report["input_has_support"] is expected_support
    assert report["input_has_oppose"] is expected_oppose
    assert report["visible_disagreement_preserved"] is expected_visible
    assert report["n_stances"] == len(selected)


def test_run_rubric_alias_and_rubric_validation():
    generator = live_advisor_generator(MockModelClient("Elena Vasquez says the final call remains with the CMO."))
    assert run_rubric(generator, [dict(STANCE)])["n_stances"] == 1
    with pytest.raises(ValueError):
        run_advisor_rubric("not callable", [dict(STANCE)])
    with pytest.raises(ValueError):
        run_advisor_rubric(generator, [])
    with pytest.raises(ValueError):
        run_advisor_rubric(generator, "not stances")
    with pytest.raises(ValueError):
        run_advisor_rubric(generator, [dict(STANCE), dict(STANCE)])
    with pytest.raises(ValueError):
        run_advisor_rubric(generator, [dict(STANCE)], judge_client="not callable")
    with pytest.raises(ValueError):
        run_advisor_rubric(lambda _stance: 42, [dict(STANCE)])


def test_valid_judge_contract_and_parsing_example():
    generator = live_advisor_generator(MockModelClient("Elena Vasquez says the final call remains with the CMO."))
    judge = MockModelClient('{"direction_fidelity": 0.9, "character": 0.8}')

    report = run_advisor_rubric(generator, [dict(STANCE)], judge)

    assert report["stances"][0]["judge"] == {
        "direction_fidelity": 0.9,
        "character": 0.8,
        "raw": '{"direction_fidelity": 0.9, "character": 0.8}',
    }
    assert report["judge_averages"] == {"direction_fidelity": 0.9, "character": 0.8}
    call = judge.calls[0]
    assert "Elena Vasquez" in call["user"]
    assert "Brand Strategist" in call["user"]
    assert "oppose" in call["user"]
    assert "final call remains with the CMO" in call["user"]
    assert "internal score" not in call["user"].lower()
    assert "0.9" not in call["user"]
    assert "engine" not in call["user"].lower()
    assert "competitor internal" not in call["user"].lower()


@pytest.mark.parametrize(
    "judge_response",
    [
        "not json",
        "[]",
        "{}",
        '{"direction_fidelity": 0.9}',
        '{"direction_fidelity": "good", "character": 0.8}',
        '{"direction_fidelity": 0.9, "character": 1.2}',
        '{"direction_fidelity": NaN, "character": 0.8}',
        '{"direction_fidelity": 0.9, "character": 0.8, "extra": 1}',
    ],
)
def test_malformed_judge_paths_raise(judge_response):
    generator = live_advisor_generator(MockModelClient("Elena Vasquez says the final call remains with the CMO."))
    with pytest.raises(ValueError):
        run_advisor_rubric(generator, [dict(STANCE)], MockModelClient(judge_response))


def test_purity_and_non_mutation():
    stance = dict(STANCE)
    original_stance = deepcopy(stance)
    profiles_source = {
        "elena": {
            "name": "Elena Vasquez",
            "role": "Brand Strategist",
            "mandate": "protect brand equity",
            "manner": "measured",
        }
    }
    original_profiles = deepcopy(profiles_source)
    model = MockModelClient("Elena Vasquez says the final call remains with the CMO.")
    generator = live_advisor_generator(model, profiles_source)

    first = generator(stance)
    second = generator(deepcopy(stance))

    assert first == second
    assert stance == original_stance
    assert profiles_source == original_profiles


def test_advisor_voice_architecture_boundary_and_imports():
    source = inspect.getsource(advisor_voice)
    assert "from live_voice import MockModelClient, ModelClient" in source
    assert "class MockModelClient" not in source
    assert "class ModelClient" not in source
    forbidden = (
        "from engine",
        "import engine",
        "from game",
        "import game",
        "from simulation",
        "from defense",
        "from report",
        "run_game",
        "compute_round",
        "compute_scorecard",
        "requests",
        "openai",
        "random",
        "cache",
        "Path(",
        "open(",
    )
    for token in forbidden:
        assert token not in source
