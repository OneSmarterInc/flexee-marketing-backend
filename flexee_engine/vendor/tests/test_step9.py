from __future__ import annotations

from copy import deepcopy
import inspect

import pytest

import advisors
from advisors import (
    ADVISOR_NAMES,
    ADVISORS,
    DEADBAND,
    POS_CENTER,
    REF_PRICE,
    SEGS,
    TRUST_BASE,
    advisor_scores,
    advisor_usage_record,
    advise,
    anita_score,
    build_usage_record,
    evaluate_stances,
    intensity_for_score,
    mock_generator,
    revealed_truth_signal,
    valence_for_score,
)
from customer_agents import Persona, revealed_warmth, stated_warmth
from game import run_game


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
BASELINE = {
    "brand": 5.0,
    "perf": 2.0,
    "research": 3.0,
    "positioning": 35.0,
    "emphasis": (0.2, 0.2, 0.2, 0.2, 0.2),
    "price": 1.50,
    "promotion": 0.0,
}
FUNCTIONAL_PIVOT = {
    "round": 1,
    "baseline": BASELINE,
    "levers": {
        "brand": 5.0,
        "perf": 3.0,
        "research": 2.0,
        "positioning": 82.0,
        "emphasis": (0.05, 0.15, 0.40, 0.30, 0.10),
        "price": 1.40,
        "promotion": 0.6,
    },
}
HERITAGE_DEFENSE = {
    "round": 1,
    "baseline": BASELINE,
    "levers": {
        "brand": 6.0,
        "perf": 2.0,
        "research": 2.0,
        "positioning": 20.0,
        "emphasis": (0.45, 0.15, 0.05, 0.05, 0.30),
        "price": 1.50,
        "promotion": 0.0,
    },
}
GOLDEN_FUNCTIONAL = {
    "elena": (-1.933, "oppose"),
    "ray": (0.350, "support"),
    "jax": (0.480, "support"),
    "anita": (-0.245, "oppose"),
    "bud": (0.510, "support"),
    "tom": (-0.577, "oppose"),
}
GOLDEN_HERITAGE = {
    "elena": (-0.150, "oppose"),
    "ray": (-0.120, "oppose"),
    "jax": (0.400, "support"),
    "anita": (-0.233, "oppose"),
    "bud": (0.450, "support"),
    "tom": (0.273, "support"),
}


def _history():
    return run_game([deepcopy(BASE_DECISIONS)])


def _perception_by_firm():
    return {
        firm: firm_result["perception"]
        for firm, firm_result in _history()[0]["firms"].items()
    }


def _context():
    perception = _perception_by_firm()
    return {
        "stated_warmth": {
            segment: stated_warmth(
                revealed_warmth(perception, "B", segment),
                Persona(f"{segment.lower()}-zero", segment, segment, 0.0),
            )
            for segment in SEGS
        },
        "eq": _history()[0]["equity"]["B"],
        "trust": 32.0,
    }


def _revealed_context():
    perception = _perception_by_firm()
    return {
        segment: revealed_warmth(perception, "B", segment)
        for segment in SEGS
    }


def test_locked_constants():
    assert SEGS == ("LEG", "FIT", "FUN", "GENZ", "MAIN")
    assert REF_PRICE == 1.50
    assert POS_CENTER == 50.0
    assert TRUST_BASE == 0.32
    assert DEADBAND == 0.05
    with pytest.raises(TypeError):
        ADVISOR_NAMES["new"] = "Nope"


def test_functional_pivot_golden_scores_and_valences():
    public = advise(FUNCTIONAL_PIVOT, _context())
    evaluated = evaluate_stances(FUNCTIONAL_PIVOT, _context())
    internal_by_id = {stance["advisor_id"]: stance for stance in evaluated}

    assert not isinstance(public, dict)
    assert len(public) == 6
    assert len(evaluated) == 6
    assert [stance["advisor_id"] for stance in evaluated] == [advisor_id for advisor_id, _ in ADVISORS]
    assert [stance["advisor_id"] for stance in public] == [advisor_id for advisor_id, _ in ADVISORS]
    for advisor_id, (expected_score, expected_valence) in GOLDEN_FUNCTIONAL.items():
        internal = internal_by_id[advisor_id]
        evaluated_stance = next(stance for stance in evaluated if stance["advisor_id"] == advisor_id)
        public_stance = next(stance for stance in public if stance["advisor_id"] == advisor_id)
        assert internal["score"] == pytest.approx(expected_score, abs=0.01)
        assert evaluated_stance["score"] == pytest.approx(expected_score, abs=0.01)
        assert internal["valence"] == expected_valence
        assert evaluated_stance["valence"] == expected_valence
        assert public_stance["valence"] == expected_valence
        assert public_stance["intensity"] == pytest.approx(min(1.0, abs(internal["score"])))

    valences = [stance["valence"] for stance in public]
    assert valences.count("support") == 3
    assert valences.count("oppose") == 3


def test_heritage_defense_golden_scores_and_valences():
    evaluated = evaluate_stances(HERITAGE_DEFENSE, _context())
    internal_by_id = {stance["advisor_id"]: stance for stance in evaluated}
    public = advise(HERITAGE_DEFENSE, _context())

    for advisor_id, (expected_score, expected_valence) in GOLDEN_HERITAGE.items():
        internal = internal_by_id[advisor_id]
        assert internal["score"] == pytest.approx(expected_score, abs=0.01)
        assert internal["valence"] == expected_valence

    valences = {stance["advisor_id"]: stance["valence"] for stance in public}
    assert valences["tom"] == "support"
    assert valences["bud"] == "support"
    assert valences["ray"] == "oppose"


def test_anita_inherits_say_do_gap_and_prefers_the_wrong_room_signal():
    stated_context = _context()
    revealed_context = _revealed_context()

    stated_legacy = anita_score(HERITAGE_DEFENSE["levers"], stated_context)
    stated_fun = anita_score(FUNCTIONAL_PIVOT["levers"], stated_context)
    revealed_legacy = revealed_truth_signal(HERITAGE_DEFENSE["levers"], revealed_context)
    revealed_fun = revealed_truth_signal(FUNCTIONAL_PIVOT["levers"], revealed_context)

    assert stated_legacy == pytest.approx(-0.2332, abs=0.001)
    assert stated_fun == pytest.approx(-0.2450, abs=0.001)
    assert revealed_legacy == pytest.approx(-0.4082, abs=0.001)
    assert revealed_fun == pytest.approx(-0.0250, abs=0.001)
    assert stated_legacy > stated_fun
    assert revealed_fun > revealed_legacy


def test_public_stance_schema_and_no_leakage_or_responsibility_claim():
    public = advise(FUNCTIONAL_PIVOT, _context())
    evaluated = evaluate_stances(FUNCTIONAL_PIVOT, _context())

    assert not isinstance(public, dict)
    assert len(public) == 6
    for internal in evaluated:
        assert set(internal) == {
            "advisor_id",
            "advisor_name",
            "valence",
            "intensity",
            "rationale_tags",
            "score",
        }
        assert "text" not in internal

    for stance in public:
        assert set(stance) == {"advisor_id", "advisor_name", "valence", "intensity", "text"}
        assert "score" not in stance
        assert "rationale_tags" not in stance
        assert "competitor" not in stance["text"].lower()
        assert "scoreboard" not in stance["text"].lower()
        assert "other team" not in stance["text"].lower()
        assert "68.215" not in stance["text"]
        assert "0.225" not in stance["text"]
        assert "0.602" not in stance["text"]
        assert "I decided" not in stance["text"]
        assert "my decision" not in stance["text"].lower()
        assert "the call remains yours" in stance["text"]


def test_advise_never_decides_or_mutates_inputs():
    decision = deepcopy(FUNCTIONAL_PIVOT)
    context = deepcopy(_context())
    original_decision = deepcopy(decision)
    original_context = deepcopy(context)

    public = advise(decision, context)

    assert decision == original_decision
    assert context == original_context
    assert not isinstance(public, dict)
    assert all("decision" not in stance for stance in public)
    assert all("chosen_decision" not in stance for stance in public)
    assert all("submit" not in stance for stance in public)
    assert all("usage" not in stance for stance in public)


def test_evaluate_stances_is_locked_no_text_api_and_does_not_mutate_inputs():
    decision = deepcopy(FUNCTIONAL_PIVOT)
    context = deepcopy(_context())
    original_decision = deepcopy(decision)
    original_context = deepcopy(context)

    evaluated = evaluate_stances(decision, context)

    assert decision == original_decision
    assert context == original_context
    assert len(evaluated) == 6
    for stance in evaluated:
        assert "text" not in stance
        assert "usage" not in stance
        assert "chosen_decision" not in stance
        assert stance["advisor_id"] in ADVISOR_NAMES


def test_valence_intensity_deadband_and_disagreement_invariants():
    evaluated = evaluate_stances(FUNCTIONAL_PIVOT, _context())
    public = advise(FUNCTIONAL_PIVOT, _context())

    for internal in evaluated:
        score = internal["score"]
        assert internal["valence"] == valence_for_score(score)
        assert internal["intensity"] == intensity_for_score(score)
        assert 0.0 <= internal["intensity"] <= 1.0

    assert valence_for_score(DEADBAND) == "mixed"
    assert valence_for_score(-DEADBAND) == "mixed"
    assert valence_for_score(DEADBAND + 0.001) == "support"
    assert valence_for_score(-DEADBAND - 0.001) == "oppose"
    assert {stance["valence"] for stance in public} >= {"support", "oppose"}


def test_no_single_advisor_is_oracle_across_golden_decisions():
    functional = {stance["advisor_id"]: stance for stance in evaluate_stances(FUNCTIONAL_PIVOT, _context())}
    heritage = {stance["advisor_id"]: stance for stance in evaluate_stances(HERITAGE_DEFENSE, _context())}

    truth_functional = revealed_truth_signal(FUNCTIONAL_PIVOT["levers"], _revealed_context())
    truth_heritage = revealed_truth_signal(HERITAGE_DEFENSE["levers"], _revealed_context())
    truth_valences = {
        "functional": valence_for_score(truth_functional),
        "heritage": valence_for_score(truth_heritage),
    }
    assert truth_valences == {"functional": "mixed", "heritage": "oppose"}

    for advisor_id, _ in ADVISORS:
        advisor_valences = {
            "functional": functional[advisor_id]["valence"],
            "heritage": heritage[advisor_id]["valence"],
        }
        assert advisor_valences != truth_valences


def test_determinism_and_generator_injection_changes_only_text():
    decision = deepcopy(FUNCTIONAL_PIVOT)
    context = deepcopy(_context())
    first = advise(decision, context)
    second = advise(deepcopy(decision), deepcopy(context))
    first_evaluated = evaluate_stances(decision, context)
    second_evaluated = evaluate_stances(deepcopy(decision), deepcopy(context))
    assert first == second
    assert first_evaluated == second_evaluated
    assert mock_generator({
        "advisor_id": "elena",
        "advisor_name": "Elena Vasquez",
        "valence": "support",
        "intensity": 0.5,
        "rationale_tags": ("brand_coherence",),
    }) == mock_generator({
        "advisor_id": "elena",
        "advisor_name": "Elena Vasquez",
        "valence": "support",
        "intensity": 0.5,
        "rationale_tags": ("brand_coherence",),
    })

    custom = advise(decision, context, generator=lambda stance: f"custom:{stance['advisor_id']}")
    first_without_text = [
        {key: value for key, value in stance.items() if key != "text"}
        for stance in first
    ]
    custom_without_text = [
        {key: value for key, value in stance.items() if key != "text"}
        for stance in custom
    ]
    assert first_without_text == custom_without_text
    assert evaluate_stances(decision, context) == evaluate_stances(decision, context)
    assert [stance["text"] for stance in custom] == [
        f"custom:{advisor_id}"
        for advisor_id, _ in ADVISORS
    ]


def test_usage_tracking_records_consulted_advisors_without_points():
    public = advise(FUNCTIONAL_PIVOT, _context())

    usage = build_usage_record(
        "Team-B",
        1,
        ("anita", "ray", "tom"),
        FUNCTIONAL_PIVOT,
    )
    assert usage == {
        "team_id": "Team-B",
        "round": 1,
        "consulted_advisors": ("anita", "ray", "tom"),
        "lever_changes": (
            "perf",
            "research",
            "positioning",
            "emphasis",
            "price",
            "promotion",
        ),
        "consultation_count": 3,
    }
    assert set(usage) == {
        "team_id",
        "round",
        "consulted_advisors",
        "lever_changes",
        "consultation_count",
    }
    for forbidden in (
        "decision_round",
        "points_awarded",
        "points",
        "score",
        "recommendation",
        "compliance",
        "correctness",
        "reward",
        "penalty",
    ):
        assert forbidden not in usage
    assert advisor_usage_record(public) == {
        "consulted_advisors": tuple(advisor_id for advisor_id, _ in ADVISORS),
        "points_awarded": 0,
    }
    assert all("usage" not in stance for stance in public)


def test_build_usage_record_lever_changes_counts_and_order():
    no_change = {
        "round": 1,
        "levers": deepcopy(BASELINE),
        "baseline": deepcopy(BASELINE),
    }

    assert build_usage_record("Team-B", 1, ("elena",), no_change) == {
        "team_id": "Team-B",
        "round": 1,
        "consulted_advisors": ("elena",),
        "lever_changes": (),
        "consultation_count": 1,
    }

    all_ids = tuple(advisor_id for advisor_id, _ in ADVISORS)
    all_usage = build_usage_record("Team-B", 1, all_ids, FUNCTIONAL_PIVOT)
    assert all_usage["consulted_advisors"] == all_ids
    assert all_usage["consultation_count"] == 6


def test_advisors_module_is_proxy_only_standard_library():
    source = inspect.getsource(advisors)
    forbidden = (
        "from engine",
        "import engine",
        "from game",
        "import game",
        "from game_faculty",
        "from scorecard",
        "from simulation",
        "from customer_agents",
        "compute_round",
        "run_game",
        "run_simulation",
    )
    for token in forbidden:
        assert token not in source


@pytest.mark.parametrize(
    "bad_decision",
    [
        [],
        {"round": 1, "levers": FUNCTIONAL_PIVOT["levers"]},
        {"round": 1.5, "levers": FUNCTIONAL_PIVOT["levers"], "baseline": BASELINE},
        {"round": True, "levers": FUNCTIONAL_PIVOT["levers"], "baseline": BASELINE},
        {"round": 1, "levers": [], "baseline": BASELINE},
        {"round": 1, "levers": {**FUNCTIONAL_PIVOT["levers"], "extra": 1.0}, "baseline": BASELINE},
        {"round": 1, "levers": {**FUNCTIONAL_PIVOT["levers"], "brand": "bad"}, "baseline": BASELINE},
        {"round": 1, "levers": {**FUNCTIONAL_PIVOT["levers"], "brand": float("nan")}, "baseline": BASELINE},
        {"round": 1, "levers": {**FUNCTIONAL_PIVOT["levers"], "emphasis": (0.2, 0.2)}, "baseline": BASELINE},
        {"round": 1, "levers": {**FUNCTIONAL_PIVOT["levers"], "emphasis": (0.2, 0.2, 0.2, 0.2, 0.3)}, "baseline": BASELINE},
        {"round": 1, "levers": {**FUNCTIONAL_PIVOT["levers"], "emphasis": (-0.2, 0.3, 0.3, 0.3, 0.3)}, "baseline": BASELINE},
        {"round": 1, "levers": {**FUNCTIONAL_PIVOT["levers"], "price": 0.0}, "baseline": BASELINE},
        {"round": 1, "levers": {**FUNCTIONAL_PIVOT["levers"], "promotion": -0.1}, "baseline": BASELINE},
    ],
)
def test_invalid_decisions_raise_value_error(bad_decision):
    with pytest.raises(ValueError):
        advise(bad_decision, _context())


@pytest.mark.parametrize(
    "bad_context",
    [
        [],
        {"eq": 68.0, "trust": 32.0},
        {"stated_warmth": {}, "eq": 68.0, "trust": 32.0},
        {"stated_warmth": {segment: 0.5 for segment in SEGS} | {"BAD": 0.5}, "eq": 68.0, "trust": 32.0},
        {"stated_warmth": {**dict.fromkeys(SEGS, 0.5), "LEG": "bad"}, "eq": 68.0, "trust": 32.0},
        {"stated_warmth": {**dict.fromkeys(SEGS, 0.5), "LEG": float("nan")}, "eq": 68.0, "trust": 32.0},
        {"stated_warmth": {**dict.fromkeys(SEGS, 0.5), "LEG": -0.1}, "eq": 68.0, "trust": 32.0},
        {"stated_warmth": {**dict.fromkeys(SEGS, 0.5), "LEG": 1.1}, "eq": 68.0, "trust": 32.0},
        {"stated_warmth": dict.fromkeys(SEGS, 0.5), "eq": "bad", "trust": 32.0},
        {"stated_warmth": dict.fromkeys(SEGS, 0.5), "eq": float("inf"), "trust": 32.0},
        {"stated_warmth": dict.fromkeys(SEGS, 0.5), "eq": 68.0, "trust": "bad"},
    ],
)
def test_invalid_contexts_raise_value_error(bad_context):
    with pytest.raises(ValueError):
        advise(FUNCTIONAL_PIVOT, bad_context)


def test_generator_and_usage_validation_errors():
    with pytest.raises(ValueError):
        advise(FUNCTIONAL_PIVOT, _context(), generator="not callable")
    with pytest.raises(ValueError):
        advise(FUNCTIONAL_PIVOT, _context(), generator=lambda stance: 123)
    with pytest.raises(ValueError):
        advisor_usage_record("not stances")
    with pytest.raises(ValueError):
        advisor_usage_record([{"advisor_id": "missing"}])
    with pytest.raises(ValueError):
        build_usage_record("", 1, ("elena",), FUNCTIONAL_PIVOT)
    with pytest.raises(ValueError):
        build_usage_record("Team B", True, ("elena",), FUNCTIONAL_PIVOT)
    with pytest.raises(ValueError):
        build_usage_record("Team B", 1, "elena", FUNCTIONAL_PIVOT)
    with pytest.raises(ValueError):
        build_usage_record("Team B", 1, ("elena", "elena"), FUNCTIONAL_PIVOT)
    with pytest.raises(ValueError):
        build_usage_record("Team B", 1, ("missing",), FUNCTIONAL_PIVOT)
    with pytest.raises(ValueError):
        build_usage_record("Team B", 2, ("elena",), FUNCTIONAL_PIVOT)


def test_advisor_scores_helper_matches_internal_channel():
    evaluated = evaluate_stances(FUNCTIONAL_PIVOT, _context())
    assert advisor_scores(FUNCTIONAL_PIVOT, _context()) == {
        stance["advisor_id"]: stance["score"]
        for stance in evaluated
    }
    assert {
        stance["advisor_id"]: stance["score"]
        for stance in evaluated
    } == advisor_scores(FUNCTIONAL_PIVOT, _context())


def test_locked_public_api_contracts():
    public = advise(FUNCTIONAL_PIVOT, _context())
    internal = evaluate_stances(FUNCTIONAL_PIVOT, _context())

    assert callable(evaluate_stances)
    assert callable(build_usage_record)
    assert not isinstance(public, dict)
    assert len(public) == 6
    assert len(internal) == 6
    for stance in public:
        assert set(stance) == {
            "advisor_id",
            "advisor_name",
            "valence",
            "intensity",
            "text",
        }
        assert "usage" not in stance
        assert "internal" not in stance
    for stance in internal:
        assert set(stance) == {
            "advisor_id",
            "advisor_name",
            "valence",
            "intensity",
            "rationale_tags",
            "score",
        }
        assert "text" not in stance
