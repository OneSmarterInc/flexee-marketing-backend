from copy import deepcopy
from dataclasses import replace

import pytest

from engine import DEFAULT_PARAMS, Segment
from simulation import DEFAULT_GROWTH_RATES, run_simulation
from test_step1 import DECISIONS


TEAMS = ("A", "B", "C", "D")
BACKDROP = ("BigRed", "Monssta", "Pulse")
FIRMS = (*TEAMS, *BACKDROP)
SEGMENTS = ("LEG", "FIT", "FUN", "GENZ", "MAIN")


def six_round_decisions():
    return [deepcopy(DECISIONS) for _ in range(6)]


def test_default_growth_rates_are_exact_specified_literals_and_read_only():
    assert dict(DEFAULT_GROWTH_RATES) == {
        "LEG": -0.01,
        "FIT": 0.003,
        "FUN": 0.025,
        "GENZ": 0.04,
        "MAIN": 0.005,
    }

    with pytest.raises(TypeError):
        DEFAULT_GROWTH_RATES["LEG"] = 0.0  # type: ignore[index]


def test_six_round_golden_segment_sizes():
    history = run_simulation(six_round_decisions(), DEFAULT_PARAMS, DEFAULT_GROWTH_RATES)
    expected_sizes = [
        {"LEG": 30.000000, "FIT": 18.000000, "FUN": 25.000000, "GENZ": 12.000000, "MAIN": 15.000000},
        {"LEG": 29.700000, "FIT": 18.054000, "FUN": 25.625000, "GENZ": 12.480000, "MAIN": 15.075000},
        {"LEG": 29.403000, "FIT": 18.108162, "FUN": 26.265625, "GENZ": 12.979200, "MAIN": 15.150375},
        {"LEG": 29.108970, "FIT": 18.162486, "FUN": 26.922266, "GENZ": 13.498368, "MAIN": 15.226127},
        {"LEG": 28.817880, "FIT": 18.216974, "FUN": 27.595322, "GENZ": 14.038303, "MAIN": 15.302258},
        {"LEG": 28.529702, "FIT": 18.271625, "FUN": 28.285206, "GENZ": 14.599835, "MAIN": 15.378769},
    ]

    for round_record, expected_round_sizes in zip(history, expected_sizes, strict=True):
        for segment in SEGMENTS:
            assert round_record["segment_sizes"][segment] == pytest.approx(expected_round_sizes[segment], abs=1e-6)

    assert sum(history[0]["segment_sizes"].values()) == pytest.approx(100.000000, abs=1e-6)
    assert sum(history[5]["segment_sizes"].values()) == pytest.approx(
        105.06513531228184,
        abs=1e-12,
    )
    # 105.065137 is the sum of independently rounded six-decimal display values;
    # run_simulation keeps the full-precision segment sizes.
    assert sum(
        expected_sizes[5][segment]
        for segment in SEGMENTS
    ) == pytest.approx(105.065137, abs=1e-6)


def test_round_one_regression_values():
    round_one = run_simulation(six_round_decisions(), DEFAULT_PARAMS, DEFAULT_GROWTH_RATES)[0]
    expected_share_pct = {
        "A": 12.0755,
        "B": 11.6891,
        "C": 12.3258,
        "D": 11.9829,
    }
    expected_overall = {
        "A": 30.34,
        "B": 50.00,
        "C": 71.32,
        "D": 63.83,
    }

    for team in TEAMS:
        assert round_one["firms"][team]["total_market_share"] * 100.0 == pytest.approx(
            expected_share_pct[team], abs=0.01
        )
        assert round_one["scorecard"]["firms"][team]["overall"] == pytest.approx(
            expected_overall[team], abs=0.1
        )


def test_round_six_golden_values():
    round_six = run_simulation(six_round_decisions(), DEFAULT_PARAMS, DEFAULT_GROWTH_RATES)[5]
    expected_share_pct = {
        "A": 11.5705,
        "B": 11.8908,
        "C": 12.1718,
        "D": 12.1304,
    }
    expected_overall = {
        "A": 0.00,
        "B": 76.64,
        "C": 71.37,
        "D": 87.53,
    }

    for team in TEAMS:
        assert round_six["firms"][team]["total_market_share"] * 100.0 == pytest.approx(
            expected_share_pct[team], abs=0.01
        )
        assert round_six["scorecard"]["firms"][team]["overall"] == pytest.approx(
            expected_overall[team], abs=0.1
        )


def test_fixed_share_invariant_for_rounds_two_through_six():
    history = run_simulation(six_round_decisions(), DEFAULT_PARAMS, DEFAULT_GROWTH_RATES)

    for later_round in history[1:]:
        for firm in FIRMS:
            for segment in SEGMENTS:
                assert later_round["firms"][firm]["share"][segment] == pytest.approx(
                    history[0]["firms"][firm]["share"][segment], abs=1e-9
                )


def test_market_reweighting_changes_total_market_share_monotonically():
    history = run_simulation(six_round_decisions(), DEFAULT_PARAMS, DEFAULT_GROWTH_RATES)
    a_shares = [round_record["firms"]["A"]["total_market_share"] for round_record in history]
    d_shares = [round_record["firms"]["D"]["total_market_share"] for round_record in history]

    assert all(earlier > later for earlier, later in zip(a_shares[:-1], a_shares[1:], strict=True))
    assert all(earlier < later for earlier, later in zip(d_shares[:-1], d_shares[1:], strict=True))


def test_reconciliation_for_segments_and_market_totals():
    history = run_simulation(six_round_decisions(), DEFAULT_PARAMS, DEFAULT_GROWTH_RATES)

    for round_record in history:
        for segment in SEGMENTS:
            assert sum(round_record["firms"][firm]["share"][segment] for firm in FIRMS) == pytest.approx(
                1.0, abs=1e-12
            )
            assert sum(round_record["firms"][firm]["volume"][segment] for firm in FIRMS) == pytest.approx(
                round_record["segment_sizes"][segment], abs=1e-9
            )

        market_size = sum(round_record["segment_sizes"].values())
        assert sum(round_record["firms"][firm]["total_volume"] for firm in FIRMS) == pytest.approx(
            market_size, abs=1e-9
        )


def test_record_structure():
    history = run_simulation(six_round_decisions(), DEFAULT_PARAMS, DEFAULT_GROWTH_RATES)

    assert isinstance(history, list)
    assert len(history) == 6
    assert [record["round"] for record in history] == [1, 2, 3, 4, 5, 6]
    for record in history:
        assert set(record) == {"round", "segment_sizes", "firms", "scorecard"}


def test_scorecard_contains_teams_only_each_round():
    history = run_simulation(six_round_decisions(), DEFAULT_PARAMS, DEFAULT_GROWTH_RATES)

    for record in history:
        assert set(record["scorecard"]["firms"]) == set(TEAMS)
        for backdrop in BACKDROP:
            assert backdrop not in record["scorecard"]["firms"]


def test_purity_and_determinism():
    decisions_by_round = six_round_decisions()
    growth_rates = dict(DEFAULT_GROWTH_RATES)
    original_decisions_by_round = deepcopy(decisions_by_round)
    original_growth_rates = deepcopy(growth_rates)
    original_segment_sizes = tuple(segment.size for segment in DEFAULT_PARAMS.segments)

    first = run_simulation(decisions_by_round, DEFAULT_PARAMS, growth_rates)
    second = run_simulation(decisions_by_round, DEFAULT_PARAMS, growth_rates)

    assert first == second
    assert decisions_by_round == original_decisions_by_round
    assert growth_rates == original_growth_rates
    assert tuple(segment.size for segment in DEFAULT_PARAMS.segments) == original_segment_sizes
    assert DEFAULT_PARAMS.segments[0].size == pytest.approx(30.0)


def test_open_ended_round_count():
    history = run_simulation([deepcopy(DECISIONS), deepcopy(DECISIONS)], DEFAULT_PARAMS, DEFAULT_GROWTH_RATES)

    assert len(history) == 2
    assert [record["round"] for record in history] == [1, 2]


def test_different_decisions_by_round_change_perception_or_share():
    second_round_decisions = deepcopy(DECISIONS)
    second_round_decisions["A"] = {
        "brand": 5,
        "perf": 4,
        "research": 1,
        "positioning": 30,
        "emphasis": (0.35, 0.20, 0.10, 0.05, 0.30),
    }

    history = run_simulation([deepcopy(DECISIONS), second_round_decisions], DEFAULT_PARAMS, DEFAULT_GROWTH_RATES)

    assert (
        history[0]["firms"]["A"]["perception"] != history[1]["firms"]["A"]["perception"]
        or history[0]["firms"]["A"]["share"] != history[1]["firms"]["A"]["share"]
    )


@pytest.mark.parametrize(
    "decisions_by_round",
    (
        {"round": DECISIONS},
        "not a sequence",
        [],
        [object()],
    ),
)
def test_invalid_decisions_by_round_raise_value_error(decisions_by_round):
    with pytest.raises(ValueError):
        run_simulation(decisions_by_round, DEFAULT_PARAMS, DEFAULT_GROWTH_RATES)


@pytest.mark.parametrize(
    "growth_rates",
    (
        {"FIT": 0.003, "FUN": 0.025, "GENZ": 0.04, "MAIN": 0.005},
        {"LEG": -0.01, "FIT": 0.003, "FUN": 0.025, "GENZ": 0.04, "MAIN": 0.005, "EXTRA": 0.0},
        {"LEG": "bad", "FIT": 0.003, "FUN": 0.025, "GENZ": 0.04, "MAIN": 0.005},
        {"LEG": float("nan"), "FIT": 0.003, "FUN": 0.025, "GENZ": 0.04, "MAIN": 0.005},
        {"LEG": float("inf"), "FIT": 0.003, "FUN": 0.025, "GENZ": 0.04, "MAIN": 0.005},
        {"LEG": float("-inf"), "FIT": 0.003, "FUN": 0.025, "GENZ": 0.04, "MAIN": 0.005},
        {"LEG": -1.0, "FIT": 0.003, "FUN": 0.025, "GENZ": 0.04, "MAIN": 0.005},
        {"LEG": -1.1, "FIT": 0.003, "FUN": 0.025, "GENZ": 0.04, "MAIN": 0.005},
        [("LEG", -0.01), ("FIT", 0.003), ("FUN", 0.025), ("GENZ", 0.04), ("MAIN", 0.005)],
    ),
)
def test_invalid_growth_rates_raise_value_error(growth_rates):
    with pytest.raises(ValueError):
        run_simulation([deepcopy(DECISIONS)], DEFAULT_PARAMS, growth_rates)


def test_duplicate_segment_names_raise_value_error():
    params = replace(
        DEFAULT_PARAMS,
        segments=(
            DEFAULT_PARAMS.segments[0],
            replace(DEFAULT_PARAMS.segments[1], name=DEFAULT_PARAMS.segments[0].name),
            *DEFAULT_PARAMS.segments[2:],
        ),
    )

    with pytest.raises(ValueError):
        run_simulation([deepcopy(DECISIONS)], params, DEFAULT_GROWTH_RATES)


@pytest.mark.parametrize("size", (-1.0, float("nan"), float("inf"), "bad"))
def test_malformed_segment_sizes_raise_value_error(size):
    params = replace(
        DEFAULT_PARAMS,
        segments=(
            replace(DEFAULT_PARAMS.segments[0], size=size),  # type: ignore[arg-type]
            *DEFAULT_PARAMS.segments[1:],
        ),
    )

    with pytest.raises(ValueError):
        run_simulation([deepcopy(DECISIONS)], params, DEFAULT_GROWTH_RATES)
