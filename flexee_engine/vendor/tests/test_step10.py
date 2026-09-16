from __future__ import annotations

from copy import deepcopy
import inspect

import pytest

import defense
from defense import DEFAULT_WEIGHTS, DIMENSIONS, SEGS, assemble_defense_packet, build_defense, normalize_across_cohort
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
ENGAGEMENT = {"A": 2, "B": 6, "C": 4, "D": 5}
GOLDEN = {
    "A": {
        "raw_market_read": -0.0960,
        "dimensions": {"market_read": 0.0, "outcome": 0.0, "engagement": 0.0, "coherence": 50.0},
        "defense_score": 7.500,
        "rank": 4,
    },
    "B": {
        "raw_market_read": 0.0950,
        "dimensions": {"market_read": 100.0, "outcome": 95.2, "engagement": 100.0, "coherence": 50.0},
        "defense_score": 90.808,
        "rank": 1,
    },
    "C": {
        "raw_market_read": 0.0150,
        "dimensions": {"market_read": 58.1, "outcome": 82.6, "engagement": 50.0, "coherence": 50.0},
        "defense_score": 64.248,
        "rank": 3,
    },
    "D": {
        "raw_market_read": 0.0875,
        "dimensions": {"market_read": 96.1, "outcome": 100.0, "engagement": 75.0, "coherence": 50.0},
        "defense_score": 87.376,
        "rank": 2,
    },
}


def _decisions(rounds: int = 6):
    return [deepcopy(BASE_DECISIONS) for _ in range(rounds)]


def _history(rounds: int = 6):
    return run_game(_decisions(rounds))


def test_locked_constants_and_default_weights():
    assert SEGS == ("LEG", "FIT", "FUN", "GENZ", "MAIN")
    assert set(DEFAULT_WEIGHTS) == set(DIMENSIONS)
    assert sum(DEFAULT_WEIGHTS.values()) == pytest.approx(1.0)
    with pytest.raises(TypeError):
        DEFAULT_WEIGHTS["market_read"] = 1.0


def test_normalize_across_cohort_public_helper():
    values = {"B": 20.0, "A": 10, "C": 15.0}

    normalized = normalize_across_cohort(values)

    assert list(normalized) == ["B", "A", "C"]
    assert normalized == {"B": 100.0, "A": 0.0, "C": 50.0}
    assert normalize_across_cohort({"A": 7.0, "B": 7.0}) == {"A": 50.0, "B": 50.0}
    assert normalize_across_cohort({"Solo": 99.0}) == {"Solo": 50.0}

    with pytest.raises(ValueError):
        normalize_across_cohort({})
    with pytest.raises(ValueError):
        normalize_across_cohort({"": 1.0})
    with pytest.raises(ValueError):
        normalize_across_cohort({"A": float("nan")})
    with pytest.raises(ValueError):
        normalize_across_cohort([])


def test_build_defense_golden_values():
    result = build_defense(_history(), _decisions(), ENGAGEMENT)

    assert set(result) == {"A", "B", "C", "D"}
    for team, expected in GOLDEN.items():
        assert set(result[team]) == {"dimensions", "defense_score", "rank", "n_teams", "cohort", "raw"}
        assert set(result[team]["dimensions"]) == set(DIMENSIONS)
        assert set(result[team]["raw"]) == set(DIMENSIONS)
        assert set(result[team]["cohort"]) == {"defense_score", *DIMENSIONS}
        for cohort_metric in result[team]["cohort"].values():
            assert set(cohort_metric) == {"best", "average", "worst"}
        assert result[team]["raw"]["market_read"] == pytest.approx(expected["raw_market_read"], abs=0.001)
        for dimension, value in expected["dimensions"].items():
            assert result[team]["dimensions"][dimension] == pytest.approx(value, abs=0.1)
        assert result[team]["defense_score"] == pytest.approx(expected["defense_score"], abs=0.1)
        assert result[team]["rank"] == expected["rank"]
        assert result[team]["n_teams"] == 4

    cohort = result["A"]["cohort"]["defense_score"]
    assert cohort["best"] == pytest.approx(90.808, abs=0.1)
    assert cohort["average"] == pytest.approx(62.483, abs=0.1)
    assert cohort["worst"] == pytest.approx(7.500, abs=0.1)


def test_flagship_say_do_ordering_and_growth_reader_comparison():
    result = build_defense(_history(), _decisions(), ENGAGEMENT)

    assert result["A"]["raw"]["market_read"] == min(item["raw"]["market_read"] for item in result.values())
    assert result["A"]["defense_score"] == min(item["defense_score"] for item in result.values())
    assert result["B"]["defense_score"] > result["C"]["defense_score"] > result["A"]["defense_score"]
    assert result["D"]["defense_score"] > result["C"]["defense_score"] > result["A"]["defense_score"]

    legacy = deepcopy(BASE_DECISIONS)
    growth = deepcopy(BASE_DECISIONS)
    legacy["B"]["emphasis"] = BASE_DECISIONS["A"]["emphasis"]
    growth["B"]["emphasis"] = BASE_DECISIONS["B"]["emphasis"]
    legacy_result = build_defense(run_game([legacy]), [legacy], {"A": 1, "B": 1, "C": 1, "D": 1})
    growth_result = build_defense(run_game([growth]), [growth], {"A": 1, "B": 1, "C": 1, "D": 1})
    assert growth_result["B"]["raw"]["market_read"] > legacy_result["B"]["raw"]["market_read"]


def test_dimension_math_cohort_anonymity_and_rank():
    result = build_defense(_history(), _decisions(), ENGAGEMENT)

    assert {team: result[team]["dimensions"]["engagement"] for team in result} == {
        "A": 0.0,
        "B": 100.0,
        "C": 50.0,
        "D": 75.0,
    }
    for team, team_result in result.items():
        expected_score = sum(
            team_result["dimensions"][dimension] * DEFAULT_WEIGHTS[dimension]
            for dimension in DIMENSIONS
        )
        assert team_result["defense_score"] == pytest.approx(expected_score)
        assert team_result["rank"] == 1 + sum(
            other["defense_score"] > team_result["defense_score"]
            for other in result.values()
        )
        assert team not in team_result["cohort"]["defense_score"]
        assert team_result["cohort"] == result["A"]["cohort"]

    scores = [team_result["defense_score"] for team_result in result.values()]
    assert result["A"]["cohort"]["defense_score"] == {
        "best": max(scores),
        "average": pytest.approx(sum(scores) / len(scores)),
        "worst": min(scores),
    }


def test_coherence_differentiates_positioning_thrash():
    decisions = _decisions()
    for round_index, round_decisions in enumerate(decisions):
        round_decisions["A"]["positioning"] = 20.0 if round_index % 2 == 0 else 80.0
    result = build_defense(run_game(decisions), decisions, ENGAGEMENT)

    assert result["A"]["raw"]["coherence"] < result["B"]["raw"]["coherence"]
    assert result["A"]["dimensions"]["coherence"] == 0.0
    for team in ("B", "C", "D"):
        assert result[team]["dimensions"]["coherence"] == 100.0


def test_weights_are_honored_and_tunable():
    result = build_defense(_history(), _decisions(), ENGAGEMENT)
    alternate = build_defense(
        _history(),
        _decisions(),
        ENGAGEMENT,
        weights={"market_read": 1.0, "outcome": 0.0, "engagement": 0.0, "coherence": 0.0},
    )

    for team in result:
        assert alternate[team]["defense_score"] == alternate[team]["dimensions"]["market_read"]
    assert result != alternate

    nonsumming = build_defense(
        _history(),
        _decisions(),
        ENGAGEMENT,
        weights={"market_read": 2.0, "outcome": 3.0, "engagement": 4.0, "coherence": 5.0},
    )
    for team, team_result in nonsumming.items():
        assert team_result["defense_score"] == pytest.approx(
            team_result["dimensions"]["market_read"] * 2.0
            + team_result["dimensions"]["outcome"] * 3.0
            + team_result["dimensions"]["engagement"] * 4.0
            + team_result["dimensions"]["coherence"] * 5.0
        )


def test_ties_share_best_rank_and_outputs_are_independent():
    result = build_defense(
        _history(),
        _decisions(),
        ENGAGEMENT,
        weights={"market_read": 0.0, "outcome": 0.0, "engagement": 0.0, "coherence": 0.0},
    )

    assert {team_result["rank"] for team_result in result.values()} == {1}
    assert {team_result["defense_score"] for team_result in result.values()} == {0.0}
    assert all(team_result["n_teams"] == 4 for team_result in result.values())
    result["A"]["dimensions"]["market_read"] = -999
    result["A"]["cohort"]["defense_score"]["best"] = -999
    fresh = build_defense(
        _history(),
        _decisions(),
        ENGAGEMENT,
        weights={"market_read": 0.0, "outcome": 0.0, "engagement": 0.0, "coherence": 0.0},
    )
    assert fresh["B"]["dimensions"]["market_read"] != -999
    assert fresh["B"]["cohort"]["defense_score"]["best"] == 0.0


def test_purity_determinism_and_packet_assembler():
    history = _history()
    decisions = _decisions()
    original_history = deepcopy(history)
    original_decisions = deepcopy(decisions)
    engagement = deepcopy(ENGAGEMENT)
    original_engagement = deepcopy(engagement)
    focus_evidence = {"B": [{"round": 1, "responses": [{"persona_id": "p"}]}]}
    advisor_usage = {"B": [{"team_id": "B", "round": 1, "consulted_advisors": ("anita",)}]}
    original_focus = deepcopy(focus_evidence)
    original_usage = deepcopy(advisor_usage)

    first = build_defense(history, decisions, engagement)
    second = build_defense(deepcopy(history), deepcopy(decisions), deepcopy(engagement))
    packet = assemble_defense_packet("B", history, decisions, focus_evidence, advisor_usage)

    assert first == second
    assert history == original_history
    assert decisions == original_decisions
    assert engagement == original_engagement
    assert focus_evidence == original_focus
    assert advisor_usage == original_usage
    assert set(packet) == {"team", "decision_trajectory", "focus_group_evidence", "advisor_usage", "scorecard_arc"}
    assert packet["team"] == "B"
    assert len(packet["decision_trajectory"]) == 6
    assert len(packet["scorecard_arc"]) == 6
    assert packet["focus_group_evidence"] == focus_evidence["B"]
    assert packet["advisor_usage"] == advisor_usage["B"]
    packet["decision_trajectory"][0]["decision"]["brand"] = -1
    packet["focus_group_evidence"][0]["responses"][0]["persona_id"] = "changed"
    packet["advisor_usage"][0]["consulted_advisors"] = ("changed",)
    assert decisions[0]["B"]["brand"] == 6.0
    assert focus_evidence == original_focus
    assert advisor_usage == original_usage


def test_packet_gathers_live_rounds_and_filters_tagged_inputs():
    history = _history()
    decisions = _decisions()
    focus_evidence = [
        {"team_id": "A", "round": 2, "responses": []},
        {"team_id": "B", "round": 3, "responses": [{"persona_id": "x"}]},
        {"team": "B", "round": 4, "responses": [{"persona_id": "y"}]},
    ]
    advisor_usage = [
        {"team_id": "A", "round": 2, "consulted_advisors": ("anita",)},
        {"team_id": "B", "round": 3, "consulted_advisors": ("ray",)},
    ]

    packet = assemble_defense_packet("B", history, decisions, focus_evidence, advisor_usage, live_from=3)

    assert [item["round"] for item in packet["decision_trajectory"]] == [3, 4, 5, 6]
    assert [item["round"] for item in packet["scorecard_arc"]] == [3, 4, 5, 6]
    assert packet["focus_group_evidence"] == focus_evidence[1:]
    assert packet["advisor_usage"] == [advisor_usage[1]]


def test_packet_accepts_single_already_team_scoped_evidence_mapping():
    history = _history()
    decisions = _decisions()
    focus_evidence = {"round": 2, "responses": [{"persona_id": "focused"}]}
    advisor_usage = {"round": 2, "consulted_advisors": ("anita",)}

    packet = assemble_defense_packet("B", history, decisions, focus_evidence, advisor_usage)

    assert packet["focus_group_evidence"] == focus_evidence
    assert packet["advisor_usage"] == advisor_usage
    packet["focus_group_evidence"]["responses"][0]["persona_id"] = "changed"
    packet["advisor_usage"]["consulted_advisors"] = ("changed",)
    assert focus_evidence["responses"][0]["persona_id"] == "focused"
    assert advisor_usage["consulted_advisors"] == ("anita",)


def test_open_ended_live_from_and_short_history():
    teams = ("A", "B", "C", "D", "E")
    round_decision = {
        team: {
            "brand": 3.0 + index % 4,
            "perf": 6.0 - index % 4,
            "research": 1.0,
            "positioning": 20.0 + index * 10.0,
            "emphasis": (0.20, 0.20, 0.20, 0.20, 0.20),
        }
        for index, team in enumerate(teams)
    }
    decisions = [deepcopy(round_decision) for _ in range(2)]
    decisions[0]["E"]["positioning"] = 20.0
    decisions[1]["E"]["positioning"] = 80.0
    history = run_game(decisions)
    result = build_defense(history, decisions, {"A": 1, "B": 2, "C": 3, "D": 4, "E": 5}, live_from=2)

    assert set(result) == set(teams)
    assert all(team_result["n_teams"] == 5 for team_result in result.values())
    assert result["E"]["raw"]["coherence"] == -60.0
    assert result["E"]["dimensions"]["coherence"] == 0.0
    for team in ("A", "B", "C", "D"):
        assert result[team]["dimensions"]["coherence"] == 100.0


def test_one_round_arbitrary_one_team_cohort_normalizes_to_midpoint():
    decisions = [{
        "Solo": {
            "brand": 4.0,
            "perf": 4.0,
            "research": 2.0,
            "positioning": 42.0,
            "emphasis": (0.20, 0.20, 0.20, 0.20, 0.20),
        }
    }]
    result = build_defense(run_game(decisions), decisions, {}, live_from=1)

    assert set(result) == {"Solo"}
    assert result["Solo"]["n_teams"] == 1
    assert result["Solo"]["raw"]["coherence"] == 0.0
    assert result["Solo"]["dimensions"] == {
        "market_read": 50.0,
        "outcome": 50.0,
        "engagement": 50.0,
        "coherence": 50.0,
    }


def test_defense_module_boundary_is_pure_overlay():
    source = inspect.getsource(defense)
    forbidden = (
        "from game",
        "import game",
        "run_game",
        "compute_round",
        "compute_scorecard",
        "run_simulation",
        "evaluate_stances",
        "advise",
        "focus_group(",
        "from customer_agents import focus_group",
        "build_report",
        "open(",
        "random",
    )
    for token in forbidden:
        assert token not in source


@pytest.mark.parametrize(
    "engagement",
    [
        {"Z": 1},
        {"A": -1},
        {"A": "many"},
        {"A": float("nan")},
        {"A": float("inf")},
    ],
)
def test_invalid_engagement_values_raise_value_error(engagement):
    with pytest.raises(ValueError):
        build_defense(_history(), _decisions(), engagement)


@pytest.mark.parametrize(
    "weights",
    [
        {"market_read": 1.0, "outcome": 0.0, "engagement": 0.0},
        {"market_read": 1.0, "outcome": 0.0, "engagement": 0.0, "coherence": 0.0, "extra": 0.0},
        {"market_read": "high", "outcome": 0.0, "engagement": 0.0, "coherence": 0.0},
        {"market_read": float("nan"), "outcome": 0.0, "engagement": 0.0, "coherence": 0.0},
        {"market_read": float("inf"), "outcome": 0.0, "engagement": 0.0, "coherence": 0.0},
    ],
)
def test_invalid_weights_raise_value_error(weights):
    with pytest.raises(ValueError):
        build_defense(_history(), _decisions(), ENGAGEMENT, weights=weights)


def test_more_history_decision_and_live_from_validation_cases():
    history = _history()
    decisions = _decisions()

    cases = []
    bad = deepcopy(history)
    bad[0] = {**bad[0], "round": True}
    cases.append((bad, decisions, ENGAGEMENT, DEFAULT_WEIGHTS, 1))
    bad = deepcopy(history)
    bad[1] = {**bad[1], "round": 1}
    cases.append((bad, decisions, ENGAGEMENT, DEFAULT_WEIGHTS, 1))
    bad = deepcopy(history)
    bad[0] = {**bad[0], "firms": []}
    cases.append((bad, decisions, ENGAGEMENT, DEFAULT_WEIGHTS, 1))
    bad = deepcopy(history)
    bad[0]["firms"]["A"] = []
    cases.append((bad, decisions, ENGAGEMENT, DEFAULT_WEIGHTS, 1))
    bad = deepcopy(history)
    del bad[0]["firms"]["A"]["perception"]
    cases.append((bad, decisions, ENGAGEMENT, DEFAULT_WEIGHTS, 1))
    bad = deepcopy(history)
    bad[-1]["scorecard"] = []
    cases.append((bad, decisions, ENGAGEMENT, DEFAULT_WEIGHTS, 1))
    bad = deepcopy(history)
    bad[-1]["scorecard"]["firms"] = []
    cases.append((bad, decisions, ENGAGEMENT, DEFAULT_WEIGHTS, 1))
    bad = deepcopy(history)
    bad[-1]["scorecard"]["firms"]["A"] = []
    cases.append((bad, decisions, ENGAGEMENT, DEFAULT_WEIGHTS, 1))
    bad = deepcopy(history)
    del bad[-1]["scorecard"]["firms"]["A"]["overall"]
    cases.append((bad, decisions, ENGAGEMENT, DEFAULT_WEIGHTS, 1))
    bad = deepcopy(history)
    bad[-1]["scorecard"]["firms"]["A"]["overall"] = float("inf")
    cases.append((bad, decisions, ENGAGEMENT, DEFAULT_WEIGHTS, 1))
    bad_decisions = deepcopy(decisions)
    del bad_decisions[0]["A"]
    cases.append((history, bad_decisions, ENGAGEMENT, DEFAULT_WEIGHTS, 1))
    bad_decisions = deepcopy(decisions)
    bad_decisions[0]["A"] = []
    cases.append((history, bad_decisions, ENGAGEMENT, DEFAULT_WEIGHTS, 1))
    bad_decisions = deepcopy(decisions)
    del bad_decisions[0]["A"]["emphasis"]
    cases.append((history, bad_decisions, ENGAGEMENT, DEFAULT_WEIGHTS, 1))
    bad_decisions = deepcopy(decisions)
    bad_decisions[0]["A"]["emphasis"] = "LEG"
    cases.append((history, bad_decisions, ENGAGEMENT, DEFAULT_WEIGHTS, 1))
    bad_decisions = deepcopy(decisions)
    bad_decisions[0]["A"]["emphasis"] = (-0.1, 0.3, 0.3, 0.3, 0.2)
    cases.append((history, bad_decisions, ENGAGEMENT, DEFAULT_WEIGHTS, 1))
    bad_decisions = deepcopy(decisions)
    bad_decisions[0]["A"]["emphasis"] = (0.2, 0.2, 0.2, 0.2, 0.3)
    cases.append((history, bad_decisions, ENGAGEMENT, DEFAULT_WEIGHTS, 1))
    bad_decisions = deepcopy(decisions)
    del bad_decisions[0]["A"]["positioning"]
    cases.append((history, bad_decisions, ENGAGEMENT, DEFAULT_WEIGHTS, 1))
    bad_decisions = deepcopy(decisions)
    bad_decisions[0]["A"]["positioning"] = float("nan")
    cases.append((history, bad_decisions, ENGAGEMENT, DEFAULT_WEIGHTS, 1))
    cases.append((history, decisions, ENGAGEMENT, DEFAULT_WEIGHTS, True))

    for bad_history, bad_decisions, bad_engagement, bad_weights, bad_live_from in cases:
        with pytest.raises(ValueError):
            build_defense(bad_history, bad_decisions, bad_engagement, bad_weights, bad_live_from)


@pytest.mark.parametrize(
    "bad_history,bad_decisions,bad_engagement,bad_weights,bad_live_from",
    [
        ("not history", _decisions(), ENGAGEMENT, DEFAULT_WEIGHTS, 1),
        ([], _decisions(), ENGAGEMENT, DEFAULT_WEIGHTS, 1),
        ([1], _decisions(1), ENGAGEMENT, DEFAULT_WEIGHTS, 1),
        ([{"round": 1}], _decisions(1), ENGAGEMENT, DEFAULT_WEIGHTS, 1),
        (_history(), "not decisions", ENGAGEMENT, DEFAULT_WEIGHTS, 1),
        (_history(), [], ENGAGEMENT, DEFAULT_WEIGHTS, 1),
        (_history(), _decisions(5), ENGAGEMENT, DEFAULT_WEIGHTS, 1),
        (_history(), [{**_decisions()[0], "A": {"positioning": 20.0, "emphasis": (0.2, 0.2)}}] * 6, ENGAGEMENT, DEFAULT_WEIGHTS, 1),
        (_history(), _decisions(), [], DEFAULT_WEIGHTS, 1),
        (_history(), _decisions(), {"": 1}, DEFAULT_WEIGHTS, 1),
        (_history(), _decisions(), {"A": float("nan")}, DEFAULT_WEIGHTS, 1),
        (_history(), _decisions(), ENGAGEMENT, [], 1),
        (_history(), _decisions(), ENGAGEMENT, {"market_read": 1.0}, 1),
        (_history(), _decisions(), ENGAGEMENT, DEFAULT_WEIGHTS, 0),
        (_history(), _decisions(), ENGAGEMENT, DEFAULT_WEIGHTS, 7),
    ],
)
def test_invalid_inputs_raise_value_error(bad_history, bad_decisions, bad_engagement, bad_weights, bad_live_from):
    with pytest.raises(ValueError):
        build_defense(bad_history, bad_decisions, bad_engagement, bad_weights, bad_live_from)


def test_packet_validation_errors():
    with pytest.raises(ValueError):
        assemble_defense_packet("", _history(), _decisions())
    with pytest.raises(ValueError):
        assemble_defense_packet("Z", _history(), _decisions())
    with pytest.raises(ValueError):
        assemble_defense_packet("A", _history(), _decisions(5))
    with pytest.raises(ValueError):
        assemble_defense_packet("A", _history(), _decisions(), focus_group_evidence=[1])
    with pytest.raises(ValueError):
        assemble_defense_packet("A", _history(), _decisions(), advisor_usage=[1])
    with pytest.raises(ValueError):
        assemble_defense_packet("A", _history(), _decisions(), live_from=0)
    bad_history = _history()
    del bad_history[2]["scorecard"]["firms"]["A"]
    with pytest.raises(ValueError):
        assemble_defense_packet("A", bad_history, _decisions(), live_from=3)


def test_build_defense_rejects_missing_live_round_scorecard_team():
    history = _history()
    del history[2]["scorecard"]["firms"]["A"]

    with pytest.raises(ValueError):
        build_defense(history, _decisions(), ENGAGEMENT, live_from=3)
