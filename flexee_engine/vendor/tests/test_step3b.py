from copy import deepcopy
from dataclasses import replace

import pytest

from engine import DEFAULT_PARAMS, compute_round
from equity import DEFAULT_EQUITY_PARAMS, next_equity, run_simulation_with_equity
from simulation import DEFAULT_GROWTH_RATES
from test_step1 import DECISIONS


TEAMS = ("A", "B", "C", "D")
BACKDROP = ("BigRed", "Monssta", "Pulse")
FIRMS = (*TEAMS, *BACKDROP)
SEGMENTS = ("LEG", "FIT", "FUN", "GENZ", "MAIN")


def six_round_decisions():
    return [deepcopy(DECISIONS) for _ in range(6)]


def test_next_equity_golden_sequence():
    history = run_simulation_with_equity(
        six_round_decisions(),
        DEFAULT_PARAMS,
        DEFAULT_GROWTH_RATES,
        DEFAULT_EQUITY_PARAMS,
    )
    expected = {
        "A": (65.0211, 67.2869, 68.9862, 70.2607, 71.2166, 71.9335),
        "B": (68.2150, 72.8762, 76.3721, 78.9940, 80.9605, 82.4353),
        "C": (67.0570, 70.8497, 73.6942, 75.8276, 77.4277, 78.6277),
        "D": (67.7080, 71.9889, 75.1997, 77.6077, 79.4137, 80.7683),
    }

    for team, expected_values in expected.items():
        actual_values = tuple(round_record["equity"][team] for round_record in history)
        assert actual_values == pytest.approx(expected_values, abs=0.01)


def test_round_one_regression_with_equity_stock():
    round_one = run_simulation_with_equity(
        six_round_decisions(),
        DEFAULT_PARAMS,
        DEFAULT_GROWTH_RATES,
        DEFAULT_EQUITY_PARAMS,
    )[0]
    expected_share_pct = {
        "A": 11.9190,
        "B": 11.5261,
        "C": 12.2109,
        "D": 11.8429,
    }
    expected_overall = {
        "A": 28.69,
        "B": 50.00,
        "C": 76.87,
        "D": 66.37,
    }

    for team in TEAMS:
        assert round_one["firms"][team]["total_market_share"] * 100.0 == pytest.approx(
            expected_share_pct[team],
            abs=0.01,
        )
        assert round_one["scorecard"]["firms"][team]["overall"] == pytest.approx(
            expected_overall[team],
            abs=0.1,
        )


def test_round_six_regression_with_equity_stock():
    round_six = run_simulation_with_equity(
        six_round_decisions(),
        DEFAULT_PARAMS,
        DEFAULT_GROWTH_RATES,
        DEFAULT_EQUITY_PARAMS,
    )[5]
    expected_share_pct = {
        "A": 11.5040,
        "B": 12.1953,
        "C": 12.4003,
        "D": 12.3971,
    }
    expected_overall = {
        "A": 0.00,
        "B": 88.56,
        "C": 76.87,
        "D": 93.06,
    }

    for team in TEAMS:
        assert round_six["firms"][team]["total_market_share"] * 100.0 == pytest.approx(
            expected_share_pct[team],
            abs=0.01,
        )
        assert round_six["scorecard"]["firms"][team]["overall"] == pytest.approx(
            expected_overall[team],
            abs=0.1,
        )


def test_equity_stock_recurrence_invariants():
    history = run_simulation_with_equity(
        six_round_decisions(),
        DEFAULT_PARAMS,
        DEFAULT_GROWTH_RATES,
        DEFAULT_EQUITY_PARAMS,
    )

    for team in TEAMS:
        prev_eq = DEFAULT_EQUITY_PARAMS.eq_start
        for round_record in history:
            decision = DECISIONS[team]
            expected_eq = next_equity(
                prev_eq,
                decision["brand"],
                decision["perf"],
                DEFAULT_EQUITY_PARAMS,
                DEFAULT_PARAMS.budget,
            )
            assert round_record["equity"][team] == pytest.approx(expected_eq)
            assert round_record["firms"][team]["eq"] == pytest.approx(expected_eq)
            assert round_record["firms"][team]["attributes"]["EQ"] == pytest.approx(expected_eq)
            prev_eq = round_record["equity"][team]

        assert history[0]["equity"][team] == pytest.approx(
            next_equity(
                DEFAULT_EQUITY_PARAMS.eq_start,
                DECISIONS[team]["brand"],
                DECISIONS[team]["perf"],
                DEFAULT_EQUITY_PARAMS,
                DEFAULT_PARAMS.budget,
            )
        )


def test_brand_heavy_team_has_highest_round_six_equity():
    round_six_equity = run_simulation_with_equity(
        six_round_decisions(),
        DEFAULT_PARAMS,
        DEFAULT_GROWTH_RATES,
        DEFAULT_EQUITY_PARAMS,
    )[5]["equity"]

    assert round_six_equity["B"] == max(round_six_equity.values())
    assert round_six_equity["A"] == min(round_six_equity.values())
    assert round_six_equity["A"] > DEFAULT_EQUITY_PARAMS.eq_base


def test_compute_round_without_overrides_matches_default_call():
    assert compute_round(DECISIONS, DEFAULT_PARAMS, team_attribute_overrides=None) == compute_round(
        DECISIONS,
        DEFAULT_PARAMS,
    )


def test_compute_round_attribute_override_replaces_only_named_team_attribute():
    overridden = compute_round(
        DECISIONS,
        DEFAULT_PARAMS,
        team_attribute_overrides={"A": {"EQ": 80.0}},
    )
    baseline = compute_round(DECISIONS, DEFAULT_PARAMS)

    assert overridden["A"]["eq"] == pytest.approx(80.0)
    assert overridden["A"]["attributes"]["EQ"] == pytest.approx(80.0)
    for attribute, value in baseline["A"]["attributes"].items():
        if attribute != "EQ":
            assert overridden["A"]["attributes"][attribute] == pytest.approx(value)
    assert overridden["B"]["attributes"] == baseline["B"]["attributes"]


def test_compute_round_override_changes_perception_and_share():
    overridden = compute_round(
        DECISIONS,
        DEFAULT_PARAMS,
        team_attribute_overrides={"A": {"EQ": 80.0}},
    )
    baseline = compute_round(DECISIONS, DEFAULT_PARAMS)

    assert any(
        overridden["A"]["perception"][segment] != pytest.approx(baseline["A"]["perception"][segment])
        for segment in SEGMENTS
    )
    assert any(
        overridden["A"]["share"][segment] != pytest.approx(baseline["A"]["share"][segment])
        for segment in SEGMENTS
    )


def test_compute_round_general_and_multiple_team_overrides():
    overrides = {"A": {"PROD": 44.0}, "B": {"EQ": 77.0, "TRUST": 66.0}}
    original_overrides = deepcopy(overrides)
    overridden = compute_round(DECISIONS, DEFAULT_PARAMS, team_attribute_overrides=overrides)
    baseline = compute_round(DECISIONS, DEFAULT_PARAMS)

    assert overridden["A"]["attributes"]["PROD"] == pytest.approx(44.0)
    assert overridden["B"]["attributes"]["EQ"] == pytest.approx(77.0)
    assert overridden["B"]["attributes"]["TRUST"] == pytest.approx(66.0)
    for attribute, value in baseline["A"]["attributes"].items():
        if attribute != "PROD":
            assert overridden["A"]["attributes"][attribute] == pytest.approx(value)
    assert overrides == original_overrides


@pytest.mark.parametrize(
    "overrides",
    (
        {"Z": {"EQ": 70.0}},
        {"BigRed": {"EQ": 70.0}},
        {"A": {"UNKNOWN": 70.0}},
        {"A": {"EQ": "bad"}},
        {"A": {"EQ": float("nan")}},
        {"A": {"EQ": float("inf")}},
        {"A": {"EQ": float("-inf")}},
        {"A": 70.0},
        [("A", {"EQ": 70.0})],
    ),
)
def test_invalid_engine_overrides_raise_value_error(overrides):
    original_overrides = deepcopy(overrides)

    with pytest.raises(ValueError):
        compute_round(DECISIONS, DEFAULT_PARAMS, team_attribute_overrides=overrides)

    assert overrides == original_overrides


def test_run_simulation_with_equity_record_shape_and_team_scorecard():
    history = run_simulation_with_equity(
        six_round_decisions(),
        DEFAULT_PARAMS,
        DEFAULT_GROWTH_RATES,
        DEFAULT_EQUITY_PARAMS,
    )

    assert len(history) == 6
    assert [round_record["round"] for round_record in history] == [1, 2, 3, 4, 5, 6]
    for round_record in history:
        assert set(round_record) == {"round", "segment_sizes", "firms", "scorecard", "equity"}
        assert set(round_record["equity"]) == set(TEAMS)
        assert set(round_record["scorecard"]["firms"]) == set(TEAMS)
        for backdrop in BACKDROP:
            assert backdrop not in round_record["scorecard"]["firms"]


def test_reconciliation_for_segments_and_market_totals_with_equity():
    history = run_simulation_with_equity(
        six_round_decisions(),
        DEFAULT_PARAMS,
        DEFAULT_GROWTH_RATES,
        DEFAULT_EQUITY_PARAMS,
    )

    for round_record in history:
        for segment in SEGMENTS:
            assert sum(round_record["firms"][firm]["share"][segment] for firm in FIRMS) == pytest.approx(
                1.0,
                abs=1e-12,
            )
            assert sum(round_record["firms"][firm]["volume"][segment] for firm in FIRMS) == pytest.approx(
                round_record["segment_sizes"][segment],
                abs=1e-9,
            )
        assert sum(round_record["firms"][firm]["total_volume"] for firm in FIRMS) == pytest.approx(
            sum(round_record["segment_sizes"].values()),
            abs=1e-9,
        )


def test_segment_sizes_follow_chunk3a_growth_formula():
    history = run_simulation_with_equity(
        six_round_decisions(),
        DEFAULT_PARAMS,
        DEFAULT_GROWTH_RATES,
        DEFAULT_EQUITY_PARAMS,
    )
    base_sizes = {segment.name: segment.size for segment in DEFAULT_PARAMS.segments}

    for round_record in history:
        for segment in SEGMENTS:
            assert round_record["segment_sizes"][segment] == pytest.approx(
                base_sizes[segment] * (1.0 + DEFAULT_GROWTH_RATES[segment]) ** (round_record["round"] - 1)
            )


def test_equity_changes_round_six_within_segment_share_from_round_one():
    history = run_simulation_with_equity(
        six_round_decisions(),
        DEFAULT_PARAMS,
        DEFAULT_GROWTH_RATES,
        DEFAULT_EQUITY_PARAMS,
    )

    assert any(
        history[5]["firms"][firm]["share"][segment] != pytest.approx(history[0]["firms"][firm]["share"][segment])
        for firm in FIRMS
        for segment in SEGMENTS
    )


def test_purity_and_determinism():
    decisions_by_round = six_round_decisions()
    growth_rates = dict(DEFAULT_GROWTH_RATES)
    equity_params = DEFAULT_EQUITY_PARAMS
    original_decisions_by_round = deepcopy(decisions_by_round)
    original_growth_rates = deepcopy(growth_rates)
    original_segment_sizes = tuple(segment.size for segment in DEFAULT_PARAMS.segments)

    first = run_simulation_with_equity(
        decisions_by_round,
        DEFAULT_PARAMS,
        growth_rates,
        equity_params,
    )
    second = run_simulation_with_equity(
        decisions_by_round,
        DEFAULT_PARAMS,
        growth_rates,
        equity_params,
    )

    assert first == second
    assert decisions_by_round == original_decisions_by_round
    assert growth_rates == original_growth_rates
    assert equity_params == DEFAULT_EQUITY_PARAMS
    assert tuple(segment.size for segment in DEFAULT_PARAMS.segments) == original_segment_sizes


def test_changed_decision_round_uses_changed_brand_perf_for_equity():
    decisions_by_round = [deepcopy(DECISIONS), deepcopy(DECISIONS)]
    decisions_by_round[1]["A"] = {
        "brand": 6,
        "perf": 3,
        "research": 1,
        "positioning": 20,
        "emphasis": (0.45, 0.15, 0.05, 0.05, 0.30),
    }

    changed_history = run_simulation_with_equity(
        decisions_by_round,
        DEFAULT_PARAMS,
        DEFAULT_GROWTH_RATES,
        DEFAULT_EQUITY_PARAMS,
    )
    unchanged_history = run_simulation_with_equity(
        six_round_decisions()[:2],
        DEFAULT_PARAMS,
        DEFAULT_GROWTH_RATES,
        DEFAULT_EQUITY_PARAMS,
    )

    expected_changed = next_equity(
        changed_history[0]["equity"]["A"],
        6,
        3,
        DEFAULT_EQUITY_PARAMS,
        DEFAULT_PARAMS.budget,
    )
    assert changed_history[1]["equity"]["A"] == pytest.approx(expected_changed)
    assert changed_history[1]["equity"]["A"] != pytest.approx(unchanged_history[1]["equity"]["A"])


def test_team_set_must_remain_fixed_across_equity_rounds():
    added = [deepcopy(DECISIONS), deepcopy(DECISIONS)]
    added[1]["E"] = {
        "brand": 2,
        "perf": 5,
        "research": 3,
        "positioning": 35,
        "emphasis": (0.30, 0.20, 0.10, 0.10, 0.30),
    }
    removed = [deepcopy(DECISIONS), deepcopy(DECISIONS)]
    removed[1].pop("D")

    with pytest.raises(ValueError):
        run_simulation_with_equity(added, DEFAULT_PARAMS, DEFAULT_GROWTH_RATES, DEFAULT_EQUITY_PARAMS)
    with pytest.raises(ValueError):
        run_simulation_with_equity(removed, DEFAULT_PARAMS, DEFAULT_GROWTH_RATES, DEFAULT_EQUITY_PARAMS)


def test_next_equity_clips_to_bounds():
    upper_params = replace(DEFAULT_EQUITY_PARAMS, a_brand=200.0, upper_bound=100.0)
    lower_params = replace(DEFAULT_EQUITY_PARAMS, lower_bound=0.0)

    assert next_equity(99.0, 100.0, 0.0, upper_params, DEFAULT_PARAMS.budget) == pytest.approx(100.0)
    assert next_equity(1.0, 0.0, 100.0, lower_params, DEFAULT_PARAMS.budget) == pytest.approx(0.0)


@pytest.mark.parametrize(
    "prev_eq,brand,perf,equity_params",
    (
        (62.0, -0.1, 3.0, DEFAULT_EQUITY_PARAMS),
        (62.0, 3.0, -0.1, DEFAULT_EQUITY_PARAMS),
        (-0.1, 3.0, 6.0, DEFAULT_EQUITY_PARAMS),
        (100.1, 3.0, 6.0, DEFAULT_EQUITY_PARAMS),
        (62.0, 3.0, 6.0, replace(DEFAULT_EQUITY_PARAMS, eq_start=-0.1)),
        (62.0, 3.0, 6.0, replace(DEFAULT_EQUITY_PARAMS, eq_start=100.1)),
        (62.0, 3.0, 6.0, replace(DEFAULT_EQUITY_PARAMS, eq_base=-0.1)),
        (62.0, 3.0, 6.0, replace(DEFAULT_EQUITY_PARAMS, eq_base=100.1)),
    ),
)
def test_invalid_next_equity_inputs_and_states_raise_value_error(prev_eq, brand, perf, equity_params):
    with pytest.raises(ValueError):
        next_equity(prev_eq, brand, perf, equity_params, DEFAULT_PARAMS.budget)


@pytest.mark.parametrize(
    "decisions_by_round",
    (
        [],
        [{"A": {**DECISIONS["A"], "brand": "bad"}}],
        [{"A": {**DECISIONS["A"], "perf": "bad"}}],
        [{"A": {**DECISIONS["A"], "brand": float("nan")}}],
        [{"A": {**DECISIONS["A"], "perf": float("inf")}}],
        [{"A": {**DECISIONS["A"], "brand": -1}}],
        [{"A": {**DECISIONS["A"], "perf": -1}}],
    ),
)
def test_invalid_equity_runner_inputs_raise_value_error(decisions_by_round):
    with pytest.raises(ValueError):
        run_simulation_with_equity(decisions_by_round, DEFAULT_PARAMS, DEFAULT_GROWTH_RATES, DEFAULT_EQUITY_PARAMS)


@pytest.mark.parametrize(
    "budget,equity_params",
    (
        (0.0, DEFAULT_EQUITY_PARAMS),
        (DEFAULT_PARAMS.budget, replace(DEFAULT_EQUITY_PARAMS, k_brand=0.0)),
        (DEFAULT_PARAMS.budget, replace(DEFAULT_EQUITY_PARAMS, lower_bound=100.0, upper_bound=0.0)),
        (DEFAULT_PARAMS.budget, replace(DEFAULT_EQUITY_PARAMS, lambda_decay=-0.1)),
        (DEFAULT_PARAMS.budget, replace(DEFAULT_EQUITY_PARAMS, eq_base=float("nan"))),
        (DEFAULT_PARAMS.budget, replace(DEFAULT_EQUITY_PARAMS, eq_start=float("inf"))),
        (DEFAULT_PARAMS.budget, replace(DEFAULT_EQUITY_PARAMS, a_brand=float("-inf"))),
        (DEFAULT_PARAMS.budget, replace(DEFAULT_EQUITY_PARAMS, b_perfskew=float("nan"))),
        (DEFAULT_PARAMS.budget, replace(DEFAULT_EQUITY_PARAMS, tau=float("inf"))),
    ),
)
def test_invalid_next_equity_parameters_raise_value_error(budget, equity_params):
    with pytest.raises(ValueError):
        next_equity(62.0, 3.0, 6.0, equity_params, budget)


def test_invalid_growth_rates_raise_value_error():
    with pytest.raises(ValueError):
        run_simulation_with_equity(
            six_round_decisions(),
            DEFAULT_PARAMS,
            {"LEG": -1.0, "FIT": 0.003, "FUN": 0.025, "GENZ": 0.04, "MAIN": 0.005},
            DEFAULT_EQUITY_PARAMS,
        )
