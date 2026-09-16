from copy import deepcopy
from math import exp

import pytest

from engine import DEFAULT_PARAMS, compute_round


DECISIONS = {
    "A": {
        "brand": 3,
        "perf": 6,
        "research": 1,
        "positioning": 20,
        "emphasis": (0.45, 0.15, 0.05, 0.05, 0.30),
    },
    "B": {
        "brand": 6,
        "perf": 2,
        "research": 2,
        "positioning": 82,
        "emphasis": (0.10, 0.20, 0.40, 0.25, 0.05),
    },
    "C": {
        "brand": 4,
        "perf": 4,
        "research": 2,
        "positioning": 52,
        "emphasis": (0.25, 0.20, 0.25, 0.15, 0.15),
    },
    "D": {
        "brand": 5,
        "perf": 3,
        "research": 2,
        "positioning": 72,
        "emphasis": (0.15, 0.10, 0.30, 0.40, 0.05),
    },
}

SEGMENTS = ("LEG", "FIT", "FUN", "GENZ", "MAIN")
FIRMS = ("A", "B", "C", "D", "BigRed", "Monssta", "Pulse")


def test_step1_golden_values():
    results = compute_round(DECISIONS, DEFAULT_PARAMS)

    expected_eq = {
        "A": 69.9,
        "B": 73.7,
        "C": 71.5,
        "D": 72.7,
    }
    expected_perception = {
        "A": (79.8, 52.3, 37.2, 47.6, 68.2),
        "B": (50.7, 56.7, 67.8, 70.0, 60.1),
        "C": (64.4, 66.0, 53.2, 62.0, 73.3),
        "D": (55.2, 59.1, 62.2, 73.8, 64.0),
        "BigRed": (75.5, 80.2, 67.5, 76.5, 84.5),
        "Monssta": (77.2, 72.5, 59.4, 69.7, 83.0),
        "Pulse": (43.8, 73.3, 88.7, 80.3, 54.0),
    }
    expected_share_pct = {
        "A": (21.4, 9.2, 5.2, 5.8, 13.3),
        "B": (8.9, 10.5, 15.2, 14.1, 10.9),
        "C": (13.5, 13.9, 9.1, 10.2, 15.1),
        "D": (10.2, 11.2, 12.5, 16.4, 12.0),
        "BigRed": (18.8, 21.2, 15.0, 18.3, 20.0),
        "Monssta": (19.8, 16.8, 11.3, 13.9, 19.3),
        "Pulse": (7.3, 17.2, 31.6, 21.3, 9.3),
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

    for firm, expected in expected_eq.items():
        assert results[firm]["eq"] == pytest.approx(expected, abs=0.1)

    for firm in FIRMS:
        for segment, expected in zip(SEGMENTS, expected_perception[firm], strict=True):
            assert results[firm]["perception"][segment] == pytest.approx(expected, abs=0.1)
        for segment, expected in zip(SEGMENTS, expected_share_pct[firm], strict=True):
            assert results[firm]["share"][segment] * 100.0 == pytest.approx(expected, abs=0.2)
            segment_size = next(s.size for s in DEFAULT_PARAMS.segments if s.name == segment)
            assert results[firm]["volume"][segment] == pytest.approx(
                results[firm]["share"][segment] * segment_size,
                abs=0.05,
            )

        total_volume, total_share_pct, growth_share_pct = expected_totals[firm]
        assert results[firm]["total_volume"] == pytest.approx(total_volume, abs=0.1)
        assert results[firm]["total_market_share"] * 100.0 == pytest.approx(total_share_pct, abs=0.1)
        assert results[firm]["growth_segment_share"] * 100.0 == pytest.approx(growth_share_pct, abs=0.1)

    assert sum(results[firm]["total_volume"] for firm in FIRMS) == pytest.approx(100.0, abs=0.1)


def test_segment_shares_and_volumes_sum_to_market_sizes():
    results = compute_round(DECISIONS, DEFAULT_PARAMS)

    for segment in DEFAULT_PARAMS.segments:
        assert sum(results[firm]["share"][segment.name] for firm in FIRMS) == pytest.approx(1.0)
        assert sum(results[firm]["volume"][segment.name] for firm in FIRMS) == pytest.approx(segment.size)


def test_financial_identities():
    results = compute_round(DECISIONS, DEFAULT_PARAMS)

    for firm in FIRMS:
        total_volume = results[firm]["total_volume"]
        assert results[firm]["revenue"] == pytest.approx(total_volume * DEFAULT_PARAMS.price)
        assert results[firm]["contribution"] == pytest.approx(
            total_volume * (DEFAULT_PARAMS.price - DEFAULT_PARAMS.variable_cost) - DEFAULT_PARAMS.budget
        )


def test_compute_round_is_deterministic_and_does_not_mutate_decisions():
    decisions = deepcopy(DECISIONS)
    original_decisions = deepcopy(decisions)

    first = compute_round(decisions, DEFAULT_PARAMS)
    second = compute_round(decisions, DEFAULT_PARAMS)

    assert first == second
    assert decisions == original_decisions


def test_fifth_team_can_be_added_without_hardcoded_firm_failures():
    decisions = deepcopy(DECISIONS)
    decisions["E"] = {
        "brand": 2,
        "perf": 5,
        "research": 3,
        "positioning": 35,
        "emphasis": (0.30, 0.20, 0.10, 0.10, 0.30),
    }

    results = compute_round(decisions, DEFAULT_PARAMS)

    assert "E" in results
    for backdrop in DEFAULT_PARAMS.backdrop_firms:
        assert backdrop in results
    for segment in DEFAULT_PARAMS.segments:
        assert sum(firm_result["share"][segment.name] for firm_result in results.values()) == pytest.approx(1.0)


def test_result_schema_contains_required_fields_for_every_firm():
    results = compute_round(DECISIONS, DEFAULT_PARAMS)
    required_fields = {
        "firm_type",
        "attributes",
        "eq",
        "attraction",
        "perception",
        "share",
        "volume",
        "total_volume",
        "total_market_share",
        "growth_segment_share",
        "revenue",
        "contribution",
    }

    for result in results.values():
        assert required_fields <= result.keys()
        assert set(result["attributes"].keys()) == set(DEFAULT_PARAMS.attributes)
        assert isinstance(result["eq"], float)
        for segment_mapping_name in ("attraction", "perception", "share", "volume"):
            assert set(result[segment_mapping_name].keys()) == set(SEGMENTS)
        for numeric_field in (
            "total_volume",
            "total_market_share",
            "growth_segment_share",
            "revenue",
            "contribution",
        ):
            assert isinstance(result[numeric_field], float)


def test_firm_types_and_backdrop_eq_are_reported():
    results = compute_round(DECISIONS, DEFAULT_PARAMS)

    assert results["A"]["firm_type"] == "team"
    assert results["BigRed"]["firm_type"] == "backdrop"
    assert results["BigRed"]["eq"] == pytest.approx(85.0)


def test_attraction_follows_specified_formula():
    results = compute_round(DECISIONS, DEFAULT_PARAMS)
    segment = DEFAULT_PARAMS.segments[0]

    assert results["A"]["attraction"]["LEG"] == pytest.approx(
        exp(segment.beta * results["A"]["perception"]["LEG"] / 100.0)
    )


@pytest.mark.parametrize(
    "team_name, patch",
    (
        ("A", {"brand": 2}),
        ("A", {"positioning": -1}),
        ("A", {"positioning": 101}),
        ("A", {"emphasis": (0.25, 0.25, 0.25, 0.25)}),
        ("A", {"emphasis": (0.20, 0.20, 0.20, 0.20, 0.10)}),
        ("A", {"brand": -1}),
        ("A", {"brand": float("inf")}),
        ("BigRed", {"brand": 3, "perf": 6, "research": 1, "positioning": 20, "emphasis": (0.45, 0.15, 0.05, 0.05, 0.30)}),
        ("A", {"missing": "research"}),
    ),
)
def test_invalid_inputs_raise_value_error_and_do_not_mutate_decisions(team_name, patch):
    decisions = deepcopy(DECISIONS)

    if team_name == "BigRed":
        decisions = {"BigRed": patch}
    elif "missing" in patch:
        decisions[team_name].pop(patch["missing"])
    else:
        decisions[team_name].update(patch)

    original_decisions = deepcopy(decisions)

    with pytest.raises(ValueError):
        compute_round(decisions, DEFAULT_PARAMS)

    assert decisions == original_decisions
