from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from types import MappingProxyType

import pytest

import game_faculty
from crisis import DEFAULT_CRISIS_PARAMS, crisis_damage
from engine import DEFAULT_PARAMS
from equity import DEFAULT_EQUITY_PARAMS, EquityParams, next_equity
from faculty import DEFAULT_FACULTY_CONFIG, FacultyConfig, build_effective_decisions
from game import run_game
from game_faculty import run_game_with_faculty
from promotion import DEFAULT_PROMOTION_PARAMS, PromotionParams
from simulation import DEFAULT_GROWTH_RATES
from test_step3c import BASELINE_PROD, BASELINE_TRUST, CRISIS_INPUTS
from test_step4 import (
    ALL_LEVERS,
    ALLOCATION,
    BASELINE,
    EMPHASIS,
    GOLDEN_CONFIG,
    POSITIONING,
    PRICE,
    PROMOTION,
    TEAMS,
    LISTENING_EQ,
    LISTENING_SHARE,
    _golden_submissions,
)
from faculty import run_simulation_with_faculty


GAME_FACULTY_KEYS = {
    "round",
    "segment_sizes",
    "firms",
    "scorecard",
    "equity",
    "attribute_state",
    "damage",
    "deal_conditioning",
    "promotion_depth",
    "open_levers",
    "effective_decisions",
}

TEN_ROUND_CONFIG = replace(DEFAULT_FACULTY_CONFIG, num_rounds=10)
ROUND9_CRISIS_PARAMS = replace(DEFAULT_CRISIS_PARAMS, crisis_round=9)
GOLDEN_EQ_10 = {
    "A": (67.7080, 71.9889, 75.1997, 74.9208, 74.7117, 71.3548, 68.8372, 66.9490, 64.5556, 63.7378),
    "B": (67.7080, 71.9889, 75.1997, 78.1147, 80.3010, 81.9407, 83.1705, 84.0928, 84.7754, 85.2965),
    "C": (67.7080, 71.9889, 75.1997, 76.9567, 78.2745, 77.6628, 77.2041, 76.8600, 76.3914, 76.2505),
    "D": (67.7080, 71.9889, 75.1997, 77.6077, 79.4137, 79.9683, 80.3842, 80.6961, 80.6964, 80.9303),
}
GOLDEN_SHARE_10 = {
    "A": (11.5405, 11.6200, 11.6687, 11.7897, 11.7752, 12.5058, 11.8723, 11.2148, 10.7130, 10.4483),
    "B": (11.5405, 11.6200, 11.6687, 11.6916, 12.0463, 11.9349, 12.1464, 11.9520, 12.1149, 12.2192),
    "C": (11.5405, 11.6200, 11.6687, 11.7247, 12.4149, 12.6929, 12.5267, 12.4984, 12.3803, 12.2981),
    "D": (11.5405, 11.6200, 11.6687, 11.6624, 12.2993, 12.3988, 12.4551, 12.4966, 12.5076, 12.5427),
}
GOLDEN_DAMAGE_R9 = {"A": 4.8857, "B": 0.0456, "C": 1.0529, "D": 1.1680}
GOLDEN_TRUST_R9_R10 = {
    "A": (29.5571, 29.5571),
    "B": (31.9772, 31.9772),
    "C": (31.4735, 31.4735),
    "D": (31.4160, 31.4160),
}
GOLDEN_PROD_R9_R10 = {
    "A": (28.5343, 28.5343),
    "B": (29.9863, 29.9863),
    "C": (29.6841, 29.6841),
    "D": (29.6496, 29.6496),
}
GOLDEN_ENTERING_DC_R6_R10 = {
    "A": (0.0000, 0.4000, 0.6800, 0.8760, 1.0132),
    "B": (0.0000, 0.0000, 0.0000, 0.0000, 0.0000),
    "C": (0.0000, 0.2000, 0.3400, 0.4380, 0.5066),
    "D": (0.0000, 0.1000, 0.1700, 0.2190, 0.2533),
}
GOLDEN_CONTRIBUTION_R10 = {"A": -5.8667, "B": 5.4415, "C": 0.5410, "D": 0.1993}
GOLDEN_PRE_CRISIS_EQ_R9 = {"A": 65.5328, "B": 84.7846, "C": 76.6020, "D": 80.9300}
GOLDEN_EQ_CRISIS_DEBIT_R9 = {"A": 0.9771, "B": 0.0091, "C": 0.2106, "D": 0.2336}


def _config(**overrides: object) -> FacultyConfig:
    return replace(GOLDEN_CONFIG, **overrides)


def _empty_submissions(num_rounds: int) -> list[dict[str, dict[str, object]]]:
    return [{} for _ in range(num_rounds)]


def _submission_for_round(round_number: int) -> dict[str, dict[str, object]]:
    if round_number < 4:
        return {}
    round_submission = {}
    for team in TEAMS:
        decision = {
            **ALLOCATION[team],
            "emphasis": EMPHASIS[team],
        }
        if round_number >= 5:
            decision["positioning"] = POSITIONING[team]
        if round_number >= 6:
            decision["promotion"] = PROMOTION[team]
        if round_number >= 8:
            decision["price"] = PRICE[team]
        round_submission[team] = decision
    return round_submission


def _ten_round_submissions() -> list[dict[str, dict[str, object]]]:
    return [_submission_for_round(round_number) for round_number in range(1, 11)]


def test_game_faculty_public_schema_and_progressive_metadata():
    history = run_game_with_faculty(TEAMS, _golden_submissions(), GOLDEN_CONFIG)

    expected_levers = {
        1: frozenset(),
        2: frozenset(),
        3: frozenset(),
        4: frozenset({"allocation", "emphasis"}),
        5: frozenset({"allocation", "emphasis", "positioning"}),
        6: frozenset({"allocation", "emphasis", "positioning", "promotion"}),
        7: frozenset({"allocation", "emphasis", "positioning", "promotion"}),
        8: ALL_LEVERS,
    }
    for record in history:
        assert set(record) == GAME_FACULTY_KEYS
        assert record["open_levers"] == expected_levers[record["round"]]

    for round_index in range(3):
        assert history[round_index]["open_levers"] == frozenset()
        for team in TEAMS:
            assert history[round_index]["effective_decisions"][team] == BASELINE

    round4 = history[3]["effective_decisions"]
    for team in TEAMS:
        assert {field: round4[team][field] for field in ("brand", "perf", "research")} == ALLOCATION[team]
        assert round4[team]["emphasis"] == EMPHASIS[team]
        assert round4[team]["positioning"] == BASELINE["positioning"]
        assert round4[team]["promotion"] == BASELINE["promotion"]
        assert round4[team]["price"] == BASELINE["price"]

    round8 = history[7]["effective_decisions"]
    for team in TEAMS:
        assert round8[team]["positioning"] == POSITIONING[team]
        assert round8[team]["promotion"] == PROMOTION[team]
        assert round8[team]["price"] == PRICE[team]


def test_game_faculty_listening_identity_is_fully_asserted():
    history = run_game_with_faculty(TEAMS, _golden_submissions(), GOLDEN_CONFIG)

    for round_index in range(3):
        record = history[round_index]
        first_team = TEAMS[0]
        first_decision = record["effective_decisions"][first_team]
        first_firm = record["firms"][first_team]
        first_scorecard = record["scorecard"]["firms"][first_team]

        for team in TEAMS:
            assert record["effective_decisions"][team] == first_decision == BASELINE
            assert record["equity"][team] == pytest.approx(LISTENING_EQ[round_index], abs=0.01)
            assert record["equity"][team] == record["equity"][first_team]
            assert record["firms"][team]["total_market_share"] * 100.0 == pytest.approx(
                LISTENING_SHARE[round_index], abs=0.01
            )
            assert record["firms"][team]["perception"] == first_firm["perception"]
            assert record["firms"][team]["share"] == first_firm["share"]
            assert record["firms"][team]["volume"] == first_firm["volume"]
            assert record["scorecard"]["firms"][team]["raw"] == first_scorecard["raw"]
            assert record["scorecard"]["firms"][team]["normalized"] == first_scorecard["normalized"]
            assert record["scorecard"]["firms"][team]["overall"] == first_scorecard["overall"]
            assert record["scorecard"]["firms"][team]["overall"] == pytest.approx(50.0)


def test_game_faculty_ten_round_integrated_golden_values():
    history = run_game_with_faculty(
        TEAMS,
        _ten_round_submissions(),
        TEN_ROUND_CONFIG,
        crisis_params=ROUND9_CRISIS_PARAMS,
        crisis_inputs=deepcopy(CRISIS_INPUTS),
    )

    assert len(history) == 10
    for round_index, record in enumerate(history):
        for team in TEAMS:
            assert record["equity"][team] == pytest.approx(GOLDEN_EQ_10[team][round_index], abs=0.01)
            assert record["firms"][team]["total_market_share"] * 100.0 == pytest.approx(
                GOLDEN_SHARE_10[team][round_index], abs=0.01
            )

    assert history[8]["damage"] == pytest.approx(GOLDEN_DAMAGE_R9, abs=0.001)
    for team in TEAMS:
        assert [history[index]["attribute_state"][team]["TRUST"] for index in (8, 9)] == pytest.approx(
            GOLDEN_TRUST_R9_R10[team], abs=0.01
        )
        assert [history[index]["attribute_state"][team]["PROD"] for index in (8, 9)] == pytest.approx(
            GOLDEN_PROD_R9_R10[team], abs=0.01
        )
        assert [history[index]["deal_conditioning"][team] for index in range(5, 10)] == pytest.approx(
            GOLDEN_ENTERING_DC_R6_R10[team], abs=0.001
        )
        assert history[9]["firms"][team]["contribution"] == pytest.approx(GOLDEN_CONTRIBUTION_R10[team], abs=0.01)


def test_game_faculty_pre_crisis_trust_prod_and_crisis_scar_formula():
    history = run_game_with_faculty(
        TEAMS,
        _ten_round_submissions(),
        TEN_ROUND_CONFIG,
        crisis_params=ROUND9_CRISIS_PARAMS,
        crisis_inputs=deepcopy(CRISIS_INPUTS),
    )

    for team in TEAMS:
        for round_index in range(8):
            assert history[round_index]["attribute_state"][team]["TRUST"] == 32.0
            assert history[round_index]["attribute_state"][team]["PROD"] == 30.0
        for round_index, expected_trust in zip((8, 9), GOLDEN_TRUST_R9_R10[team], strict=True):
            assert history[round_index]["attribute_state"][team]["TRUST"] == pytest.approx(expected_trust, abs=0.01)
        expected_prod_after_crisis = 30.0 - ROUND9_CRISIS_PARAMS.prod_fraction * history[8]["damage"][team]
        assert history[8]["attribute_state"][team]["PROD"] == pytest.approx(expected_prod_after_crisis)
        assert history[9]["attribute_state"][team]["PROD"] == pytest.approx(expected_prod_after_crisis)


def test_game_faculty_round9_crisis_debits_the_single_eq_stock():
    crisis_history = run_game_with_faculty(
        TEAMS,
        _ten_round_submissions(),
        TEN_ROUND_CONFIG,
        crisis_params=ROUND9_CRISIS_PARAMS,
        crisis_inputs=deepcopy(CRISIS_INPUTS),
    )
    no_crisis_history = run_game_with_faculty(
        TEAMS,
        _ten_round_submissions(),
        TEN_ROUND_CONFIG,
        crisis_params=replace(DEFAULT_CRISIS_PARAMS, crisis_round=99),
    )

    for team in TEAMS:
        assert no_crisis_history[8]["equity"][team] == pytest.approx(GOLDEN_PRE_CRISIS_EQ_R9[team], abs=0.01)
        assert no_crisis_history[8]["equity"][team] - crisis_history[8]["equity"][team] == pytest.approx(
            GOLDEN_EQ_CRISIS_DEBIT_R9[team], abs=0.01
        )
        assert crisis_history[8]["equity"][team] == crisis_history[8]["attribute_state"][team]["EQ"]


def test_game_faculty_round9_eq_is_recomputed_from_locked_functions():
    history = run_game_with_faculty(
        TEAMS,
        _ten_round_submissions(),
        TEN_ROUND_CONFIG,
        crisis_params=ROUND9_CRISIS_PARAMS,
        crisis_inputs=deepcopy(CRISIS_INPUTS),
    )

    for team in TEAMS:
        round9_decision = history[8]["effective_decisions"][team]
        post_recurrence_eq = next_equity(
            history[7]["equity"][team],
            round9_decision["brand"],
            round9_decision["perf"],
            DEFAULT_EQUITY_PARAMS,
            DEFAULT_PARAMS.budget,
            promo_depth=history[8]["promotion_depth"][team],
        )
        damage = crisis_damage(
            post_recurrence_eq,
            CRISIS_INPUTS[team]["exposure"],
            CRISIS_INPUTS[team]["preempt"],
            CRISIS_INPUTS[team]["response"],
            ROUND9_CRISIS_PARAMS,
        )
        expected_round9_eq = min(
            ROUND9_CRISIS_PARAMS.upper_bound,
            max(ROUND9_CRISIS_PARAMS.lower_bound, post_recurrence_eq - ROUND9_CRISIS_PARAMS.eq_fraction * damage),
        )
        assert history[8]["damage"][team] == pytest.approx(damage)
        assert history[8]["equity"][team] == pytest.approx(expected_round9_eq)

        round10_decision = history[9]["effective_decisions"][team]
        expected_round10_eq = next_equity(
            history[8]["equity"][team],
            round10_decision["brand"],
            round10_decision["perf"],
            DEFAULT_EQUITY_PARAMS,
            DEFAULT_PARAMS.budget,
            promo_depth=history[9]["promotion_depth"][team],
        )
        assert history[9]["equity"][team] == pytest.approx(expected_round10_eq)


def test_game_faculty_deal_conditioning_recurrence_and_crisis_independence():
    crisis_history = run_game_with_faculty(
        TEAMS,
        _ten_round_submissions(),
        TEN_ROUND_CONFIG,
        crisis_params=ROUND9_CRISIS_PARAMS,
        crisis_inputs=deepcopy(CRISIS_INPUTS),
    )
    no_crisis_history = run_game_with_faculty(
        TEAMS,
        _ten_round_submissions(),
        TEN_ROUND_CONFIG,
        crisis_params=replace(DEFAULT_CRISIS_PARAMS, crisis_round=99),
    )

    for team in TEAMS:
        for round_index in range(6):
            assert crisis_history[round_index]["deal_conditioning"][team] == 0.0
        for round_index in range(9):
            expected_next_entering_dc = (
                crisis_history[round_index]["deal_conditioning"][team]
                * (1.0 - DEFAULT_PROMOTION_PARAMS.dc_decay)
                + DEFAULT_PROMOTION_PARAMS.dc_gain * crisis_history[round_index]["promotion_depth"][team]
            )
            assert crisis_history[round_index + 1]["deal_conditioning"][team] == pytest.approx(
                expected_next_entering_dc
            )
        assert [record["deal_conditioning"][team] for record in crisis_history] == [
            record["deal_conditioning"][team] for record in no_crisis_history
        ]


def test_game_faculty_backdrops_are_excluded_from_team_state_and_metadata():
    history = run_game_with_faculty(
        TEAMS,
        _ten_round_submissions(),
        TEN_ROUND_CONFIG,
        crisis_params=ROUND9_CRISIS_PARAMS,
        crisis_inputs=deepcopy(CRISIS_INPUTS),
    )

    for record in history:
        assert isinstance(record["open_levers"], frozenset)
        for backdrop in ("BigRed", "Monssta", "Pulse"):
            assert backdrop in record["firms"]
            assert backdrop not in record["effective_decisions"]
            assert backdrop not in record["equity"]
            assert backdrop not in record["attribute_state"]
            assert backdrop not in record["deal_conditioning"]
            assert backdrop not in record["promotion_depth"]
            assert backdrop not in record["damage"]
            assert backdrop not in record["scorecard"]["firms"]


def test_game_faculty_matches_direct_game_for_built_effective_decisions():
    submissions = _golden_submissions()
    effective = build_effective_decisions(TEAMS, submissions, GOLDEN_CONFIG)
    faculty_history = run_game_with_faculty(
        TEAMS,
        deepcopy(submissions),
        GOLDEN_CONFIG,
        crisis_inputs=deepcopy(CRISIS_INPUTS),
    )
    game_history = run_game(deepcopy(effective), crisis_inputs=deepcopy(CRISIS_INPUTS))

    game_fields = (
        "round",
        "segment_sizes",
        "firms",
        "scorecard",
        "equity",
        "attribute_state",
        "damage",
        "deal_conditioning",
        "promotion_depth",
    )
    for faculty_record, game_record, effective_round in zip(faculty_history, game_history, effective, strict=True):
        for field in game_fields:
            assert faculty_record[field] == game_record[field]
        assert faculty_record["effective_decisions"] == effective_round


def test_game_faculty_no_crisis_matches_chunk4_exactly_on_shared_fields():
    faculty_game_history = run_game_with_faculty(
        TEAMS,
        _golden_submissions(),
        GOLDEN_CONFIG,
        crisis_params=replace(DEFAULT_CRISIS_PARAMS, crisis_round=99),
    )
    chunk4_history = run_simulation_with_faculty(TEAMS, _golden_submissions(), GOLDEN_CONFIG)

    chunk4_fields = (
        "round",
        "segment_sizes",
        "firms",
        "scorecard",
        "equity",
        "deal_conditioning",
        "promotion_depth",
        "open_levers",
        "effective_decisions",
    )
    for game_record, chunk4_record in zip(faculty_game_history, chunk4_history, strict=True):
        for field in chunk4_fields:
            assert game_record[field] == chunk4_record[field]
        assert game_record["damage"] == {}
        for team in TEAMS:
            assert game_record["attribute_state"][team]["EQ"] == chunk4_record["equity"][team]
            assert game_record["attribute_state"][team]["TRUST"] == BASELINE_TRUST
            assert game_record["attribute_state"][team]["PROD"] == BASELINE_PROD


def test_game_faculty_with_all_levers_open_passes_full_submissions_through():
    decisions = []
    for _ in range(4):
        decisions.append({
            team: {
                **ALLOCATION[team],
                "emphasis": EMPHASIS[team],
                "positioning": POSITIONING[team],
                "promotion": PROMOTION[team],
                "price": PRICE[team],
            }
            for team in TEAMS
        })
    config = replace(
        DEFAULT_FACULTY_CONFIG,
        num_rounds=4,
        takeover_round=1,
        lever_open_round=MappingProxyType({
            "allocation": 1,
            "emphasis": 1,
            "positioning": 1,
            "promotion": 1,
            "price": 1,
        }),
    )

    faculty_history = run_game_with_faculty(TEAMS, deepcopy(decisions), config, crisis_inputs=deepcopy(CRISIS_INPUTS))
    game_history = run_game(deepcopy(decisions), crisis_inputs=deepcopy(CRISIS_INPUTS))

    for faculty_record, game_record, submitted_round in zip(faculty_history, game_history, decisions, strict=True):
        assert faculty_record["open_levers"] == ALL_LEVERS
        assert faculty_record["effective_decisions"] == submitted_round
        assert faculty_record["firms"] == game_record["firms"]
        assert faculty_record["scorecard"] == game_record["scorecard"]
        assert faculty_record["equity"] == game_record["equity"]
        assert faculty_record["damage"] == game_record["damage"]
        assert faculty_record["deal_conditioning"] == game_record["deal_conditioning"]
        assert faculty_record["promotion_depth"] == game_record["promotion_depth"]


def test_game_faculty_price_open_override_applies_round4_price():
    config = _config(
        lever_open_round=MappingProxyType({
            "allocation": 4,
            "emphasis": 4,
            "positioning": 5,
            "promotion": 6,
            "price": 4,
        })
    )
    submissions = _golden_submissions()
    submissions[3]["A"]["price"] = 1.20

    history = run_game_with_faculty(TEAMS, submissions, config)

    assert "price" in history[3]["open_levers"]
    assert history[3]["effective_decisions"]["A"]["price"] == 1.20
    assert history[3]["firms"]["A"]["price"] == 1.20


def test_game_faculty_uses_omitted_crisis_input_zero_defaults():
    history = run_game_with_faculty(TEAMS, _golden_submissions(), GOLDEN_CONFIG)
    direct_game = run_game(build_effective_decisions(TEAMS, _golden_submissions(), GOLDEN_CONFIG))

    for faculty_record, game_record in zip(history, direct_game, strict=True):
        assert faculty_record["damage"] == game_record["damage"]
        if faculty_record["round"] == DEFAULT_CRISIS_PARAMS.crisis_round:
            assert set(faculty_record["damage"]) == set(TEAMS)
            assert all(value == 0.0 for value in faculty_record["damage"].values())
        for team in TEAMS:
            assert faculty_record["attribute_state"][team]["TRUST"] == BASELINE_TRUST
            assert faculty_record["attribute_state"][team]["PROD"] == BASELINE_PROD


def test_game_faculty_applies_real_crisis_inputs_and_preserves_state_identities():
    history = run_game_with_faculty(
        TEAMS,
        _golden_submissions(),
        GOLDEN_CONFIG,
        crisis_inputs=deepcopy(CRISIS_INPUTS),
    )

    crisis_record = history[DEFAULT_CRISIS_PARAMS.crisis_round - 1]
    assert set(crisis_record["damage"]) == set(TEAMS)
    assert any(value > 0.0 for value in crisis_record["damage"].values())
    for record in history:
        for team in TEAMS:
            assert record["equity"][team] == record["attribute_state"][team]["EQ"]
            assert record["firms"][team]["attributes"]["EQ"] == record["attribute_state"][team]["EQ"]
            assert record["firms"][team]["attributes"]["TRUST"] == record["attribute_state"][team]["TRUST"]
            assert record["firms"][team]["attributes"]["PROD"] == record["attribute_state"][team]["PROD"]
            assert record["firms"][team]["deal_conditioning"] == record["deal_conditioning"][team]
            assert record["firms"][team]["promotion"] == record["promotion_depth"][team]


def test_game_faculty_calls_builder_and_integrated_game_once(monkeypatch):
    builder_calls = []
    game_calls = []
    built_decisions = [{"A": deepcopy(BASELINE)}]

    def fake_builder(team_names, submissions_by_round, faculty_config, params):
        builder_calls.append((team_names, submissions_by_round, faculty_config, params))
        return built_decisions

    def fake_run_game(decisions_by_round, params, growth_rates, equity_params, crisis_params, promotion_params, crisis_inputs):
        game_calls.append((decisions_by_round, params, growth_rates, equity_params, crisis_params, promotion_params, crisis_inputs))
        return [
            {
                "round": 1,
                "segment_sizes": {},
                "firms": {},
                "scorecard": {},
                "equity": {},
                "attribute_state": {},
                "damage": {},
                "deal_conditioning": {},
                "promotion_depth": {},
            }
        ]

    monkeypatch.setattr(game_faculty, "build_effective_decisions", fake_builder)
    monkeypatch.setattr(game_faculty, "run_game", fake_run_game)
    config = replace(DEFAULT_FACULTY_CONFIG, num_rounds=1, takeover_round=2)
    submissions = [{}]

    history = run_game_with_faculty(("A",), submissions, config)

    assert len(builder_calls) == 1
    assert builder_calls[0] == (("A",), submissions, config, DEFAULT_PARAMS)
    assert len(game_calls) == 1
    assert game_calls[0][0] is built_decisions
    assert game_calls[0][1] == DEFAULT_PARAMS
    assert game_calls[0][2] == DEFAULT_GROWTH_RATES
    assert game_calls[0][3] == DEFAULT_EQUITY_PARAMS
    assert game_calls[0][4] == DEFAULT_CRISIS_PARAMS
    assert game_calls[0][5] == DEFAULT_PROMOTION_PARAMS
    assert game_calls[0][6] is None
    assert history[0]["open_levers"] == frozenset()
    assert history[0]["effective_decisions"] == {"A": BASELINE}


def test_game_faculty_uses_custom_params_for_effective_decisions_and_open_levers():
    custom_params = replace(DEFAULT_PARAMS, budget=12.0)
    custom_baseline = MappingProxyType({
        "brand": 6.0,
        "perf": 3.0,
        "research": 3.0,
        "positioning": 35.0,
        "emphasis": BASELINE["emphasis"],
        "price": 1.50,
        "promotion": 0.0,
    })
    config = replace(
        DEFAULT_FACULTY_CONFIG,
        num_rounds=1,
        takeover_round=1,
        lever_open_round=MappingProxyType({
            "allocation": 1,
            "emphasis": 1,
            "positioning": 1,
            "promotion": 1,
            "price": 1,
        }),
        baseline_decision=custom_baseline,
    )

    history = run_game_with_faculty(("A",), [{}], config, params=custom_params)

    assert history[0]["open_levers"] == ALL_LEVERS
    assert history[0]["effective_decisions"]["A"] == dict(custom_baseline)


def test_game_faculty_validation_is_inherited_from_faculty_and_game():
    submissions = _golden_submissions()
    submissions[4]["A"]["price"] = 1.20
    with pytest.raises(ValueError, match="price.*5.*8"):
        run_game_with_faculty(TEAMS, submissions, GOLDEN_CONFIG)

    with pytest.raises(ValueError, match="unknown team"):
        run_game_with_faculty(TEAMS, [{"Ghost": {"price": 1.2}}] + _empty_submissions(7), GOLDEN_CONFIG)

    bad_inputs = {team: {"exposure": 2.0, "preempt": 0.0, "response": 0.0} for team in TEAMS}
    with pytest.raises(ValueError):
        run_game_with_faculty(TEAMS, _golden_submissions(), GOLDEN_CONFIG, crisis_inputs=bad_inputs)

    bad_growth = dict(DEFAULT_GROWTH_RATES)
    bad_growth.pop("LEG")
    with pytest.raises(ValueError, match="growth_rates"):
        run_game_with_faculty(TEAMS, _golden_submissions(), GOLDEN_CONFIG, growth_rates=bad_growth)

    with pytest.raises(ValueError, match="k_brand"):
        run_game_with_faculty(
            TEAMS,
            _golden_submissions(),
            GOLDEN_CONFIG,
            equity_params=replace(DEFAULT_EQUITY_PARAMS, k_brand=0.0),
        )

    with pytest.raises(ValueError, match="dc_decay"):
        run_game_with_faculty(
            TEAMS,
            _golden_submissions(),
            GOLDEN_CONFIG,
            promotion_params=replace(DEFAULT_PROMOTION_PARAMS, dc_decay=2.0),
        )


def test_game_faculty_joined_api_faculty_validation_failures():
    with pytest.raises(ValueError, match="team_names"):
        run_game_with_faculty("A", _golden_submissions(), GOLDEN_CONFIG)

    with pytest.raises(ValueError, match="duplicate"):
        run_game_with_faculty(("A", "A"), _golden_submissions(), GOLDEN_CONFIG)

    with pytest.raises(ValueError, match="length"):
        run_game_with_faculty(TEAMS, _empty_submissions(7), GOLDEN_CONFIG)

    with pytest.raises(ValueError, match="non-string sequence"):
        run_game_with_faculty(TEAMS, "not submissions", GOLDEN_CONFIG)

    with pytest.raises(ValueError, match="round 1 submissions"):
        run_game_with_faculty(TEAMS, [1] + _empty_submissions(7), GOLDEN_CONFIG)

    with pytest.raises(ValueError, match="unknown team"):
        run_game_with_faculty(TEAMS, [{"Ghost": {}}] + _empty_submissions(7), GOLDEN_CONFIG)

    with pytest.raises(ValueError, match="must be a mapping"):
        run_game_with_faculty(TEAMS, [{"A": []}] + _empty_submissions(7), GOLDEN_CONFIG)

    with pytest.raises(ValueError, match="unknown decision field"):
        run_game_with_faculty(TEAMS, [{"A": {"coupon": 1.0}}] + _empty_submissions(7), GOLDEN_CONFIG)

    malformed_config = replace(GOLDEN_CONFIG, num_rounds=0)
    with pytest.raises(ValueError, match="num_rounds"):
        run_game_with_faculty(TEAMS, _golden_submissions(), malformed_config)


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
    ],
)
def test_game_faculty_joined_api_crisis_input_validation_failures(bad_inputs):
    with pytest.raises(ValueError):
        run_game_with_faculty(TEAMS, _golden_submissions(), GOLDEN_CONFIG, crisis_inputs=bad_inputs)


def test_game_faculty_open_ended_fifth_team_short_run():
    teams = ("A", "B", "C", "D", "E")
    config = replace(
        DEFAULT_FACULTY_CONFIG,
        num_rounds=2,
        takeover_round=2,
        lever_open_round=MappingProxyType({
            "allocation": 2,
            "emphasis": 99,
            "positioning": 99,
            "promotion": 99,
            "price": 99,
        }),
    )
    submissions = [
        {},
        {team: {"brand": 4.0, "perf": 4.0, "research": 2.0} for team in teams},
    ]

    history = run_game_with_faculty(teams, submissions, config, crisis_params=replace(DEFAULT_CRISIS_PARAMS, crisis_round=2))

    assert len(history) == 2
    assert history[0]["open_levers"] == frozenset()
    assert history[1]["open_levers"] == frozenset({"allocation"})
    assert set(history[1]["effective_decisions"]) == set(teams)
    assert set(history[1]["damage"]) == set(teams)
    assert all(value == 0.0 for value in history[1]["damage"].values())


def test_game_faculty_short_run_crisis_beyond_horizon_keeps_damage_empty_and_baseline_scar_attrs():
    teams = ("A", "B", "C", "D", "E")
    config = replace(
        DEFAULT_FACULTY_CONFIG,
        num_rounds=2,
        takeover_round=2,
        lever_open_round=MappingProxyType({
            "allocation": 2,
            "emphasis": 99,
            "positioning": 99,
            "promotion": 99,
            "price": 99,
        }),
    )
    submissions = [
        {},
        {team: {"brand": 4.0, "perf": 4.0, "research": 2.0} for team in teams},
    ]

    history = run_game_with_faculty(
        teams,
        submissions,
        config,
        crisis_params=replace(DEFAULT_CRISIS_PARAMS, crisis_round=3),
    )

    assert len(history) == 2
    for record in history:
        assert record["damage"] == {}
        for team in teams:
            assert record["attribute_state"][team]["TRUST"] == BASELINE_TRUST
            assert record["attribute_state"][team]["PROD"] == BASELINE_PROD


def test_game_faculty_outputs_are_independent_from_inputs_and_builder_results():
    submissions = _golden_submissions()
    effective = build_effective_decisions(TEAMS, deepcopy(submissions), GOLDEN_CONFIG)
    history = run_game_with_faculty(TEAMS, submissions, GOLDEN_CONFIG)

    submissions[7]["A"]["price"] = 1.90
    effective[7]["A"]["price"] = 1.90
    history[7]["effective_decisions"]["A"]["price"] = 1.10

    fresh = run_game_with_faculty(TEAMS, _golden_submissions(), GOLDEN_CONFIG)
    assert fresh[7]["effective_decisions"]["A"]["price"] == 1.20
    assert history[7]["firms"]["A"]["price"] == 1.20


def test_game_faculty_purity_and_determinism():
    team_names = list(TEAMS)
    submissions = _golden_submissions()
    growth_rates = dict(DEFAULT_GROWTH_RATES)
    crisis_inputs = deepcopy(CRISIS_INPUTS)
    config = replace(
        DEFAULT_FACULTY_CONFIG,
        num_rounds=8,
        lever_open_round=dict(GOLDEN_CONFIG.lever_open_round),
        baseline_decision=dict(GOLDEN_CONFIG.baseline_decision),
    )
    originals = (
        deepcopy(team_names),
        deepcopy(submissions),
        deepcopy(growth_rates),
        deepcopy(crisis_inputs),
        config,
        DEFAULT_PARAMS,
        DEFAULT_EQUITY_PARAMS,
        DEFAULT_PROMOTION_PARAMS,
        DEFAULT_CRISIS_PARAMS,
        deepcopy(dict(DEFAULT_GROWTH_RATES)),
    )

    first = run_game_with_faculty(
        team_names,
        submissions,
        config,
        DEFAULT_PARAMS,
        growth_rates,
        DEFAULT_EQUITY_PARAMS,
        DEFAULT_PROMOTION_PARAMS,
        DEFAULT_CRISIS_PARAMS,
        crisis_inputs,
    )
    second = run_game_with_faculty(
        list(TEAMS),
        _golden_submissions(),
        config,
        DEFAULT_PARAMS,
        dict(DEFAULT_GROWTH_RATES),
        DEFAULT_EQUITY_PARAMS,
        DEFAULT_PROMOTION_PARAMS,
        DEFAULT_CRISIS_PARAMS,
        deepcopy(CRISIS_INPUTS),
    )

    assert first == second
    assert team_names == originals[0]
    assert submissions == originals[1]
    assert growth_rates == originals[2]
    assert crisis_inputs == originals[3]
    assert config == originals[4]
    assert DEFAULT_PARAMS == originals[5]
    assert DEFAULT_EQUITY_PARAMS == originals[6]
    assert DEFAULT_PROMOTION_PARAMS == originals[7]
    assert DEFAULT_CRISIS_PARAMS == originals[8]
    assert dict(DEFAULT_GROWTH_RATES) == originals[9]
