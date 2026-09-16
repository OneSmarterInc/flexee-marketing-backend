from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from math import inf, nan

import pytest

from crisis import (
    DEFAULT_CRISIS_PARAMS,
    CrisisParams,
    crisis_damage,
    run_simulation_with_crisis,
)
from engine import DEFAULT_PARAMS, Segment
from equity import DEFAULT_EQUITY_PARAMS, EquityParams, next_equity
from game import run_game
from promotion import DEFAULT_PROMOTION_PARAMS, PromotionParams, run_simulation_with_promotion
from simulation import DEFAULT_GROWTH_RATES
from test_step1 import DECISIONS
from test_step3c import BASELINE_PROD, BASELINE_TRUST, CRISIS_INPUTS
from test_step3d_ii import BASE_DECISIONS


TEAMS = ("A", "B", "C", "D")
BACKDROP = ("BigRed", "Monssta", "Pulse")
FIRMS = (*TEAMS, *BACKDROP)
SEGMENTS = ("LEG", "FIT", "FUN", "GENZ", "MAIN")
GAME_KEYS = {
    "round",
    "segment_sizes",
    "firms",
    "scorecard",
    "equity",
    "attribute_state",
    "deal_conditioning",
    "promotion_depth",
    "damage",
}

PROMOTION_DEPTHS_6 = {
    "A": (0.8, 0.8, 0.8, 0.8, 0.8, 0.8),
    "B": (0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
    "C": (0.4, 0.4, 0.4, 0.4, 0.4, 0.4),
    "D": (0.2, 0.4, 0.6, 0.8, 0.8, 0.8),
}
PRICES = {"A": 1.20, "B": 1.75, "C": 1.50, "D": 1.40}

GOLDEN_EQ = {
    "A": (61.8211, 61.6869, 61.5862, 60.4196, 60.6357, 60.7979),
    "B": (68.2150, 72.8762, 76.3721, 78.9814, 80.9510, 82.4282),
    "C": (65.4570, 68.0497, 69.9942, 71.1957, 72.3537, 73.2223),
    "D": (66.9080, 69.7889, 71.1497, 71.0195, 71.2726, 71.4624),
}
GOLDEN_ENTERING_DC = {
    "A": (0.0000, 0.4000, 0.6800, 0.8760, 1.0132, 1.1092),
    "B": (0.0000, 0.0000, 0.0000, 0.0000, 0.0000, 0.0000),
    "C": (0.0000, 0.2000, 0.3400, 0.4380, 0.5066, 0.5546),
    "D": (0.0000, 0.1000, 0.2700, 0.4890, 0.7423, 0.9196),
}
GOLDEN_DAMAGE_R4 = {"A": 5.4559, "B": 0.0630, "C": 1.2846, "D": 1.7536}
GOLDEN_TRUST = {
    "A": (32.0000, 32.0000, 32.0000, 29.2721, 29.2721, 29.2721),
    "B": (32.0000, 32.0000, 32.0000, 31.9685, 31.9685, 31.9685),
    "C": (32.0000, 32.0000, 32.0000, 31.3577, 31.3577, 31.3577),
    "D": (32.0000, 32.0000, 32.0000, 31.1232, 31.1232, 31.1232),
}
GOLDEN_PROD = {
    "A": (30.0000, 30.0000, 30.0000, 28.3632, 28.3632, 28.3632),
    "B": (30.0000, 30.0000, 30.0000, 29.9811, 29.9811, 29.9811),
    "C": (30.0000, 30.0000, 30.0000, 29.6146, 29.6146, 29.6146),
    "D": (30.0000, 30.0000, 30.0000, 29.4739, 29.4739, 29.4739),
}
GOLDEN_SHARE = {
    3: {"A": 11.7386, "B": 11.8232, "C": 12.3080, "D": 12.4725},
    4: {"A": 11.2354, "B": 12.0503, "C": 12.2381, "D": 12.5338},
    6: {"A": 10.8688, "B": 12.3831, "C": 12.2281, "D": 12.1810},
}
GOLDEN_CONTRIBUTION = {
    4: {"A": -2.3682, "B": 1.1617, "C": -0.1757, "D": -1.4863},
    6: {"A": -2.4633, "B": 1.7093, "C": 0.0210, "D": -1.5533},
}


def _promotion_rounds(n_rounds: int = 6, priced: bool = False) -> list[dict[str, dict[str, object]]]:
    rounds = []
    for round_index in range(n_rounds):
        row = {}
        for team in TEAMS:
            decision = deepcopy(BASE_DECISIONS[team])
            decision["promotion"] = PROMOTION_DEPTHS_6[team][round_index]
            if priced:
                decision["price"] = PRICES[team]
            row[team] = decision
        rounds.append(row)
    return rounds


def _no_promotion_rounds(n_rounds: int = 6) -> list[dict[str, dict[str, object]]]:
    return [deepcopy(DECISIONS) for _ in range(n_rounds)]


def _priced_no_promotion_rounds(n_rounds: int = 6) -> list[dict[str, dict[str, object]]]:
    rounds = _no_promotion_rounds(n_rounds)
    for row in rounds:
        for team, price in PRICES.items():
            row[team]["price"] = price
    return rounds


def test_game_public_round_record_schema():
    history = run_game(_promotion_rounds(), crisis_inputs=deepcopy(CRISIS_INPUTS))

    for index, record in enumerate(history, start=1):
        assert set(record) == GAME_KEYS
        if index == DEFAULT_CRISIS_PARAMS.crisis_round:
            assert set(record["damage"]) == set(TEAMS)
        else:
            assert record["damage"] == {}


def test_game_matches_promotion_runner_when_crisis_is_outside_run_exactly():
    decisions = _promotion_rounds()
    crisis_params = replace(DEFAULT_CRISIS_PARAMS, crisis_round=99)

    game_history = run_game(deepcopy(decisions), crisis_params=crisis_params)
    promotion_history = run_simulation_with_promotion(deepcopy(decisions))

    promotion_fields = (
        "round",
        "segment_sizes",
        "firms",
        "scorecard",
        "equity",
        "deal_conditioning",
        "promotion_depth",
    )
    for game_record, promotion_record in zip(game_history, promotion_history, strict=True):
        for field in promotion_fields:
            assert game_record[field] == promotion_record[field]
        assert game_record["damage"] == {}
        for team in TEAMS:
            assert game_record["attribute_state"][team]["EQ"] == promotion_record["equity"][team]
            assert game_record["attribute_state"][team]["TRUST"] == BASELINE_TRUST
            assert game_record["attribute_state"][team]["PROD"] == BASELINE_PROD


def test_game_matches_crisis_runner_when_promotion_is_zero_exactly():
    decisions = _no_promotion_rounds()

    game_history = run_game(deepcopy(decisions), crisis_inputs=deepcopy(CRISIS_INPUTS))
    crisis_history = run_simulation_with_crisis(deepcopy(decisions), crisis_inputs=deepcopy(CRISIS_INPUTS))

    crisis_fields = (
        "round",
        "segment_sizes",
        "firms",
        "scorecard",
        "equity",
        "attribute_state",
        "damage",
    )
    for game_record, crisis_record in zip(game_history, crisis_history, strict=True):
        for field in crisis_fields:
            assert game_record[field] == crisis_record[field]
        for team in TEAMS:
            assert game_record["promotion_depth"][team] == 0.0
            assert game_record["deal_conditioning"][team] == 0.0


def test_combined_promotion_and_crisis_golden_values_are_explicit():
    history = run_game(_promotion_rounds(), crisis_inputs=deepcopy(CRISIS_INPUTS))

    for round_index, record in enumerate(history):
        for team in TEAMS:
            assert record["equity"][team] == pytest.approx(GOLDEN_EQ[team][round_index], abs=0.01)
            assert record["attribute_state"][team]["EQ"] == pytest.approx(GOLDEN_EQ[team][round_index], abs=0.01)
            assert record["deal_conditioning"][team] == pytest.approx(
                GOLDEN_ENTERING_DC[team][round_index], abs=0.001
            )
            assert record["attribute_state"][team]["TRUST"] == pytest.approx(
                GOLDEN_TRUST[team][round_index], abs=0.01
            )
            assert record["attribute_state"][team]["PROD"] == pytest.approx(
                GOLDEN_PROD[team][round_index], abs=0.01
            )

    assert history[3]["damage"] == pytest.approx(GOLDEN_DAMAGE_R4, abs=0.001)
    for round_number, expected_share in GOLDEN_SHARE.items():
        for team, expected in expected_share.items():
            assert history[round_number - 1]["firms"][team]["total_market_share"] * 100.0 == pytest.approx(
                expected, abs=0.01
            )
    for round_number, expected_contribution in GOLDEN_CONTRIBUTION.items():
        for team, expected in expected_contribution.items():
            assert history[round_number - 1]["firms"][team]["contribution"] == pytest.approx(expected, abs=0.01)


def test_state_identity_for_every_team_and_round():
    history = run_game(_promotion_rounds(), crisis_inputs=deepcopy(CRISIS_INPUTS))

    for record in history:
        for team in TEAMS:
            assert record["equity"][team] == record["attribute_state"][team]["EQ"]
            assert record["firms"][team]["eq"] == record["attribute_state"][team]["EQ"]
            assert record["firms"][team]["attributes"]["EQ"] == record["attribute_state"][team]["EQ"]
            assert record["firms"][team]["attributes"]["TRUST"] == record["attribute_state"][team]["TRUST"]
            assert record["firms"][team]["attributes"]["PROD"] == record["attribute_state"][team]["PROD"]
            assert record["firms"][team]["deal_conditioning"] == record["deal_conditioning"][team]
            assert record["firms"][team]["promotion"] == record["promotion_depth"][team]


def test_single_stock_crisis_recurrence_is_post_promotion_and_then_carried_forward():
    history = run_game(_promotion_rounds(), crisis_inputs=deepcopy(CRISIS_INPUTS))
    expected_eq_by_team = {team: DEFAULT_EQUITY_PARAMS.eq_start for team in TEAMS}

    for round_index in range(3):
        for team in TEAMS:
            decision = _promotion_rounds()[round_index][team]
            expected_eq_by_team[team] = next_equity(
                expected_eq_by_team[team],
                decision["brand"],
                decision["perf"],
                DEFAULT_EQUITY_PARAMS,
                DEFAULT_PARAMS.budget,
                promo_depth=decision["promotion"],
            )
            assert history[round_index]["equity"][team] == pytest.approx(expected_eq_by_team[team], abs=1e-12)

    pre_crisis_eq = {}
    for team in TEAMS:
        decision = _promotion_rounds()[3][team]
        pre_crisis_eq[team] = next_equity(
            expected_eq_by_team[team],
            decision["brand"],
            decision["perf"],
            DEFAULT_EQUITY_PARAMS,
            DEFAULT_PARAMS.budget,
            promo_depth=decision["promotion"],
        )
        damage = crisis_damage(
            pre_crisis_eq[team],
            CRISIS_INPUTS[team]["exposure"],
            CRISIS_INPUTS[team]["preempt"],
            CRISIS_INPUTS[team]["response"],
            DEFAULT_CRISIS_PARAMS,
        )
        expected_eq_by_team[team] = pre_crisis_eq[team] - DEFAULT_CRISIS_PARAMS.eq_fraction * damage
        assert history[3]["damage"][team] == pytest.approx(damage, abs=1e-12)
        assert history[3]["equity"][team] == pytest.approx(expected_eq_by_team[team], abs=1e-12)

    for round_index in (4, 5):
        for team in TEAMS:
            decision = _promotion_rounds()[round_index][team]
            expected_eq_by_team[team] = next_equity(
                expected_eq_by_team[team],
                decision["brand"],
                decision["perf"],
                DEFAULT_EQUITY_PARAMS,
                DEFAULT_PARAMS.budget,
                promo_depth=decision["promotion"],
            )
            assert history[round_index]["equity"][team] == pytest.approx(expected_eq_by_team[team], abs=1e-12)

    assert history[3]["damage"]["A"] > 4.2155


def test_pricing_pass_through_changes_market_but_not_carried_state():
    reference_history = run_game(_promotion_rounds(), crisis_inputs=deepcopy(CRISIS_INPUTS))
    priced_history = run_game(_promotion_rounds(priced=True), crisis_inputs=deepcopy(CRISIS_INPUTS))

    changed_perception_or_share = False
    for reference_record, priced_record in zip(reference_history, priced_history, strict=True):
        assert priced_record["equity"] == reference_record["equity"]
        assert priced_record["attribute_state"] == reference_record["attribute_state"]
        assert priced_record["deal_conditioning"] == reference_record["deal_conditioning"]
        for team in TEAMS:
            firm = priced_record["firms"][team]
            total_volume = firm["total_volume"]
            price = PRICES[team]
            promotion_depth = priced_record["promotion_depth"][team]
            assert firm["revenue"] == pytest.approx(total_volume * price)
            assert firm["contribution"] == pytest.approx(
                total_volume * (price - DEFAULT_PARAMS.variable_cost - DEFAULT_PARAMS.promo_cost_rate * promotion_depth)
                - DEFAULT_PARAMS.budget
            )
            if (
                firm["perception"] != reference_record["firms"][team]["perception"]
                or firm["share"] != reference_record["firms"][team]["share"]
            ):
                changed_perception_or_share = True
    assert changed_perception_or_share


def test_crisis_scar_does_not_recover_in_game_runner():
    history = run_game(_no_promotion_rounds(), crisis_inputs=deepcopy(CRISIS_INPUTS))

    crisis_state = history[DEFAULT_CRISIS_PARAMS.crisis_round - 1]["attribute_state"]
    for record in history[DEFAULT_CRISIS_PARAMS.crisis_round:]:
        for team in TEAMS:
            assert record["attribute_state"][team]["TRUST"] == crisis_state[team]["TRUST"]
            assert record["attribute_state"][team]["PROD"] == crisis_state[team]["PROD"]


def test_reconciliation_for_segments_market_totals_and_scorecard():
    history = run_game(_promotion_rounds(), crisis_inputs=deepcopy(CRISIS_INPUTS))

    for record in history:
        for segment in SEGMENTS:
            assert sum(record["firms"][firm]["share"][segment] for firm in FIRMS) == pytest.approx(1.0)
            assert sum(record["firms"][firm]["volume"][segment] for firm in FIRMS) == pytest.approx(
                record["segment_sizes"][segment]
            )
        assert sum(record["firms"][firm]["total_volume"] for firm in FIRMS) == pytest.approx(
            sum(record["segment_sizes"].values())
        )
        assert set(record["scorecard"]["firms"]) == set(TEAMS)


def test_omitted_crisis_inputs_record_zero_damage_on_in_horizon_crisis_round():
    history = run_game(_promotion_rounds())

    for index, record in enumerate(history, start=1):
        if index == DEFAULT_CRISIS_PARAMS.crisis_round:
            assert set(record["damage"]) == set(TEAMS)
            assert all(value == 0.0 for value in record["damage"].values())
        else:
            assert record["damage"] == {}


def test_omitted_crisis_inputs_keep_trust_prod_and_match_promotion_equity_exactly():
    game_history = run_game(_promotion_rounds())
    promotion_history = run_simulation_with_promotion(_promotion_rounds())

    promotion_fields = (
        "round",
        "segment_sizes",
        "firms",
        "scorecard",
        "equity",
        "deal_conditioning",
        "promotion_depth",
    )
    for game_record, promotion_record in zip(game_history, promotion_history, strict=True):
        for field in promotion_fields:
            assert game_record[field] == promotion_record[field]
        for team in TEAMS:
            assert game_record["attribute_state"][team]["EQ"] == promotion_record["equity"][team]
            assert game_record["attribute_state"][team]["TRUST"] == BASELINE_TRUST
            assert game_record["attribute_state"][team]["PROD"] == BASELINE_PROD


def test_omitted_crisis_inputs_beyond_horizon_leave_damage_empty():
    history = run_game(_promotion_rounds(), crisis_params=replace(DEFAULT_CRISIS_PARAMS, crisis_round=99))
    assert all(record["damage"] == {} for record in history)


def test_game_rejects_empty_nonsequence_and_nonmapping_rounds():
    with pytest.raises(ValueError, match="at least one round"):
        run_game([])
    with pytest.raises(ValueError, match="non-string sequence"):
        run_game("rounds")
    with pytest.raises(ValueError, match="round 1 decisions"):
        run_game([1])


def test_game_rejects_later_round_team_additions_and_removals():
    added = _no_promotion_rounds(2)
    added[1]["E"] = deepcopy(DECISIONS["A"])
    with pytest.raises(ValueError, match="same team names"):
        run_game(added, crisis_params=replace(DEFAULT_CRISIS_PARAMS, crisis_round=99))

    removed = _no_promotion_rounds(2)
    del removed[1]["D"]
    with pytest.raises(ValueError, match="same team names"):
        run_game(removed, crisis_params=replace(DEFAULT_CRISIS_PARAMS, crisis_round=99))


def test_game_rejects_malformed_decisions_and_invalid_brand_perf_promotion():
    decisions = _promotion_rounds()
    decisions[0]["A"] = 1
    with pytest.raises(ValueError):
        run_game(decisions, crisis_inputs=deepcopy(CRISIS_INPUTS))

    decisions = _promotion_rounds()
    decisions[0]["A"]["brand"] = -1.0
    with pytest.raises(ValueError, match="brand"):
        run_game(decisions, crisis_inputs=deepcopy(CRISIS_INPUTS))

    decisions = _promotion_rounds()
    decisions[0]["A"]["perf"] = -1.0
    with pytest.raises(ValueError, match="perf"):
        run_game(decisions, crisis_inputs=deepcopy(CRISIS_INPUTS))

    decisions = _promotion_rounds()
    decisions[0]["A"]["promotion"] = 2.0
    with pytest.raises(ValueError, match="promotion"):
        run_game(decisions, crisis_inputs=deepcopy(CRISIS_INPUTS))


def test_game_rejects_invalid_growth_equity_crisis_and_promotion_params():
    bad_growth = dict(DEFAULT_GROWTH_RATES)
    bad_growth.pop("LEG")
    with pytest.raises(ValueError, match="growth_rates"):
        run_game(_promotion_rounds(), growth_rates=bad_growth, crisis_inputs=deepcopy(CRISIS_INPUTS))

    with pytest.raises(ValueError, match="k_brand"):
        run_game(
            _promotion_rounds(),
            equity_params=replace(DEFAULT_EQUITY_PARAMS, k_brand=0.0),
            crisis_inputs=deepcopy(CRISIS_INPUTS),
        )

    with pytest.raises(ValueError, match="crisis_round"):
        run_game(
            _promotion_rounds(),
            crisis_params=replace(DEFAULT_CRISIS_PARAMS, crisis_round=0),
            crisis_inputs=deepcopy(CRISIS_INPUTS),
        )

    with pytest.raises(ValueError, match="dc_decay"):
        run_game(
            _promotion_rounds(),
            promotion_params=replace(DEFAULT_PROMOTION_PARAMS, dc_decay=2.0),
            crisis_inputs=deepcopy(CRISIS_INPUTS),
        )


@pytest.mark.parametrize(
    "bad_inputs",
    [
        [],
        {"A": {"exposure": 0.1, "preempt": 0.1, "response": 0.1}},
        {**CRISIS_INPUTS, "E": {"exposure": 0.1, "preempt": 0.1, "response": 0.1}},
        {team: 1.0 for team in TEAMS},
        {team: {"preempt": 0.1, "response": 0.1} for team in TEAMS},
        {team: {"exposure": 0.1, "response": 0.1} for team in TEAMS},
        {team: {"exposure": 0.1, "preempt": 0.1} for team in TEAMS},
        {team: {"exposure": 2.0, "preempt": 0.1, "response": 0.1} for team in TEAMS},
        {team: {"exposure": 0.1, "preempt": -0.1, "response": 0.1} for team in TEAMS},
        {team: {"exposure": 0.1, "preempt": 0.1, "response": 2.0} for team in TEAMS},
        {team: {"exposure": nan, "preempt": 0.1, "response": 0.1} for team in TEAMS},
        {team: {"exposure": 0.1, "preempt": inf, "response": 0.1} for team in TEAMS},
    ],
)
def test_game_rejects_invalid_crisis_inputs(bad_inputs):
    with pytest.raises(ValueError):
        run_game(_promotion_rounds(), crisis_inputs=bad_inputs)


def test_game_rejects_missing_carried_attributes_duplicate_attributes_and_bad_baseline_length():
    missing_eq = replace(
        DEFAULT_PARAMS,
        attributes=tuple(attribute for attribute in DEFAULT_PARAMS.attributes if attribute != "EQ"),
        team_baseline_attributes=DEFAULT_PARAMS.team_baseline_attributes[:-1],
    )
    with pytest.raises(ValueError, match="EQ"):
        run_game(_promotion_rounds(), params=missing_eq, crisis_inputs=deepcopy(CRISIS_INPUTS))

    missing_trust = replace(
        DEFAULT_PARAMS,
        attributes=tuple(attribute for attribute in DEFAULT_PARAMS.attributes if attribute != "TRUST"),
        team_baseline_attributes=DEFAULT_PARAMS.team_baseline_attributes[:-1],
    )
    with pytest.raises(ValueError, match="TRUST"):
        run_game(_promotion_rounds(), params=missing_trust, crisis_inputs=deepcopy(CRISIS_INPUTS))

    missing_prod = replace(
        DEFAULT_PARAMS,
        attributes=tuple(attribute for attribute in DEFAULT_PARAMS.attributes if attribute != "PROD"),
        team_baseline_attributes=DEFAULT_PARAMS.team_baseline_attributes[:-1],
    )
    with pytest.raises(ValueError, match="PROD"):
        run_game(_promotion_rounds(), params=missing_prod, crisis_inputs=deepcopy(CRISIS_INPUTS))

    duplicate_attributes = replace(
        DEFAULT_PARAMS,
        attributes=("EQ", "EQ", "PROD", "CRE", "DIST", "SOC", "TRUST"),
    )
    with pytest.raises(ValueError, match="unique"):
        run_game(_promotion_rounds(), params=duplicate_attributes, crisis_inputs=deepcopy(CRISIS_INPUTS))

    bad_length = replace(DEFAULT_PARAMS, team_baseline_attributes=DEFAULT_PARAMS.team_baseline_attributes[:-1])
    with pytest.raises(ValueError, match="length"):
        run_game(_promotion_rounds(), params=bad_length, crisis_inputs=deepcopy(CRISIS_INPUTS))


def test_open_ended_fifth_team_two_round_crisis_inside_and_beyond_horizon():
    five_team_rounds = []
    for _ in range(2):
        row = deepcopy(DECISIONS)
        row["E"] = deepcopy(DECISIONS["A"])
        five_team_rounds.append(row)
    five_team_inputs = {
        **deepcopy(CRISIS_INPUTS),
        "E": {"exposure": 0.2, "preempt": 0.5, "response": 0.5},
    }

    inside = run_game(
        deepcopy(five_team_rounds),
        crisis_params=replace(DEFAULT_CRISIS_PARAMS, crisis_round=2),
        crisis_inputs=deepcopy(five_team_inputs),
    )
    beyond = run_game(
        deepcopy(five_team_rounds),
        crisis_params=replace(DEFAULT_CRISIS_PARAMS, crisis_round=3),
    )

    assert len(inside) == 2
    assert set(inside[1]["damage"]) == {"A", "B", "C", "D", "E"}
    assert set(inside[1]["equity"]) == {"A", "B", "C", "D", "E"}
    assert len(beyond) == 2
    assert all(record["damage"] == {} for record in beyond)


def test_pricing_pass_through_with_zero_promotion_uses_financial_identities():
    reference_history = run_game(_no_promotion_rounds(), crisis_inputs=deepcopy(CRISIS_INPUTS))
    priced_history = run_game(_priced_no_promotion_rounds(), crisis_inputs=deepcopy(CRISIS_INPUTS))

    assert any(
        priced_history[round_index]["firms"][team]["share"] != reference_history[round_index]["firms"][team]["share"]
        for round_index in range(6)
        for team in TEAMS
    )
    for reference_record, priced_record in zip(reference_history, priced_history, strict=True):
        assert priced_record["equity"] == reference_record["equity"]
        assert priced_record["attribute_state"] == reference_record["attribute_state"]
        assert priced_record["deal_conditioning"] == reference_record["deal_conditioning"]
        for team in TEAMS:
            firm = priced_record["firms"][team]
            assert firm["revenue"] == pytest.approx(firm["total_volume"] * PRICES[team])
            assert firm["contribution"] == pytest.approx(
                firm["total_volume"] * (PRICES[team] - DEFAULT_PARAMS.variable_cost) - DEFAULT_PARAMS.budget
            )


def test_game_purity_and_determinism():
    decisions = _promotion_rounds()
    growth_rates = dict(DEFAULT_GROWTH_RATES)
    crisis_inputs = deepcopy(CRISIS_INPUTS)
    custom_params = replace(
        DEFAULT_PARAMS,
        segments=tuple(
            Segment(
                segment.name,
                segment.size,
                segment.positioning_ideal,
                segment.beta,
                segment.weights,
                segment.ideal_price,
                segment.deal_sensitivity,
            )
            for segment in DEFAULT_PARAMS.segments
        ),
    )
    equity_params = replace(DEFAULT_EQUITY_PARAMS)
    crisis_params = replace(DEFAULT_CRISIS_PARAMS)
    promotion_params = replace(DEFAULT_PROMOTION_PARAMS)
    originals = (
        deepcopy(decisions),
        deepcopy(growth_rates),
        deepcopy(crisis_inputs),
        custom_params,
        equity_params,
        crisis_params,
        promotion_params,
        DEFAULT_PARAMS,
        DEFAULT_EQUITY_PARAMS,
        DEFAULT_CRISIS_PARAMS,
        DEFAULT_PROMOTION_PARAMS,
        deepcopy(dict(DEFAULT_GROWTH_RATES)),
    )

    first = run_game(
        decisions,
        custom_params,
        growth_rates,
        equity_params,
        crisis_params,
        promotion_params,
        crisis_inputs,
    )
    second = run_game(
        deepcopy(originals[0]),
        custom_params,
        dict(DEFAULT_GROWTH_RATES),
        equity_params,
        crisis_params,
        promotion_params,
        deepcopy(CRISIS_INPUTS),
    )

    assert first == second
    assert decisions == originals[0]
    assert growth_rates == originals[1]
    assert crisis_inputs == originals[2]
    assert custom_params == originals[3]
    assert equity_params == originals[4]
    assert crisis_params == originals[5]
    assert promotion_params == originals[6]
    assert DEFAULT_PARAMS == originals[7]
    assert DEFAULT_EQUITY_PARAMS == originals[8]
    assert DEFAULT_CRISIS_PARAMS == originals[9]
    assert DEFAULT_PROMOTION_PARAMS == originals[10]
    assert dict(DEFAULT_GROWTH_RATES) == originals[11]
