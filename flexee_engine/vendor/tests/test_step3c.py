from copy import deepcopy
from dataclasses import replace

import pytest

from crisis import DEFAULT_CRISIS_PARAMS, crisis_damage, run_simulation_with_crisis
from engine import DEFAULT_PARAMS
from equity import DEFAULT_EQUITY_PARAMS, run_simulation_with_equity
from simulation import DEFAULT_GROWTH_RATES
from test_step1 import DECISIONS


TEAMS = ("A", "B", "C", "D")
BACKDROP = ("BigRed", "Monssta", "Pulse")
FIRMS = (*TEAMS, *BACKDROP)
SEGMENTS = ("LEG", "FIT", "FUN", "GENZ", "MAIN")
BASELINE_TRUST = 32.0
BASELINE_PROD = 30.0


CRISIS_INPUTS = {
    "A": {"exposure": 0.9, "preempt": 0.1, "response": 0.3},
    "B": {"exposure": 0.3, "preempt": 0.8, "response": 0.8},
    "C": {"exposure": 0.6, "preempt": 0.4, "response": 0.5},
    "D": {"exposure": 0.7, "preempt": 0.3, "response": 0.5},
}


def six_round_decisions():
    return [deepcopy(DECISIONS) for _ in range(6)]


def six_round_crisis_history():
    return run_simulation_with_crisis(
        six_round_decisions(),
        DEFAULT_PARAMS,
        DEFAULT_GROWTH_RATES,
        DEFAULT_EQUITY_PARAMS,
        DEFAULT_CRISIS_PARAMS,
        deepcopy(CRISIS_INPUTS),
    )


def params_with_replaced_attribute(old_attribute, new_attribute):
    return replace(
        DEFAULT_PARAMS,
        attributes=tuple(
            new_attribute if attribute == old_attribute else attribute
            for attribute in DEFAULT_PARAMS.attributes
        ),
    )


def test_crisis_damage_golden_values():
    history = six_round_crisis_history()
    expected_damage = {
        "A": 4.2155,
        "B": 0.0630,
        "C": 1.0878,
        "D": 1.3715,
    }

    assert history[3]["damage"].keys() == expected_damage.keys()
    for team, expected in expected_damage.items():
        assert history[3]["damage"][team] == pytest.approx(expected, abs=0.001)


def test_crisis_public_round_record_schema_and_damage_key():
    history = six_round_crisis_history()
    expected_keys = {"round", "segment_sizes", "firms", "scorecard", "equity", "attribute_state", "damage"}

    for index, round_record in enumerate(history, start=1):
        assert set(round_record) == expected_keys
        if index == DEFAULT_CRISIS_PARAMS.crisis_round:
            assert set(round_record["damage"]) == set(TEAMS)
        else:
            assert round_record["damage"] == {}


def test_crisis_attribute_state_golden_values():
    history = six_round_crisis_history()
    expected_trust_after = {
        "A": 29.8922,
        "B": 31.9685,
        "C": 31.4561,
        "D": 31.3142,
    }
    expected_prod_after = {
        "A": 28.7353,
        "B": 29.9811,
        "C": 29.6737,
        "D": 29.5885,
    }
    expected_eq = {
        "A": {0: 65.0211, 1: 67.2869, 2: 68.9862, 3: 69.4176, 5: 71.4593},
        "B": {0: 68.2150, 1: 72.8762, 2: 76.3721, 3: 78.9814, 5: 82.4282},
        "C": {0: 67.0570, 1: 70.8497, 2: 73.6942, 3: 75.6101, 5: 78.5054},
        "D": {0: 67.7080, 1: 71.9889, 2: 75.1997, 3: 77.3334, 5: 80.6140},
    }

    for round_index in range(3):
        for team in TEAMS:
            assert history[round_index]["attribute_state"][team]["TRUST"] == pytest.approx(BASELINE_TRUST)
            assert history[round_index]["attribute_state"][team]["PROD"] == pytest.approx(BASELINE_PROD)

    for round_index in range(3, 6):
        for team in TEAMS:
            assert history[round_index]["attribute_state"][team]["TRUST"] == pytest.approx(
                expected_trust_after[team],
                abs=0.01,
            )
            assert history[round_index]["attribute_state"][team]["PROD"] == pytest.approx(
                expected_prod_after[team],
                abs=0.01,
            )

    for team, expected_by_round_index in expected_eq.items():
        for round_index, expected in expected_by_round_index.items():
            assert history[round_index]["attribute_state"][team]["EQ"] == pytest.approx(expected, abs=0.01)
            assert history[round_index]["equity"][team] == pytest.approx(expected, abs=0.01)


def test_crisis_share_and_scorecard_golden_values():
    history = six_round_crisis_history()
    expected_share_pct = {
        3: {"A": 11.7702, "B": 11.8735, "C": 12.3503, "D": 12.1348},
        4: {"A": 11.5479, "B": 12.0335, "C": 12.3647, "D": 12.2022},
        6: {"A": 11.3845, "B": 12.2256, "C": 12.3831, "D": 12.3616},
    }
    expected_overall = {
        3: {"A": 0.00, "B": 58.90, "C": 76.86, "D": 74.63},
        4: {"A": 0.00, "B": 79.73, "C": 77.15, "D": 82.69},
        6: {"A": 0.00, "B": 92.11, "C": 77.02, "D": 91.79},
    }

    for round_number in (3, 4, 6):
        record = history[round_number - 1]
        for team in TEAMS:
            assert record["firms"][team]["total_market_share"] * 100.0 == pytest.approx(
                expected_share_pct[round_number][team],
                abs=0.01,
            )
            assert record["scorecard"]["firms"][team]["overall"] == pytest.approx(
                expected_overall[round_number][team],
                abs=0.1,
            )


def test_crisis_rounds_before_shock_match_equity_runner():
    crisis_history = six_round_crisis_history()
    equity_history = run_simulation_with_equity(
        six_round_decisions(),
        DEFAULT_PARAMS,
        DEFAULT_GROWTH_RATES,
        DEFAULT_EQUITY_PARAMS,
    )

    for round_index in range(DEFAULT_CRISIS_PARAMS.crisis_round - 1):
        assert crisis_history[round_index]["segment_sizes"] == equity_history[round_index]["segment_sizes"]
        assert crisis_history[round_index]["equity"] == equity_history[round_index]["equity"]
        for firm in FIRMS:
            assert crisis_history[round_index]["firms"][firm] == equity_history[round_index]["firms"][firm]
        assert crisis_history[round_index]["scorecard"] == equity_history[round_index]["scorecard"]


def test_crisis_outside_run_matches_equity_runner():
    crisis_params = replace(DEFAULT_CRISIS_PARAMS, crisis_round=99)
    crisis_history = run_simulation_with_crisis(
        six_round_decisions(),
        DEFAULT_PARAMS,
        DEFAULT_GROWTH_RATES,
        DEFAULT_EQUITY_PARAMS,
        crisis_params,
        deepcopy(CRISIS_INPUTS),
    )
    equity_history = run_simulation_with_equity(
        six_round_decisions(),
        DEFAULT_PARAMS,
        DEFAULT_GROWTH_RATES,
        DEFAULT_EQUITY_PARAMS,
    )

    for crisis_record, equity_record in zip(crisis_history, equity_history, strict=True):
        assert crisis_record["segment_sizes"] == equity_record["segment_sizes"]
        assert crisis_record["equity"] == equity_record["equity"]
        assert crisis_record["firms"] == equity_record["firms"]
        assert crisis_record["scorecard"] == equity_record["scorecard"]
        assert crisis_record["damage"] == {}
        for team in TEAMS:
            assert crisis_record["attribute_state"][team]["TRUST"] == pytest.approx(BASELINE_TRUST)
            assert crisis_record["attribute_state"][team]["PROD"] == pytest.approx(BASELINE_PROD)


def test_crisis_damage_ordering_and_attribute_scar_invariants():
    history = six_round_crisis_history()
    damage = history[3]["damage"]

    assert damage["A"] == max(damage.values())
    assert damage["B"] == min(damage.values())

    for team in ("B", "C", "D"):
        assert history[3]["attribute_state"]["A"]["TRUST"] < history[3]["attribute_state"][team]["TRUST"]
        assert history[3]["attribute_state"]["A"]["PROD"] < history[3]["attribute_state"][team]["PROD"]

    for team in TEAMS:
        crisis_state = history[3]["attribute_state"][team]
        assert crisis_state["TRUST"] < BASELINE_TRUST
        assert crisis_state["PROD"] < BASELINE_PROD
        for round_index in range(4, 6):
            assert history[round_index]["attribute_state"][team]["TRUST"] == pytest.approx(crisis_state["TRUST"])
            assert history[round_index]["attribute_state"][team]["PROD"] == pytest.approx(crisis_state["PROD"])


def test_crisis_state_matches_firm_attribute_overrides_each_round():
    history = six_round_crisis_history()

    for record in history:
        for team in TEAMS:
            assert record["equity"][team] == pytest.approx(record["attribute_state"][team]["EQ"])
            assert record["firms"][team]["eq"] == pytest.approx(record["attribute_state"][team]["EQ"])
            assert record["firms"][team]["attributes"]["EQ"] == pytest.approx(record["attribute_state"][team]["EQ"])
            assert record["firms"][team]["attributes"]["TRUST"] == pytest.approx(
                record["attribute_state"][team]["TRUST"]
            )
            assert record["firms"][team]["attributes"]["PROD"] == pytest.approx(
                record["attribute_state"][team]["PROD"]
            )


def test_crisis_segment_sizes_follow_growth_formula():
    history = six_round_crisis_history()
    base_sizes = {segment.name: segment.size for segment in DEFAULT_PARAMS.segments}

    for record in history:
        for segment in SEGMENTS:
            assert record["segment_sizes"][segment] == pytest.approx(
                base_sizes[segment] * (1.0 + DEFAULT_GROWTH_RATES[segment]) ** (record["round"] - 1)
            )


def test_crisis_reconciliation_for_segments_and_market_totals():
    history = six_round_crisis_history()

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


def test_crisis_purity_and_determinism():
    decisions_by_round = six_round_decisions()
    growth_rates = dict(DEFAULT_GROWTH_RATES)
    crisis_inputs = deepcopy(CRISIS_INPUTS)
    original_decisions_by_round = deepcopy(decisions_by_round)
    original_growth_rates = deepcopy(growth_rates)
    original_crisis_inputs = deepcopy(crisis_inputs)
    original_segment_sizes = tuple(segment.size for segment in DEFAULT_PARAMS.segments)

    first = run_simulation_with_crisis(
        decisions_by_round,
        DEFAULT_PARAMS,
        growth_rates,
        DEFAULT_EQUITY_PARAMS,
        DEFAULT_CRISIS_PARAMS,
        crisis_inputs,
    )
    second = run_simulation_with_crisis(
        decisions_by_round,
        DEFAULT_PARAMS,
        growth_rates,
        DEFAULT_EQUITY_PARAMS,
        DEFAULT_CRISIS_PARAMS,
        crisis_inputs,
    )

    assert first == second
    assert decisions_by_round == original_decisions_by_round
    assert growth_rates == original_growth_rates
    assert crisis_inputs == original_crisis_inputs
    assert tuple(segment.size for segment in DEFAULT_PARAMS.segments) == original_segment_sizes


def test_crisis_damage_function_formula_and_clipping_effect():
    assert crisis_damage(80.0, 0.5, 0.25, 0.1, DEFAULT_CRISIS_PARAMS) == pytest.approx(
        25.0 * 0.5 * 0.2 * 0.75 * 0.9
    )
    high_damage_params = replace(DEFAULT_CRISIS_PARAMS, damage_base=10000.0)
    history = run_simulation_with_crisis(
        six_round_decisions(),
        DEFAULT_PARAMS,
        DEFAULT_GROWTH_RATES,
        DEFAULT_EQUITY_PARAMS,
        high_damage_params,
        {team: {"exposure": 1.0, "preempt": 0.0, "response": 0.0} for team in TEAMS},
    )

    for team in TEAMS:
        assert history[3]["attribute_state"][team]["EQ"] == pytest.approx(high_damage_params.lower_bound)
        assert history[3]["attribute_state"][team]["TRUST"] == pytest.approx(high_damage_params.lower_bound)
        assert history[3]["attribute_state"][team]["PROD"] == pytest.approx(high_damage_params.lower_bound)


@pytest.mark.parametrize(
    "eq,exposure,preempt,response",
    (
        (80.0, 0.0, 0.0, 0.0),
        (80.0, 1.0, 1.0, 0.0),
        (80.0, 1.0, 0.0, 1.0),
        (100.0, 1.0, 0.0, 0.0),
    ),
)
def test_crisis_damage_zero_cases(eq, exposure, preempt, response):
    assert crisis_damage(eq, exposure, preempt, response, DEFAULT_CRISIS_PARAMS) == pytest.approx(0.0)


@pytest.mark.parametrize(
    "bad_inputs",
    (
        None,
        [],
        {"A": CRISIS_INPUTS["A"]},
        {**CRISIS_INPUTS, "Z": {"exposure": 0.1, "preempt": 0.1, "response": 0.1}},
        {**CRISIS_INPUTS, "A": ["not", "a", "mapping"]},
        {**CRISIS_INPUTS, "A": {"preempt": 0.1, "response": 0.1}},
        {**CRISIS_INPUTS, "A": {"exposure": 0.1, "response": 0.1}},
        {**CRISIS_INPUTS, "A": {"exposure": 0.1, "preempt": 0.1}},
        {**CRISIS_INPUTS, "A": {"exposure": "bad", "preempt": 0.1, "response": 0.1}},
        {**CRISIS_INPUTS, "A": {"exposure": 0.1, "preempt": "bad", "response": 0.1}},
        {**CRISIS_INPUTS, "A": {"exposure": 0.1, "preempt": 0.1, "response": "bad"}},
        {**CRISIS_INPUTS, "A": {"exposure": -0.1, "preempt": 0.1, "response": 0.1}},
        {**CRISIS_INPUTS, "A": {"exposure": 1.1, "preempt": 0.1, "response": 0.1}},
        {**CRISIS_INPUTS, "A": {"exposure": 0.1, "preempt": -0.1, "response": 0.1}},
        {**CRISIS_INPUTS, "A": {"exposure": 0.1, "preempt": 0.1, "response": 1.1}},
        {**CRISIS_INPUTS, "A": {"exposure": float("nan"), "preempt": 0.1, "response": 0.1}},
        {**CRISIS_INPUTS, "A": {"exposure": float("inf"), "preempt": 0.1, "response": 0.1}},
        {**CRISIS_INPUTS, "A": {"exposure": float("-inf"), "preempt": 0.1, "response": 0.1}},
        {**CRISIS_INPUTS, "A": {"exposure": 0.1, "preempt": float("nan"), "response": 0.1}},
        {**CRISIS_INPUTS, "A": {"exposure": 0.1, "preempt": float("inf"), "response": 0.1}},
        {**CRISIS_INPUTS, "A": {"exposure": 0.1, "preempt": float("-inf"), "response": 0.1}},
        {**CRISIS_INPUTS, "A": {"exposure": 0.1, "preempt": 0.1, "response": float("nan")}},
        {**CRISIS_INPUTS, "A": {"exposure": 0.1, "preempt": 0.1, "response": float("inf")}},
        {**CRISIS_INPUTS, "A": {"exposure": 0.1, "preempt": 0.1, "response": float("-inf")}},
    ),
)
def test_invalid_crisis_inputs_raise_value_error(bad_inputs):
    with pytest.raises(ValueError):
        run_simulation_with_crisis(
            six_round_decisions(),
            DEFAULT_PARAMS,
            DEFAULT_GROWTH_RATES,
            DEFAULT_EQUITY_PARAMS,
            DEFAULT_CRISIS_PARAMS,
            bad_inputs,
        )


@pytest.mark.parametrize(
    "params",
    (
        params_with_replaced_attribute("TRUST", "NOT_TRUST"),
        params_with_replaced_attribute("PROD", "NOT_PROD"),
        replace(DEFAULT_PARAMS, team_baseline_attributes=DEFAULT_PARAMS.team_baseline_attributes[:-1]),
        replace(DEFAULT_PARAMS, team_baseline_attributes=DEFAULT_PARAMS.team_baseline_attributes + (1.0,)),
    ),
)
def test_invalid_carried_attributes_or_baseline_shape_raise_value_error(params):
    with pytest.raises(ValueError):
        run_simulation_with_crisis(
            six_round_decisions(),
            params,
            DEFAULT_GROWTH_RATES,
            DEFAULT_EQUITY_PARAMS,
            DEFAULT_CRISIS_PARAMS,
            deepcopy(CRISIS_INPUTS),
        )


@pytest.mark.parametrize(
    "crisis_params",
    (
        replace(DEFAULT_CRISIS_PARAMS, crisis_round=0),
        replace(DEFAULT_CRISIS_PARAMS, crisis_round=1.5),
        replace(DEFAULT_CRISIS_PARAMS, damage_base=-0.1),
        replace(DEFAULT_CRISIS_PARAMS, trust_fraction=-0.1),
        replace(DEFAULT_CRISIS_PARAMS, prod_fraction=1.1),
        replace(DEFAULT_CRISIS_PARAMS, eq_fraction=float("nan")),
        replace(DEFAULT_CRISIS_PARAMS, lower_bound=100.0, upper_bound=0.0),
        replace(DEFAULT_CRISIS_PARAMS, trust_fraction=0.4),
        replace(DEFAULT_CRISIS_PARAMS, trust_fraction=0.6),
    ),
)
def test_invalid_crisis_params_raise_value_error(crisis_params):
    with pytest.raises(ValueError):
        crisis_damage(80.0, 0.5, 0.5, 0.5, crisis_params)


def test_team_set_must_remain_fixed_across_crisis_rounds():
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
        run_simulation_with_crisis(
            added,
            DEFAULT_PARAMS,
            DEFAULT_GROWTH_RATES,
            DEFAULT_EQUITY_PARAMS,
            DEFAULT_CRISIS_PARAMS,
            {**CRISIS_INPUTS, "E": {"exposure": 0.1, "preempt": 0.1, "response": 0.1}},
        )
    with pytest.raises(ValueError):
        run_simulation_with_crisis(
            removed,
            DEFAULT_PARAMS,
            DEFAULT_GROWTH_RATES,
            DEFAULT_EQUITY_PARAMS,
            DEFAULT_CRISIS_PARAMS,
            CRISIS_INPUTS,
        )
