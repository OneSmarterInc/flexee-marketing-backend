from __future__ import annotations

from copy import deepcopy

import pytest

from crisis import DEFAULT_CRISIS_PARAMS
from game import run_game
from report import DIMENSIONS, build_report


TEAMS = ("A", "B", "C", "D")
CRISIS_INPUTS = {
    "A": {"exposure": 0.9, "preempt": 0.1, "response": 0.3},
    "B": {"exposure": 0.3, "preempt": 0.8, "response": 0.8},
    "C": {"exposure": 0.6, "preempt": 0.4, "response": 0.5},
    "D": {"exposure": 0.7, "preempt": 0.3, "response": 0.5},
}
BASE_DECISIONS = {
    "A": {
        "brand": 3.0,
        "perf": 6.0,
        "research": 1.0,
        "positioning": 20.0,
        "emphasis": (0.45, 0.15, 0.05, 0.05, 0.30),
        "promotion": 0.8,
    },
    "B": {
        "brand": 6.0,
        "perf": 2.0,
        "research": 2.0,
        "positioning": 82.0,
        "emphasis": (0.10, 0.20, 0.40, 0.25, 0.05),
        "promotion": 0.0,
    },
    "C": {
        "brand": 4.0,
        "perf": 4.0,
        "research": 2.0,
        "positioning": 52.0,
        "emphasis": (0.25, 0.20, 0.25, 0.15, 0.15),
        "promotion": 0.4,
    },
    "D": {
        "brand": 5.0,
        "perf": 3.0,
        "research": 2.0,
        "positioning": 72.0,
        "emphasis": (0.15, 0.10, 0.30, 0.40, 0.05),
        "promotion": 0.2,
    },
}
D_PROMOTIONS = (0.2, 0.4, 0.6, 0.8, 0.8, 0.8)
GOLDEN = {
    1: {
        "cohort_overall": {"best": 75.0000, "average": 59.2186, "worst": 25.0000},
        "cohort_dimension_average": {
            "brand_health": 59.1060,
            "customer_value": 58.9114,
            "market_position": 53.5537,
            "financial": 65.3035,
        },
        "teams": {
            "A": {"overall": 25.0000, "rank": 4, "normalized": (0.00, 0.00, 100.00, 0.00)},
            "B": {"overall": 75.0000, "rank": 1, "normalized": (100.00, 100.00, 0.00, 100.00)},
            "C": {"overall": 63.4592, "rank": 3, "normalized": (56.87, 44.83, 78.00, 74.14)},
            "D": {"overall": 73.4152, "rank": 2, "normalized": (79.56, 90.82, 36.21, 87.07)},
        },
    },
    4: {
        "cohort_overall": {"best": 90.6895, "average": 54.7759, "worst": 0.0000},
        "cohort_dimension_average": {
            "brand_health": 53.7903,
            "customer_value": 58.5440,
            "market_position": 59.9953,
            "financial": 46.7741,
        },
        "teams": {
            "A": {"overall": 0.0000, "rank": 4, "normalized": (0.00, 0.00, 0.00, 0.00)},
            "B": {"overall": 90.6895, "rank": 1, "normalized": (100.00, 100.00, 62.76, 100.00)},
            "C": {"overall": 60.2100, "rank": 3, "normalized": (58.06, 43.45, 77.22, 62.11)},
            "D": {"overall": 68.2042, "rank": 2, "normalized": (57.11, 90.73, 100.00, 24.98)},
        },
    },
    6: {
        "cohort_overall": {"best": 100.0000, "average": 55.7701, "worst": 0.0000},
        "cohort_dimension_average": {
            "brand_health": 51.6858,
            "customer_value": 56.9517,
            "market_position": 69.1059,
            "financial": 45.3368,
        },
        "teams": {
            "A": {"overall": 0.0000, "rank": 4, "normalized": (0.00, 0.00, 0.00, 0.00)},
            "B": {"overall": 100.0000, "rank": 1, "normalized": (100.00, 100.00, 100.00, 100.00)},
            "C": {"overall": 62.4515, "rank": 2, "normalized": (57.44, 43.06, 89.76, 59.54)},
            "D": {"overall": 60.6287, "rank": 3, "normalized": (49.30, 84.74, 86.66, 21.81)},
        },
    },
}


def _six_round_history():
    decisions = []
    for round_index in range(6):
        round_decisions = deepcopy(BASE_DECISIONS)
        round_decisions["D"]["promotion"] = D_PROMOTIONS[round_index]
        decisions.append(round_decisions)
    return run_game(decisions, crisis_inputs=deepcopy(CRISIS_INPUTS))


def _short_history(team_count: int = 5, round_count: int = 2):
    names = ("North", "South", "East", "West", "Central")
    teams = names[:team_count]
    round_decision = {
        team: {
            "brand": 3.0 + (index % 4),
            "perf": 6.0 - (index % 4),
            "research": 1.0,
            "positioning": 20.0 + index * 12.0,
            "emphasis": (0.20, 0.20, 0.20, 0.20, 0.20),
            "promotion": (index % 3) * 0.2,
        }
        for index, team in enumerate(teams)
    }
    return run_game([deepcopy(round_decision) for _ in range(round_count)])


def _score(overall: object = 50.0, normalized: object | None = None):
    if normalized is None:
        normalized = dict.fromkeys(DIMENSIONS, 50.0)
    return {"overall": overall, "normalized": normalized}


def _history_with_firms(firms, round_number: object = 1):
    return [{"round": round_number, "scorecard": {"firms": firms}}]


def test_build_report_golden_rounds():
    report = build_report(_six_round_history())

    assert len(report) == 6
    for round_number, expected in GOLDEN.items():
        round_report = report[round_number - 1]
        assert round_report["round"] == round_number
        for metric, value in expected["cohort_overall"].items():
            assert round_report["teams"]["A"]["cohort_overall"][metric] == pytest.approx(value, abs=0.0001)
        for dimension, value in expected["cohort_dimension_average"].items():
            assert round_report["teams"]["A"]["cohort_dimension_average"][dimension] == pytest.approx(
                value,
                abs=0.0001,
            )
        for team, team_expected in expected["teams"].items():
            team_report = round_report["teams"][team]
            assert team_report["own"]["overall"] == pytest.approx(team_expected["overall"], abs=0.0001)
            assert team_report["rank"] == team_expected["rank"]
            for dimension, value in zip(DIMENSIONS, team_expected["normalized"], strict=True):
                assert team_report["own"]["normalized"][dimension] == pytest.approx(value, abs=0.01)


def test_build_report_schema_is_anonymized_and_cohort_blocks_match():
    report = build_report(_six_round_history())

    for round_report in report:
        assert set(round_report) == {"round", "teams"}
        cohort_blocks = []
        for team, team_report in round_report["teams"].items():
            assert set(team_report) == {
                "own",
                "cohort_overall",
                "cohort_dimension_average",
                "rank",
                "n_teams",
            }
            assert set(team_report["own"]) == {"overall", "normalized"}
            assert set(team_report["own"]["normalized"]) == set(DIMENSIONS)
            assert set(team_report["cohort_overall"]) == {"best", "average", "worst"}
            assert set(team_report["cohort_dimension_average"]) == set(DIMENSIONS)
            assert team_report["n_teams"] == len(TEAMS)
            assert team not in team_report["cohort_overall"]
            assert team not in team_report["cohort_dimension_average"]
            cohort_blocks.append((team_report["cohort_overall"], team_report["cohort_dimension_average"]))
        assert all(block == cohort_blocks[0] for block in cohort_blocks)


def test_build_report_invariants_match_underlying_scorecard_exactly():
    history = _six_round_history()
    report = build_report(history)

    for record, round_report in zip(history, report, strict=True):
        teams = round_report["teams"]
        scorecard_firms = record["scorecard"]["firms"]
        overalls = [teams[team]["own"]["overall"] for team in teams]
        assert teams["A"]["cohort_overall"]["best"] == max(overalls)
        assert teams["A"]["cohort_overall"]["worst"] == min(overalls)
        assert teams["A"]["cohort_overall"]["average"] == pytest.approx(sum(overalls) / len(overalls))

        for dimension in DIMENSIONS:
            dimension_values = [teams[team]["own"]["normalized"][dimension] for team in teams]
            assert teams["A"]["cohort_dimension_average"][dimension] == pytest.approx(
                sum(dimension_values) / len(dimension_values)
            )

        expected_ranks = {
            team: 1 + sum(
                scorecard_firms[other_team]["overall"] > scorecard_firms[team]["overall"]
                for other_team in scorecard_firms
            )
            for team in scorecard_firms
        }
        assert {team: teams[team]["rank"] for team in teams} == expected_ranks
        assert min(expected_ranks.values()) == 1
        assert max(expected_ranks.values()) <= len(teams)

        for team, firm_scorecard in scorecard_firms.items():
            assert teams[team]["own"]["overall"] == firm_scorecard["overall"]
            assert teams[team]["own"]["normalized"] == firm_scorecard["normalized"]


def test_build_report_ties_share_better_rank():
    history = _history_with_firms({
        "A": _score(80.0, dict.fromkeys(DIMENSIONS, 80.0)),
        "B": _score(80.0, dict.fromkeys(DIMENSIONS, 40.0)),
        "C": _score(10.0, dict.fromkeys(DIMENSIONS, 10.0)),
    })

    teams = build_report(history)[0]["teams"]

    assert teams["A"]["rank"] == 1
    assert teams["B"]["rank"] == 1
    assert teams["C"]["rank"] == 3


def test_build_report_single_leader_followed_by_two_tied_teams_rank_1_2_2():
    history = _history_with_firms({
        "Leader": _score(90.0),
        "TieOne": _score(70.0),
        "TieTwo": _score(70.0),
    })

    teams = build_report(history)[0]["teams"]

    assert teams["Leader"]["rank"] == 1
    assert teams["TieOne"]["rank"] == 2
    assert teams["TieTwo"]["rank"] == 2


def test_build_report_rank_is_strictly_greater_count_plus_one():
    history = _history_with_firms({
        "High": _score(91.0),
        "MidA": _score(75.0),
        "MidB": _score(75.0),
        "Low": _score(12.0),
    })

    teams = build_report(history)[0]["teams"]
    overalls = {team: team_report["own"]["overall"] for team, team_report in teams.items()}

    for team, overall in overalls.items():
        assert teams[team]["rank"] == 1 + sum(other > overall for other in overalls.values())


def test_build_report_is_pure_and_deterministic():
    history = _six_round_history()
    original = deepcopy(history)

    first = build_report(history)
    second = build_report(deepcopy(history))
    first["rounds" if False else 0]["teams"]["A"]["own"]["normalized"]["brand_health"] = -1.0
    third = build_report(history)

    assert history == original
    assert second == third


def test_build_report_output_dictionaries_are_independent():
    history = _six_round_history()
    original = deepcopy(history)
    report = build_report(history)

    report[0]["teams"]["A"]["cohort_overall"]["best"] = -1.0
    report[0]["teams"]["A"]["cohort_dimension_average"]["brand_health"] = -2.0
    report[0]["teams"]["A"]["own"]["overall"] = -3.0
    report[0]["teams"]["A"]["own"]["normalized"]["brand_health"] = -4.0

    assert report[0]["teams"]["B"]["cohort_overall"]["best"] != -1.0
    assert report[0]["teams"]["B"]["cohort_dimension_average"]["brand_health"] != -2.0
    assert report[0]["teams"]["B"]["own"]["overall"] != -3.0
    assert report[0]["teams"]["B"]["own"]["normalized"]["brand_health"] != -4.0
    assert history == original


def test_build_report_open_ended_five_team_two_round_history():
    history = _short_history(team_count=5, round_count=2)
    report = build_report(history)

    assert len(report) == 2
    for record, round_report in zip(history, report, strict=True):
        assert round_report["round"] == record["round"]
        assert set(round_report["teams"]) == set(record["scorecard"]["firms"])
        assert len(round_report["teams"]) == 5
        assert set(round_report["teams"]) == {"North", "South", "East", "West", "Central"}
        for team_report in round_report["teams"].values():
            assert team_report["n_teams"] == 5
            assert set(team_report["own"]["normalized"]) == set(DIMENSIONS)


def test_build_report_allows_varying_team_counts_by_round():
    history = [
        {
            "round": 1,
            "scorecard": {
                "firms": {
                    "Solo": _score(100.0),
                    "Pair": _score(50.0),
                }
            },
        },
        {
            "round": 2,
            "scorecard": {
                "firms": {
                    "Solo": _score(100.0),
                    "Pair": _score(50.0),
                    "Trio": _score(25.0),
                }
            },
        },
    ]

    report = build_report(history)

    assert {team: team_report["n_teams"] for team, team_report in report[0]["teams"].items()} == {
        "Solo": 2,
        "Pair": 2,
    }
    assert {team: team_report["n_teams"] for team, team_report in report[1]["teams"].items()} == {
        "Solo": 3,
        "Pair": 3,
        "Trio": 3,
    }


@pytest.mark.parametrize(
    "bad_history",
    [
        "not history",
        [],
        [1],
        [{"scorecard": {"firms": {"A": _score()}}}],
        [{"round": 1.5, "scorecard": {"firms": {"A": _score()}}}],
        [{"round": True, "scorecard": {"firms": {"A": _score()}}}],
        [{"round": 1, "scorecard": {"firms": {"A": _score()}}}, {"round": 1, "scorecard": {"firms": {"B": _score()}}}],
        [{"round": 1}],
        [{"round": 1, "scorecard": []}],
        [{"round": 1, "scorecard": {}}],
        [{"round": 1, "scorecard": {"firms": []}}],
        [{"round": 1, "scorecard": {"firms": {}}}],
        [{"round": 1, "scorecard": {"firms": {"": _score()}}}],
        [{"round": 1, "scorecard": {"firms": {1: _score()}}}],
        [{"round": 1, "scorecard": {"firms": {"A": []}}}],
        [{"round": 1, "scorecard": {"firms": {"A": {"normalized": dict.fromkeys(DIMENSIONS, 1.0)}}}}],
        [{"round": 1, "scorecard": {"firms": {"A": _score("not numeric")}}}],
        [{"round": 1, "scorecard": {"firms": {"A": _score("nan")}}}],
        [{"round": 1, "scorecard": {"firms": {"A": _score(float("inf"))}}}],
        [{"round": 1, "scorecard": {"firms": {"A": _score(float("-inf"))}}}],
        [{"round": 1, "scorecard": {"firms": {"A": {"overall": 1.0}}}}],
        [{"round": 1, "scorecard": {"firms": {"A": {"overall": 1.0, "normalized": []}}}}],
        [{"round": 1, "scorecard": {"firms": {"A": _score(1.0, {"brand_health": 1.0})}}}],
        [{"round": 1, "scorecard": {"firms": {"A": _score(1.0, {**dict.fromkeys(DIMENSIONS, 1.0), "extra": 1.0})}}}],
        [{"round": 1, "scorecard": {"firms": {"A": _score(1.0, {**dict.fromkeys(DIMENSIONS, 1.0), "brand_health": "bad"})}}}],
        [{"round": 1, "scorecard": {"firms": {"A": _score(1.0, {**dict.fromkeys(DIMENSIONS, 1.0), "brand_health": float("nan")})}}}],
        [{"round": 1, "scorecard": {"firms": {"A": _score(1.0, {**dict.fromkeys(DIMENSIONS, 1.0), "brand_health": float("inf")})}}}],
        [{"round": 1, "scorecard": {"firms": {"A": _score(1.0, {**dict.fromkeys(DIMENSIONS, 1.0), "brand_health": float("-inf")})}}}],
    ],
)
def test_build_report_rejects_invalid_history_shapes(bad_history):
    with pytest.raises(ValueError):
        build_report(bad_history)
