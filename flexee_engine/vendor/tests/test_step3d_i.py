from copy import deepcopy
from dataclasses import replace

import pytest

from engine import DEFAULT_PARAMS, Segment, compute_round
from simulation import _params_with_segment_sizes
from test_step1 import DECISIONS, FIRMS, SEGMENTS


PRICED_DECISIONS = deepcopy(DECISIONS)
PRICED_DECISIONS["A"]["price"] = 1.20
PRICED_DECISIONS["B"]["price"] = 1.75
PRICED_DECISIONS["C"]["price"] = 1.50
PRICED_DECISIONS["D"]["price"] = 1.40


def test_pricing_golden_values():
    results = compute_round(PRICED_DECISIONS, DEFAULT_PARAMS)
    expected_perception = {
        "A": (80.662, 48.175, 30.521, 45.929, 70.741),
        "B": (46.199, 56.318, 69.534, 67.552, 54.163),
        "C": (64.420, 66.048, 53.222, 61.974, 73.348),
        "D": (56.059, 58.237, 60.489, 73.785, 65.359),
    }
    expected_share_pct = {
        "A": 11.9252,
        "B": 11.2862,
        "C": 12.4214,
        "D": 11.9996,
    }
    expected_revenue = {
        "A": 14.3103,
        "B": 19.7508,
        "C": 18.6320,
        "D": 16.7995,
    }
    expected_contribution = {
        "A": -2.8449,
        "B": 2.9791,
        "C": 1.1792,
        "D": -0.4003,
    }

    for team, expected_values in expected_perception.items():
        for segment, expected in zip(SEGMENTS, expected_values, strict=True):
            assert results[team]["perception"][segment] == pytest.approx(expected, abs=0.01)

    for team in ("A", "B", "C", "D"):
        assert results[team]["total_market_share"] * 100.0 == pytest.approx(expected_share_pct[team], abs=0.01)
        assert results[team]["revenue"] == pytest.approx(expected_revenue[team], abs=0.01)
        assert results[team]["contribution"] == pytest.approx(expected_contribution[team], abs=0.01)


def test_no_price_field_preserves_chunk1_golden_behavior():
    results = compute_round(DECISIONS, DEFAULT_PARAMS)
    expected_perception = {
        "A": (79.8, 52.3, 37.2, 47.6, 68.2),
        "B": (50.7, 56.7, 67.8, 70.0, 60.1),
        "C": (64.4, 66.0, 53.2, 62.0, 73.3),
        "D": (55.2, 59.1, 62.2, 73.8, 64.0),
        "BigRed": (75.5, 80.2, 67.5, 76.5, 84.5),
        "Monssta": (77.2, 72.5, 59.4, 69.7, 83.0),
        "Pulse": (43.8, 73.3, 88.7, 80.3, 54.0),
    }
    expected_totals = {
        "A": (12.08, 12.1, 5.4),
        "B": (11.69, 11.7, 14.8),
        "C": (12.33, 12.3, 9.5),
        "D": (11.98, 12.0, 13.8),
        "BigRed": (18.42, 18.4, 16.1),
        "Monssta": (16.36, 16.4, 12.2),
        "Pulse": (17.14, 17.1, 28.3),
    }

    for firm in FIRMS:
        for segment, expected in zip(SEGMENTS, expected_perception[firm], strict=True):
            assert results[firm]["perception"][segment] == pytest.approx(expected, abs=0.1)
        total_volume, total_share_pct, growth_share_pct = expected_totals[firm]
        assert results[firm]["total_volume"] == pytest.approx(total_volume, abs=0.1)
        assert results[firm]["total_market_share"] * 100.0 == pytest.approx(total_share_pct, abs=0.1)
        assert results[firm]["growth_segment_share"] * 100.0 == pytest.approx(growth_share_pct, abs=0.1)


def test_no_price_and_explicit_reference_prices_are_exactly_equal():
    explicit_reference = deepcopy(DECISIONS)
    for decision in explicit_reference.values():
        decision["price"] = DEFAULT_PARAMS.price

    assert compute_round(DECISIONS, DEFAULT_PARAMS) == compute_round(explicit_reference, DEFAULT_PARAMS)


def test_reference_price_has_zero_perception_increment_for_team_c():
    baseline = compute_round(DECISIONS, DEFAULT_PARAMS)
    priced = compute_round(PRICED_DECISIONS, DEFAULT_PARAMS)

    for segment in SEGMENTS:
        assert priced["C"]["perception"][segment] == pytest.approx(baseline["C"]["perception"][segment])


def test_price_cross_pressure_and_backdrops_are_unchanged():
    baseline = compute_round(DECISIONS, DEFAULT_PARAMS)
    priced = compute_round(PRICED_DECISIONS, DEFAULT_PARAMS)

    assert priced["A"]["perception"]["MAIN"] > baseline["A"]["perception"]["MAIN"]
    assert priced["A"]["perception"]["LEG"] > baseline["A"]["perception"]["LEG"]
    assert priced["A"]["perception"]["FUN"] < baseline["A"]["perception"]["FUN"]

    assert priced["B"]["perception"]["FUN"] > baseline["B"]["perception"]["FUN"]
    assert priced["B"]["perception"]["LEG"] < baseline["B"]["perception"]["LEG"]

    for backdrop in ("BigRed", "Monssta", "Pulse"):
        assert priced[backdrop]["perception"] == baseline[backdrop]["perception"]


def test_segment_shares_and_volumes_still_reconcile_with_pricing():
    results = compute_round(PRICED_DECISIONS, DEFAULT_PARAMS)

    for segment in DEFAULT_PARAMS.segments:
        assert sum(results[firm]["share"][segment.name] for firm in FIRMS) == pytest.approx(1.0)
        assert sum(results[firm]["volume"][segment.name] for firm in FIRMS) == pytest.approx(segment.size)


def test_priced_financial_identities_use_team_price_and_backdrops_reference_price():
    results = compute_round(PRICED_DECISIONS, DEFAULT_PARAMS)
    expected_prices = {
        "A": 1.20,
        "B": 1.75,
        "C": 1.50,
        "D": 1.40,
        "BigRed": DEFAULT_PARAMS.price,
        "Monssta": DEFAULT_PARAMS.price,
        "Pulse": DEFAULT_PARAMS.price,
    }

    for firm, price in expected_prices.items():
        total_volume = results[firm]["total_volume"]
        assert results[firm]["revenue"] == pytest.approx(total_volume * price)
        assert results[firm]["contribution"] == pytest.approx(
            total_volume * (price - DEFAULT_PARAMS.variable_cost) - DEFAULT_PARAMS.budget
        )


def test_pricing_is_deterministic_and_does_not_mutate_inputs_or_default_params():
    decisions = deepcopy(PRICED_DECISIONS)
    original_decisions = deepcopy(decisions)
    original_params = (
        DEFAULT_PARAMS.attributes,
        DEFAULT_PARAMS.segments,
        DEFAULT_PARAMS.positioning_bonus_max,
        DEFAULT_PARAMS.targeting_bonus_coef,
        DEFAULT_PARAMS.eq_start,
        DEFAULT_PARAMS.mu,
        DEFAULT_PARAMS.eq_gain_max,
        DEFAULT_PARAMS.eq_k,
        DEFAULT_PARAMS.price,
        DEFAULT_PARAMS.variable_cost,
        DEFAULT_PARAMS.value_fit_width,
        DEFAULT_PARAMS.value_fit_vp,
        DEFAULT_PARAMS.price_min,
        DEFAULT_PARAMS.price_max,
        DEFAULT_PARAMS.budget,
        DEFAULT_PARAMS.team_baseline_attributes,
        tuple(DEFAULT_PARAMS.backdrop_firms.items()),
        DEFAULT_PARAMS.growth_segments,
    )

    first = compute_round(decisions, DEFAULT_PARAMS)
    second = compute_round(decisions, DEFAULT_PARAMS)

    assert first == second
    assert decisions == original_decisions
    assert (
        DEFAULT_PARAMS.attributes,
        DEFAULT_PARAMS.segments,
        DEFAULT_PARAMS.positioning_bonus_max,
        DEFAULT_PARAMS.targeting_bonus_coef,
        DEFAULT_PARAMS.eq_start,
        DEFAULT_PARAMS.mu,
        DEFAULT_PARAMS.eq_gain_max,
        DEFAULT_PARAMS.eq_k,
        DEFAULT_PARAMS.price,
        DEFAULT_PARAMS.variable_cost,
        DEFAULT_PARAMS.value_fit_width,
        DEFAULT_PARAMS.value_fit_vp,
        DEFAULT_PARAMS.price_min,
        DEFAULT_PARAMS.price_max,
        DEFAULT_PARAMS.budget,
        DEFAULT_PARAMS.team_baseline_attributes,
        tuple(DEFAULT_PARAMS.backdrop_firms.items()),
        DEFAULT_PARAMS.growth_segments,
    ) == original_params


@pytest.mark.parametrize("price", (1.09, 1.91, float("nan"), float("inf"), float("-inf"), "bad"))
def test_invalid_price_raises_value_error_and_does_not_mutate(price):
    decisions = deepcopy(PRICED_DECISIONS)
    decisions["A"]["price"] = price
    original = deepcopy(decisions)

    with pytest.raises(ValueError):
        compute_round(decisions, DEFAULT_PARAMS)

    assert decisions == original


@pytest.mark.parametrize(
    "params",
    (
        replace(DEFAULT_PARAMS, value_fit_width=0.0),
        replace(DEFAULT_PARAMS, value_fit_width=-0.1),
        replace(DEFAULT_PARAMS, value_fit_width=float("nan")),
        replace(DEFAULT_PARAMS, value_fit_width=float("inf")),
        replace(DEFAULT_PARAMS, value_fit_width=float("-inf")),
        replace(DEFAULT_PARAMS, value_fit_vp=float("nan")),
        replace(DEFAULT_PARAMS, value_fit_vp=float("inf")),
        replace(DEFAULT_PARAMS, value_fit_vp=float("-inf")),
        replace(DEFAULT_PARAMS, price_min="bad"),
        replace(DEFAULT_PARAMS, price_max="bad"),
        replace(DEFAULT_PARAMS, price_min=float("nan")),
        replace(DEFAULT_PARAMS, price_min=float("inf")),
        replace(DEFAULT_PARAMS, price_max=float("-inf")),
        replace(DEFAULT_PARAMS, price_min=2.0, price_max=1.0),
        replace(DEFAULT_PARAMS, price=1.0),
        replace(DEFAULT_PARAMS, price=2.0),
    ),
)
def test_invalid_pricing_configuration_raises_value_error(params):
    with pytest.raises(ValueError):
        compute_round(DECISIONS, params)


@pytest.mark.parametrize("ideal_price", ("bad", float("nan"), float("inf"), float("-inf")))
def test_invalid_segment_ideal_price_raises_value_error(ideal_price):
    segments = (
        replace(DEFAULT_PARAMS.segments[0], ideal_price=ideal_price),
        *DEFAULT_PARAMS.segments[1:],
    )
    params = replace(DEFAULT_PARAMS, segments=segments)

    with pytest.raises(ValueError):
        compute_round(DECISIONS, params)


def test_segment_ideal_prices_and_pricing_config_are_locked():
    assert [segment.ideal_price for segment in DEFAULT_PARAMS.segments] == pytest.approx(
        (1.30, 1.60, 1.75, 1.45, 1.20)
    )
    assert DEFAULT_PARAMS.value_fit_width == pytest.approx(0.60)
    assert DEFAULT_PARAMS.value_fit_vp == pytest.approx(10.0)
    assert DEFAULT_PARAMS.price_min == pytest.approx(1.10)
    assert DEFAULT_PARAMS.price_max == pytest.approx(1.90)


def test_segment_constructor_remains_backward_compatible():
    segment = Segment("TEST", 1.0, 50.0, 2.0, (1.0,))

    assert segment.ideal_price == pytest.approx(DEFAULT_PARAMS.price)


def test_segment_reconstruction_preserves_locked_ideal_prices():
    segment_sizes = {segment.name: segment.size * 1.1 for segment in DEFAULT_PARAMS.segments}
    reconstructed = _params_with_segment_sizes(DEFAULT_PARAMS, segment_sizes)

    assert [segment.ideal_price for segment in reconstructed.segments] == pytest.approx(
        [segment.ideal_price for segment in DEFAULT_PARAMS.segments]
    )
    assert [segment.ideal_price for segment in reconstructed.segments] == pytest.approx(
        (1.30, 1.60, 1.75, 1.45, 1.20)
    )


def test_priced_fifth_team_can_be_added_without_reconciliation_failures():
    decisions = deepcopy(PRICED_DECISIONS)
    decisions["E"] = {
        "brand": 2,
        "perf": 5,
        "research": 3,
        "positioning": 35,
        "emphasis": (0.30, 0.20, 0.10, 0.10, 0.30),
        "price": 1.35,
    }

    results = compute_round(decisions, DEFAULT_PARAMS)

    assert "E" in results
    for backdrop in DEFAULT_PARAMS.backdrop_firms:
        assert backdrop in results
    for segment in DEFAULT_PARAMS.segments:
        assert sum(firm_result["share"][segment.name] for firm_result in results.values()) == pytest.approx(1.0)
        assert sum(firm_result["volume"][segment.name] for firm_result in results.values()) == pytest.approx(
            segment.size
        )


def test_price_and_attribute_override_interact_for_same_team():
    decisions = deepcopy(DECISIONS)
    decisions["A"]["price"] = 1.20
    baseline = compute_round(DECISIONS, DEFAULT_PARAMS)
    priced = compute_round(decisions, DEFAULT_PARAMS)
    overridden = compute_round(
        decisions,
        DEFAULT_PARAMS,
        team_attribute_overrides={"A": {"EQ": 80.0}},
    )

    assert overridden["A"]["price"] == pytest.approx(1.20)
    assert overridden["A"]["eq"] == pytest.approx(80.0)
    assert overridden["A"]["attributes"]["EQ"] == pytest.approx(80.0)
    assert overridden["A"]["perception"]["MAIN"] > baseline["A"]["perception"]["MAIN"]
    assert overridden["A"]["perception"]["FUN"] < baseline["A"]["perception"]["FUN"]
    assert priced["A"]["perception"]["MAIN"] > baseline["A"]["perception"]["MAIN"]
    for attribute, value in baseline["A"]["attributes"].items():
        if attribute != "EQ":
            assert overridden["A"]["attributes"][attribute] == pytest.approx(value)
