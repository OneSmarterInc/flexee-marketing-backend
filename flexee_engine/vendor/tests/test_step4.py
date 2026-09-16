from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from types import MappingProxyType

import pytest

import faculty
from engine import DEFAULT_PARAMS
from equity import DEFAULT_EQUITY_PARAMS, next_equity
from promotion import DEFAULT_PROMOTION_PARAMS, run_simulation_with_promotion
from simulation import DEFAULT_GROWTH_RATES
from faculty import (
    DEFAULT_FACULTY_CONFIG,
    FacultyConfig,
    build_effective_decisions,
    open_levers_for_round,
    run_simulation_with_faculty,
)


TEAMS = ("A", "B", "C", "D")
SEGMENTS = ("LEG", "FIT", "FUN", "GENZ", "MAIN")
GOLDEN_CONFIG = replace(DEFAULT_FACULTY_CONFIG, num_rounds=8)
ALL_LEVERS = frozenset({"allocation", "emphasis", "positioning", "promotion", "price"})

BASELINE = {
    "brand": 5.0,
    "perf": 2.0,
    "research": 3.0,
    "positioning": 35.0,
    "emphasis": (0.20, 0.20, 0.20, 0.20, 0.20),
    "price": 1.50,
    "promotion": 0.0,
}

ALLOCATION = {
    "A": {"brand": 3, "perf": 6, "research": 1},
    "B": {"brand": 6, "perf": 2, "research": 2},
    "C": {"brand": 4, "perf": 4, "research": 2},
    "D": {"brand": 5, "perf": 3, "research": 2},
}
EMPHASIS = {
    "A": (0.45, 0.15, 0.05, 0.05, 0.30),
    "B": (0.10, 0.20, 0.40, 0.25, 0.05),
    "C": (0.25, 0.20, 0.25, 0.15, 0.15),
    "D": (0.15, 0.10, 0.30, 0.40, 0.05),
}
POSITIONING = {"A": 20, "B": 82, "C": 52, "D": 72}
PROMOTION = {"A": 0.8, "B": 0.0, "C": 0.4, "D": 0.2}
PRICE = {"A": 1.20, "B": 1.75, "C": 1.50, "D": 1.40}

LISTENING_EQ = [67.7080, 71.9889, 75.1997]
LISTENING_SHARE = [11.5405, 11.6200, 11.6687]
EQUITY_TRAJECTORY = {
    "A": [67.7080, 71.9889, 75.1997, 74.9208, 74.7117, 71.3548, 68.8372, 66.9490],
    "B": [67.7080, 71.9889, 75.1997, 78.1147, 80.3010, 81.9407, 83.1705, 84.0928],
    "C": [67.7080, 71.9889, 75.1997, 76.9567, 78.2745, 77.6628, 77.2041, 76.8600],
    "D": [67.7080, 71.9889, 75.1997, 77.6077, 79.4137, 79.9683, 80.3842, 80.6961],
}
ROUND4_SHARE = {"A": 11.7897, "B": 11.6916, "C": 11.7247, "D": 11.6624}
ROUND8_SHARE = {"A": 11.2148, "B": 11.9520, "C": 12.4984, "D": 12.4966}
ROUND8_CONTRIBUTION = {"A": -5.6645, "B": 4.7598, "C": 0.4686, "D": -0.0696}


def _submission_for_round(round_number: int) -> dict[str, dict[str, object]]:
    if round_number < 4:
        return {}

    round_submission: dict[str, dict[str, object]] = {}
    for team in TEAMS:
        decision: dict[str, object] = {
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


def _golden_submissions() -> list[dict[str, dict[str, object]]]:
    return [_submission_for_round(round_number) for round_number in range(1, 9)]


def _full_decision(team: str) -> dict[str, object]:
    return {
        **ALLOCATION[team],
        "emphasis": EMPHASIS[team],
        "positioning": POSITIONING[team],
        "promotion": PROMOTION[team],
        "price": PRICE[team],
    }


def _full_decisions_by_round(n_rounds: int) -> list[dict[str, dict[str, object]]]:
    return [
        {team: deepcopy(_full_decision(team)) for team in TEAMS}
        for _ in range(n_rounds)
    ]


def _config(**overrides: object) -> FacultyConfig:
    return replace(GOLDEN_CONFIG, **overrides)


def _baseline_config(**overrides: object) -> FacultyConfig:
    return replace(DEFAULT_FACULTY_CONFIG, **overrides)


def _empty_submissions(num_rounds: int) -> list[dict[str, dict[str, object]]]:
    return [{} for _ in range(num_rounds)]


def test_default_faculty_config_is_locked_to_chunk4_defaults():
    assert DEFAULT_FACULTY_CONFIG.num_rounds == 14
    assert DEFAULT_FACULTY_CONFIG.takeover_round == 4
    assert dict(DEFAULT_FACULTY_CONFIG.lever_open_round) == {
        "allocation": 4,
        "emphasis": 4,
        "positioning": 5,
        "promotion": 6,
        "price": 8,
    }
    assert dict(DEFAULT_FACULTY_CONFIG.baseline_decision) == BASELINE
    with pytest.raises(TypeError):
        DEFAULT_FACULTY_CONFIG.lever_open_round["price"] = 4  # type: ignore[index]
    with pytest.raises(TypeError):
        DEFAULT_FACULTY_CONFIG.baseline_decision["price"] = 1.2  # type: ignore[index]


def test_open_levers_follow_progressive_schedule():
    expected = {
        1: frozenset(),
        2: frozenset(),
        3: frozenset(),
        4: frozenset({"allocation", "emphasis"}),
        5: frozenset({"allocation", "emphasis", "positioning"}),
        6: frozenset({"allocation", "emphasis", "positioning", "promotion"}),
        7: frozenset({"allocation", "emphasis", "positioning", "promotion"}),
        8: ALL_LEVERS,
    }
    for round_number, levers in expected.items():
        opened = open_levers_for_round(round_number, GOLDEN_CONFIG)
        assert opened == levers
        assert isinstance(opened, frozenset)


def test_golden_listening_rounds_are_baseline_identity_rounds():
    history = run_simulation_with_faculty(TEAMS, _golden_submissions(), GOLDEN_CONFIG)
    repeated_eq = DEFAULT_EQUITY_PARAMS.eq_start

    for round_index in range(3):
        record = history[round_index]
        repeated_eq = next_equity(
            repeated_eq,
            BASELINE["brand"],
            BASELINE["perf"],
            DEFAULT_EQUITY_PARAMS,
            DEFAULT_PARAMS.budget,
            promo_depth=0.0,
        )
        assert record["open_levers"] == frozenset()

        decisions = record["effective_decisions"]
        ids = {id(decisions[team]) for team in TEAMS}
        assert len(ids) == len(TEAMS)

        first_firm = record["firms"]["A"]
        first_score = record["scorecard"]["firms"]["A"]
        for team in TEAMS:
            assert decisions[team] == BASELINE
            assert record["equity"][team] == pytest.approx(LISTENING_EQ[round_index], abs=0.01)
            assert record["equity"][team] == pytest.approx(repeated_eq, abs=1e-12)
            assert record["firms"][team]["total_market_share"] * 100.0 == pytest.approx(
                LISTENING_SHARE[round_index], abs=0.01
            )
            assert record["firms"][team]["perception"] == first_firm["perception"]
            assert record["firms"][team]["share"] == first_firm["share"]
            assert record["firms"][team]["volume"] == first_firm["volume"]
            assert record["scorecard"]["firms"][team]["raw"] == first_score["raw"]
            assert record["scorecard"]["firms"][team]["normalized"] == first_score["normalized"]
            assert record["scorecard"]["firms"][team]["overall"] == first_score["overall"]
            assert record["scorecard"]["firms"][team]["overall"] == pytest.approx(50.0)


def test_golden_equity_trajectory_round4_and_round8_outputs():
    history = run_simulation_with_faculty(TEAMS, _golden_submissions(), GOLDEN_CONFIG)

    for round_index, record in enumerate(history):
        for team in TEAMS:
            assert record["equity"][team] == pytest.approx(
                EQUITY_TRAJECTORY[team][round_index], abs=0.01
            )

    round4 = history[3]
    for team in TEAMS:
        decision = round4["effective_decisions"][team]
        assert {field: decision[field] for field in ("brand", "perf", "research")} == ALLOCATION[team]
        assert decision["emphasis"] == EMPHASIS[team]
        assert decision["positioning"] == BASELINE["positioning"]
        assert decision["promotion"] == BASELINE["promotion"]
        assert decision["price"] == BASELINE["price"]
        assert round4["firms"][team]["total_market_share"] * 100.0 == pytest.approx(
            ROUND4_SHARE[team], abs=0.01
        )

    round8 = history[7]
    assert round8["open_levers"] == ALL_LEVERS
    for team in TEAMS:
        decision = round8["effective_decisions"][team]
        assert decision["price"] == PRICE[team]
        assert decision["promotion"] == PROMOTION[team]
        assert round8["firms"][team]["price"] == PRICE[team]
        assert round8["promotion_depth"][team] == PROMOTION[team]
        assert round8["firms"][team]["total_market_share"] * 100.0 == pytest.approx(
            ROUND8_SHARE[team], abs=0.01
        )
        assert round8["firms"][team]["contribution"] == pytest.approx(
            ROUND8_CONTRIBUTION[team], abs=0.01
        )


def test_effective_decisions_change_only_when_their_lever_opens():
    effective = build_effective_decisions(TEAMS, _golden_submissions(), GOLDEN_CONFIG)

    for round_number, round_decisions in enumerate(effective, start=1):
        for team in TEAMS:
            decision = round_decisions[team]
            if round_number < 4:
                assert decision == BASELINE
            if round_number == 4:
                assert decision["brand"] == ALLOCATION[team]["brand"]
                assert decision["perf"] == ALLOCATION[team]["perf"]
                assert decision["research"] == ALLOCATION[team]["research"]
                assert decision["emphasis"] == EMPHASIS[team]
                assert decision["positioning"] == BASELINE["positioning"]
                assert decision["promotion"] == BASELINE["promotion"]
                assert decision["price"] == BASELINE["price"]
            if round_number == 5:
                assert decision["positioning"] == POSITIONING[team]
                assert decision["promotion"] == BASELINE["promotion"]
                assert decision["price"] == BASELINE["price"]
            if round_number in (6, 7):
                assert decision["promotion"] == PROMOTION[team]
                assert decision["price"] == BASELINE["price"]
            if round_number == 8:
                assert decision == _full_decision(team)


def test_faculty_override_can_open_price_in_round4():
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

    history = run_simulation_with_faculty(TEAMS, submissions, config)

    assert "price" in history[3]["open_levers"]
    assert history[3]["effective_decisions"]["A"]["price"] == 1.20
    assert history[3]["firms"]["A"]["price"] == 1.20


def test_default_schedule_rejects_round4_nonreference_price():
    submissions = _golden_submissions()
    submissions[3]["A"]["price"] = 1.20

    with pytest.raises(ValueError, match="price.*4.*8"):
        build_effective_decisions(TEAMS, submissions, GOLDEN_CONFIG)


def test_closed_price_gating_allows_reference_price_only_until_round8():
    submissions = _golden_submissions()
    submissions[4]["A"]["price"] = 1.20
    with pytest.raises(ValueError, match="price.*5.*8"):
        build_effective_decisions(TEAMS, submissions, GOLDEN_CONFIG)

    submissions = _golden_submissions()
    submissions[4]["A"]["price"] = 1.50
    effective = build_effective_decisions(TEAMS, submissions, GOLDEN_CONFIG)
    assert effective[4]["A"]["price"] == 1.50


def test_lever_after_num_rounds_never_opens_and_baseline_noop_is_allowed():
    config = _config(
        lever_open_round=MappingProxyType({
            "allocation": 4,
            "emphasis": 4,
            "positioning": 5,
            "promotion": 6,
            "price": 99,
        })
    )
    assert "price" not in open_levers_for_round(8, config)

    submissions = _golden_submissions()
    with pytest.raises(ValueError, match="price.*8.*99"):
        build_effective_decisions(TEAMS, submissions, config)

    submissions = _golden_submissions()
    submissions[7]["A"]["price"] = 1.50
    for team in ("B", "C", "D"):
        submissions[7][team].pop("price")
    effective = build_effective_decisions(TEAMS, submissions, config)
    assert effective[7]["A"]["price"] == 1.50
    assert effective[7]["B"]["price"] == 1.50


def test_closed_and_open_allocation_gating():
    config = _config(
        lever_open_round=MappingProxyType({
            "allocation": 6,
            "emphasis": 4,
            "positioning": 5,
            "promotion": 6,
            "price": 8,
        })
    )
    submissions = _golden_submissions()
    with pytest.raises(ValueError, match="allocation.*4.*6"):
        build_effective_decisions(TEAMS, submissions, config)

    submissions = _golden_submissions()
    submissions[3] = {
        "A": {
            "brand": BASELINE["brand"],
            "perf": BASELINE["perf"],
            "research": BASELINE["research"],
            "emphasis": EMPHASIS["A"],
        }
    }
    submissions[4] = {}
    effective = build_effective_decisions(TEAMS, submissions, config)
    assert effective[3]["A"]["brand"] == BASELINE["brand"]

    submissions = _golden_submissions()
    submissions[3]["A"] = {"brand": 3, "emphasis": EMPHASIS["A"]}
    with pytest.raises(ValueError, match="allocation"):
        build_effective_decisions(TEAMS, submissions, GOLDEN_CONFIG)

    submissions = _golden_submissions()
    effective = build_effective_decisions(TEAMS, submissions, GOLDEN_CONFIG)
    assert effective[3]["A"]["brand"] == 3
    assert effective[3]["A"]["perf"] == 6
    assert effective[3]["A"]["research"] == 1


def test_listening_content_is_ignored_but_structure_is_validated():
    submissions = _golden_submissions()
    submissions[0] = {"A": {"price": -999, "promotion": 999, "brand": -5}}

    effective = build_effective_decisions(TEAMS, submissions, GOLDEN_CONFIG)

    assert effective[0]["A"] == BASELINE

    with pytest.raises(ValueError, match="unknown team"):
        build_effective_decisions(TEAMS, [{"Ghost": {"price": -999}}] + _empty_submissions(7), GOLDEN_CONFIG)
    with pytest.raises(ValueError, match="must be a mapping"):
        build_effective_decisions(TEAMS, [{"A": 1}] + _empty_submissions(7), GOLDEN_CONFIG)
    with pytest.raises(ValueError, match="unknown decision field"):
        build_effective_decisions(TEAMS, [{"A": {"coupon": 1}}] + _empty_submissions(7), GOLDEN_CONFIG)


def test_takeover_round_after_final_round_means_entire_run_is_listening():
    config = _config(takeover_round=9)
    submissions = _golden_submissions()

    history = run_simulation_with_faculty(TEAMS, submissions, config)

    for record in history:
        assert record["open_levers"] == frozenset()
        for team in TEAMS:
            assert record["effective_decisions"][team] == BASELINE


def test_faculty_pass_through_matches_direct_promotion_when_all_levers_open():
    decisions_by_round = _full_decisions_by_round(4)
    config = _baseline_config(
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

    faculty_history = run_simulation_with_faculty(TEAMS, deepcopy(decisions_by_round), config)
    promotion_history = run_simulation_with_promotion(deepcopy(decisions_by_round))

    underlying_fields = (
        "round",
        "segment_sizes",
        "firms",
        "scorecard",
        "equity",
        "deal_conditioning",
        "promotion_depth",
    )
    for faculty_record, promotion_record, submissions in zip(
        faculty_history, promotion_history, decisions_by_round, strict=True
    ):
        for field in underlying_fields:
            assert faculty_record[field] == promotion_record[field]
        assert faculty_record["open_levers"] == ALL_LEVERS
        assert faculty_record["effective_decisions"] == submissions


def test_run_simulation_with_faculty_uses_custom_params_for_open_lever_metadata():
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
    config = _baseline_config(
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

    with pytest.raises(ValueError, match="spending"):
        open_levers_for_round(1, config)

    assert open_levers_for_round(1, config, custom_params) == ALL_LEVERS
    history = run_simulation_with_faculty(("A",), [{}], config, params=custom_params)

    assert history[0]["open_levers"] == ALL_LEVERS
    assert history[0]["effective_decisions"]["A"] == dict(custom_baseline)


def test_history_effective_decisions_are_independent_copies():
    submissions = _golden_submissions()
    effective = build_effective_decisions(TEAMS, submissions, GOLDEN_CONFIG)
    history = run_simulation_with_faculty(TEAMS, submissions, GOLDEN_CONFIG)

    effective[7]["A"]["price"] = 1.90
    submissions[7]["A"]["price"] = 1.90
    history[7]["effective_decisions"]["A"]["price"] = 1.10

    fresh = run_simulation_with_faculty(TEAMS, _golden_submissions(), GOLDEN_CONFIG)
    assert fresh[7]["effective_decisions"]["A"]["price"] == 1.20
    assert history[7]["firms"]["A"]["price"] == 1.20


def test_purity_and_determinism_for_inputs_and_defaults():
    team_names = list(TEAMS)
    submissions = _golden_submissions()
    growth_rates = dict(DEFAULT_GROWTH_RATES)
    lever_open_round = dict(GOLDEN_CONFIG.lever_open_round)
    baseline_decision = dict(GOLDEN_CONFIG.baseline_decision)
    config = FacultyConfig(
        num_rounds=8,
        takeover_round=4,
        lever_open_round=lever_open_round,
        baseline_decision=baseline_decision,
    )

    originals = (
        deepcopy(team_names),
        deepcopy(submissions),
        deepcopy(growth_rates),
        deepcopy(lever_open_round),
        deepcopy(baseline_decision),
        deepcopy(dict(DEFAULT_FACULTY_CONFIG.lever_open_round)),
        deepcopy(dict(DEFAULT_FACULTY_CONFIG.baseline_decision)),
        DEFAULT_PARAMS,
        deepcopy(dict(DEFAULT_GROWTH_RATES)),
        DEFAULT_EQUITY_PARAMS,
        DEFAULT_PROMOTION_PARAMS,
    )

    first = run_simulation_with_faculty(
        team_names,
        submissions,
        config,
        growth_rates=growth_rates,
    )
    second = run_simulation_with_faculty(
        list(TEAMS),
        _golden_submissions(),
        config,
        growth_rates=dict(DEFAULT_GROWTH_RATES),
    )

    assert first == second
    assert team_names == originals[0]
    assert submissions == originals[1]
    assert growth_rates == originals[2]
    assert lever_open_round == originals[3]
    assert baseline_decision == originals[4]
    assert dict(DEFAULT_FACULTY_CONFIG.lever_open_round) == originals[5]
    assert dict(DEFAULT_FACULTY_CONFIG.baseline_decision) == originals[6]
    assert DEFAULT_PARAMS == originals[7]
    assert dict(DEFAULT_GROWTH_RATES) == originals[8]
    assert DEFAULT_EQUITY_PARAMS == originals[9]
    assert DEFAULT_PROMOTION_PARAMS == originals[10]


def test_run_simulation_with_faculty_calls_promotion_runner_once(monkeypatch):
    calls = []

    def fake_runner(decisions_by_round, params, growth_rates, equity_params, promotion_params):
        calls.append((decisions_by_round, params, growth_rates, equity_params, promotion_params))
        return [
            {
                "round": 1,
                "segment_sizes": {},
                "firms": {},
                "scorecard": {},
                "equity": {},
                "deal_conditioning": {},
                "promotion_depth": {},
            }
        ]

    monkeypatch.setattr(faculty, "run_simulation_with_promotion", fake_runner)
    config = _baseline_config(num_rounds=1, takeover_round=2)

    history = run_simulation_with_faculty(("A",), [{}], config)

    assert len(calls) == 1
    assert calls[0][0] == [{"A": BASELINE}]
    assert history[0]["open_levers"] == frozenset()
    assert history[0]["effective_decisions"] == {"A": BASELINE}


def test_open_ended_five_team_short_run_with_custom_schedule():
    teams = ("A", "B", "C", "D", "E")
    config = _baseline_config(
        num_rounds=3,
        takeover_round=2,
        lever_open_round=MappingProxyType({
            "allocation": 2,
            "emphasis": 3,
            "positioning": 3,
            "promotion": 99,
            "price": 99,
        }),
    )
    submissions = [
        {},
        {team: {"brand": 4.0, "perf": 4.0, "research": 2.0} for team in teams},
        {
            team: {
                "brand": 4.0,
                "perf": 4.0,
                "research": 2.0,
                "emphasis": BASELINE["emphasis"],
                "positioning": 45.0,
            }
            for team in teams
        },
    ]

    history = run_simulation_with_faculty(teams, submissions, config)

    assert len(history) == 3
    assert history[0]["open_levers"] == frozenset()
    assert history[1]["open_levers"] == frozenset({"allocation"})
    assert history[2]["open_levers"] == frozenset({"allocation", "emphasis", "positioning"})
    assert set(history[2]["effective_decisions"]) == set(teams)


@pytest.mark.parametrize(
    ("team_names", "match"),
    [
        ("A", "team_names"),
        ([], "nonempty"),
        ((1,), "non-empty strings"),
        (("",), "non-empty strings"),
        (("A", "A"), "duplicate"),
        (("BigRed",), "backdrop"),
    ],
)
def test_invalid_team_names_raise_value_error(team_names, match):
    with pytest.raises(ValueError, match=match):
        build_effective_decisions(team_names, _empty_submissions(8), GOLDEN_CONFIG)


def test_submission_shape_validation_errors():
    with pytest.raises(ValueError, match="length"):
        build_effective_decisions(TEAMS, _empty_submissions(7), GOLDEN_CONFIG)
    with pytest.raises(ValueError, match="non-string sequence"):
        build_effective_decisions(TEAMS, "not a sequence", GOLDEN_CONFIG)
    with pytest.raises(ValueError, match="round 1 submissions"):
        build_effective_decisions(TEAMS, [1] + _empty_submissions(7), GOLDEN_CONFIG)
    with pytest.raises(ValueError, match="unknown team"):
        build_effective_decisions(TEAMS, [{"Z": {}}] + _empty_submissions(7), GOLDEN_CONFIG)
    with pytest.raises(ValueError, match="must be a mapping"):
        build_effective_decisions(TEAMS, [{"A": []}] + _empty_submissions(7), GOLDEN_CONFIG)
    with pytest.raises(ValueError, match="unknown decision field"):
        build_effective_decisions(TEAMS, [{"A": {"media": 1}}] + _empty_submissions(7), GOLDEN_CONFIG)


@pytest.mark.parametrize(
    ("config", "match"),
    [
        (_config(num_rounds=1.5), "num_rounds"),
        (_config(num_rounds=0), "num_rounds"),
        (_config(takeover_round=1.5), "takeover_round"),
        (_config(takeover_round=0), "takeover_round"),
        (_config(takeover_round=10), "takeover_round"),
        (_config(lever_open_round=[]), "lever_open_round"),
        (
            _config(lever_open_round={"allocation": 4, "emphasis": 4, "positioning": 5, "promotion": 6}),
            "missing required lever",
        ),
        (
            _config(
                lever_open_round={
                    "allocation": 4,
                    "emphasis": 4,
                    "positioning": 5,
                    "promotion": 6,
                    "price": 8,
                    "coupon": 8,
                }
            ),
            "unknown lever",
        ),
        (
            _config(
                lever_open_round={
                    "allocation": 4,
                    "emphasis": 4,
                    "positioning": 5,
                    "promotion": 6,
                    "price": 8.0,
                }
            ),
            "open round",
        ),
        (
            _config(
                lever_open_round={
                    "allocation": 4,
                    "emphasis": 4,
                    "positioning": 5,
                    "promotion": 6,
                    "price": 0,
                }
            ),
            "at least 1",
        ),
        (_config(baseline_decision=[]), "baseline_decision"),
        (
            _config(
                baseline_decision={
                    "brand": 5.0,
                    "perf": 2.0,
                    "research": 3.0,
                    "positioning": 35.0,
                    "emphasis": BASELINE["emphasis"],
                    "price": 1.5,
                }
            ),
            "missing required field",
        ),
        (
            _config(baseline_decision={**BASELINE, "coupon": 0.0}),
            "unknown field",
        ),
        (
            _config(baseline_decision={**BASELINE, "brand": 6.0}),
            "spending",
        ),
        (
            _config(baseline_decision={**BASELINE, "emphasis": (0.2, 0.2)}),
            "emphasis",
        ),
        (
            _config(baseline_decision={**BASELINE, "price": 9.0}),
            "price",
        ),
        (
            _config(baseline_decision={**BASELINE, "promotion": 9.0}),
            "promotion",
        ),
    ],
)
def test_invalid_faculty_config_raises_value_error(config, match):
    with pytest.raises(ValueError, match=match):
        build_effective_decisions(TEAMS, _empty_submissions(8), config)


def test_invalid_effective_open_decision_raises_value_error():
    submissions = _golden_submissions()
    submissions[7]["A"]["price"] = 9.0
    with pytest.raises(ValueError, match="price"):
        build_effective_decisions(TEAMS, submissions, GOLDEN_CONFIG)

    submissions = _golden_submissions()
    submissions[3]["A"] = {"brand": 3, "perf": 6, "emphasis": EMPHASIS["A"]}
    with pytest.raises(ValueError, match="allocation"):
        build_effective_decisions(TEAMS, submissions, GOLDEN_CONFIG)


def test_open_lever_round_input_validation():
    with pytest.raises(ValueError, match="integer"):
        open_levers_for_round(1.5, GOLDEN_CONFIG)
    with pytest.raises(ValueError, match="at least 1"):
        open_levers_for_round(0, GOLDEN_CONFIG)
