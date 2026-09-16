from copy import deepcopy

import pytest

from engine import DEFAULT_PARAMS, compute_round
from scorecard import DEFAULT_SCORECARD_PARAMS, ScorecardParams, compute_scorecard
from test_step1 import DECISIONS


TEAMS = ("A", "B", "C", "D")
PERSPECTIVES = ("brand_health", "customer_value", "market_position", "financial")


def make_params(**overrides):
    values = {
        "perspectives": DEFAULT_SCORECARD_PARAMS.perspectives,
        "perspective_weights": dict(DEFAULT_SCORECARD_PARAMS.perspective_weights),
        "raw_metric_fields": dict(DEFAULT_SCORECARD_PARAMS.raw_metric_fields),
        "raw_metric_scales": dict(DEFAULT_SCORECARD_PARAMS.raw_metric_scales),
        "normalization_method": DEFAULT_SCORECARD_PARAMS.normalization_method,
        "team_firm_type": DEFAULT_SCORECARD_PARAMS.team_firm_type,
        "tie_normalized_score": DEFAULT_SCORECARD_PARAMS.tie_normalized_score,
    }
    values.update(overrides)
    return ScorecardParams(**values)


def test_scorecard_golden_single_round_values():
    round_results = compute_round(DECISIONS, DEFAULT_PARAMS)
    scorecard = compute_scorecard(round_results)

    expected_raw = {
        "A": (69.915, 5.386, 12.075, 0.868),
        "B": (73.653, 14.845, 11.689, 0.520),
        "C": (71.482, 9.487, 12.326, 1.093),
        "D": (72.702, 13.755, 11.983, 0.785),
    }
    expected_normalized = {
        "A": (0.0, 0.0, 60.7, 60.7),
        "B": (100.0, 100.0, 0.0, 0.0),
        "C": (41.9, 43.4, 100.0, 100.0),
        "D": (74.6, 88.5, 46.1, 46.1),
    }
    expected_overall = {
        "A": 30.3,
        "B": 50.0,
        "C": 71.3,
        "D": 63.8,
    }

    for team in TEAMS:
        for perspective, expected in zip(PERSPECTIVES, expected_raw[team], strict=True):
            assert scorecard["firms"][team]["raw"][perspective] == pytest.approx(expected, abs=0.001)
        for perspective, expected in zip(PERSPECTIVES, expected_normalized[team], strict=True):
            assert scorecard["firms"][team]["normalized"][perspective] == pytest.approx(expected, abs=0.1)
        assert scorecard["firms"][team]["overall"] == pytest.approx(expected_overall[team], abs=0.1)


def test_scorecard_contains_teams_only_and_industry_summary():
    scorecard = compute_scorecard(compute_round(DECISIONS, DEFAULT_PARAMS))

    assert set(scorecard["firms"]) == set(TEAMS)
    assert "BigRed" not in scorecard["firms"]
    assert set(scorecard["industry"]) == {"overall", *PERSPECTIVES}

    for perspective, summary in scorecard["industry"].items():
        assert set(summary) == {"best", "average", "worst"}
        values = [
            firm_scorecard["overall"] if perspective == "overall" else firm_scorecard["normalized"][perspective]
            for firm_scorecard in scorecard["firms"].values()
        ]
        assert summary["best"] == pytest.approx(max(values))
        assert summary["average"] == pytest.approx(sum(values) / len(values))
        assert summary["worst"] == pytest.approx(min(values))


def test_published_golden_values():
    scorecard = compute_scorecard(compute_round(DECISIONS, DEFAULT_PARAMS))

    expected = {
        "overall": {"best": 71.32, "average": 53.87, "worst": 30.34},
        "brand_health": {"best": 100.00, "average": 54.12, "worst": 0.00},
        "customer_value": {"best": 100.00, "average": 57.96, "worst": 0.00},
        "market_position": {"best": 100.00, "average": 51.70, "worst": 0.00},
        "financial": {"best": 100.00, "average": 51.70, "worst": 0.00},
    }

    for perspective, summary in expected.items():
        for field, value in summary.items():
            assert scorecard["industry"][perspective][field] == pytest.approx(value, abs=0.1)


def test_min_and_max_raw_scores_normalize_to_bounds():
    scorecard = compute_scorecard(compute_round(DECISIONS, DEFAULT_PARAMS))

    for perspective in PERSPECTIVES:
        raw_values = {
            team: scorecard["firms"][team]["raw"][perspective]
            for team in TEAMS
        }
        max_raw = max(raw_values.values())
        min_raw = min(raw_values.values())

        for team, raw_value in raw_values.items():
            normalized = scorecard["firms"][team]["normalized"][perspective]
            assert 0.0 <= normalized <= 100.0
            if raw_value == max_raw:
                assert normalized == pytest.approx(100.0)
            if raw_value == min_raw:
                assert normalized == pytest.approx(0.0)


def test_scorecard_overall_uses_configured_equal_weights():
    scorecard = compute_scorecard(compute_round(DECISIONS, DEFAULT_PARAMS), DEFAULT_SCORECARD_PARAMS)

    for firm_scorecard in scorecard["firms"].values():
        expected = sum(
            firm_scorecard["normalized"][perspective]
            * DEFAULT_SCORECARD_PARAMS.perspective_weights[perspective]
            for perspective in DEFAULT_SCORECARD_PARAMS.perspectives
        )
        assert firm_scorecard["overall"] == pytest.approx(expected)


def test_scorecard_config_declares_min_max_normalization_method():
    assert DEFAULT_SCORECARD_PARAMS.normalization_method == "minmax"


def test_default_config_mappings_are_read_only():
    with pytest.raises(TypeError):
        DEFAULT_SCORECARD_PARAMS.perspective_weights["brand_health"] = 1.0  # type: ignore[index]
    with pytest.raises(TypeError):
        DEFAULT_SCORECARD_PARAMS.raw_metric_fields["brand_health"] = "other"  # type: ignore[index]
    with pytest.raises(TypeError):
        DEFAULT_SCORECARD_PARAMS.raw_metric_scales["brand_health"] = 2.0  # type: ignore[index]


def test_scorecard_rejects_unsupported_normalization_method():
    for method in ("min_max", "percentile", "unsupported"):
        params = make_params(normalization_method=method)

        with pytest.raises(ValueError):
            compute_scorecard(compute_round(DECISIONS, DEFAULT_PARAMS), params)


def test_scorecard_is_deterministic_and_does_not_mutate_round_results():
    round_results = compute_round(DECISIONS, DEFAULT_PARAMS)
    original_round_results = deepcopy(round_results)

    first = compute_scorecard(round_results)
    second = compute_scorecard(round_results)

    assert first == second
    assert round_results == original_round_results


def test_tied_perspective_normalizes_to_configured_midpoint():
    round_results = compute_round(DECISIONS, DEFAULT_PARAMS)
    tied_results = deepcopy(round_results)
    for team in TEAMS:
        tied_results[team]["eq"] = 70.0

    scorecard = compute_scorecard(tied_results)

    for team in TEAMS:
        assert scorecard["firms"][team]["normalized"]["brand_health"] == pytest.approx(
            DEFAULT_SCORECARD_PARAMS.tie_normalized_score
        )


def test_full_degenerate_case_normalizes_everything_to_midpoint():
    round_results = {
        "A": {
            "firm_type": "team",
            "eq": 70.0,
            "growth_segment_share": 0.12,
            "total_market_share": 0.10,
            "contribution": 1.0,
        },
        "B": {
            "firm_type": "team",
            "eq": 70.0,
            "growth_segment_share": 0.12,
            "total_market_share": 0.10,
            "contribution": 1.0,
        },
    }

    scorecard = compute_scorecard(round_results)

    for firm_scorecard in scorecard["firms"].values():
        for perspective in PERSPECTIVES:
            assert firm_scorecard["normalized"][perspective] == pytest.approx(50.0)
        assert firm_scorecard["overall"] == pytest.approx(50.0)

    for perspective in ("overall", *PERSPECTIVES):
        assert scorecard["industry"][perspective]["best"] == pytest.approx(50.0)
        assert scorecard["industry"][perspective]["average"] == pytest.approx(50.0)
        assert scorecard["industry"][perspective]["worst"] == pytest.approx(50.0)


def test_scorecard_accepts_a_fifth_team_without_structure_changes():
    decisions = deepcopy(DECISIONS)
    decisions["E"] = {
        "brand": 2,
        "perf": 5,
        "research": 3,
        "positioning": 35,
        "emphasis": (0.30, 0.20, 0.10, 0.10, 0.30),
    }

    scorecard = compute_scorecard(compute_round(decisions, DEFAULT_PARAMS))

    assert set(scorecard["firms"]) == {"A", "B", "C", "D", "E"}
    assert scorecard["industry"]["overall"]["best"] >= scorecard["industry"]["overall"]["average"]
    assert scorecard["industry"]["overall"]["average"] >= scorecard["industry"]["overall"]["worst"]


@pytest.mark.parametrize(
    "bad_results",
    (
        {},
        {"BigRed": {"firm_type": "backdrop", "eq": 85.0}},
        {"A": {"firm_type": "team", "growth_segment_share": 0.1, "total_market_share": 0.1, "contribution": 1.0}},
        {"A": {"firm_type": "team", "eq": float("nan"), "growth_segment_share": 0.1, "total_market_share": 0.1, "contribution": 1.0}},
    ),
)
def test_scorecard_rejects_invalid_round_results(bad_results):
    with pytest.raises(ValueError):
        compute_scorecard(bad_results)


@pytest.mark.parametrize(
    "params",
    (
        make_params(perspectives=()),
        make_params(perspectives=("brand_health", "brand_health")),
        make_params(perspective_weights={"customer_value": 0.25, "market_position": 0.25, "financial": 0.25}),
        make_params(
            perspective_weights={
                "brand_health": 0.25,
                "customer_value": 0.25,
                "market_position": 0.25,
                "financial": 0.25,
                "extra": 0.0,
            }
        ),
        make_params(
            perspective_weights={
                "brand_health": -0.25,
                "customer_value": 0.25,
                "market_position": 0.25,
                "financial": 0.75,
            }
        ),
        make_params(
            perspective_weights={
                "brand_health": float("inf"),
                "customer_value": 0.25,
                "market_position": 0.25,
                "financial": 0.25,
            }
        ),
        make_params(
            perspective_weights={
                "brand_health": 0.10,
                "customer_value": 0.20,
                "market_position": 0.30,
                "financial": 0.30,
            }
        ),
        make_params(raw_metric_fields={"customer_value": "growth_segment_share", "market_position": "total_market_share", "financial": "contribution"}),
        make_params(
            raw_metric_scales={
                "brand_health": 1.0,
                "customer_value": 100.0,
                "market_position": 100.0,
                "financial": 1.0,
                "extra": 1.0,
            }
        ),
        make_params(raw_metric_scales={"brand_health": 1.0, "customer_value": 100.0, "market_position": 100.0}),
        make_params(raw_metric_scales={"brand_health": 1.0, "customer_value": float("nan"), "market_position": 100.0, "financial": 1.0}),
        make_params(tie_normalized_score=-1.0),
        make_params(tie_normalized_score=101.0),
        make_params(tie_normalized_score=float("nan")),
    ),
)
def test_invalid_scorecard_configuration_raises_value_error(params):
    with pytest.raises(ValueError):
        compute_scorecard(compute_round(DECISIONS, DEFAULT_PARAMS), params)
