from __future__ import annotations

"""Assumptions: Chunk 14 is a measuring harness, not a tuning layer, so these
tests treat the current locked engine, equity, crisis, and promotion defaults as
the baseline coefficient snapshot; the only tuning-responsiveness assertion uses
the inspected engine-level promo_cost_rate field because the active promotion
cost is stored on SimulationParams rather than PromotionParams."""

import ast
from dataclasses import replace
from math import inf, nan
from types import MappingProxyType

import pytest

import calibration
from crisis import CrisisParams
from calibration import (
    ARCHETYPES,
    ARCHETYPE_ORDER,
    CRISIS_INPUTS,
    run_battery,
    telemetry,
)
from engine import DEFAULT_PARAMS
from equity import DEFAULT_EQUITY_PARAMS
from promotion import DEFAULT_PROMOTION_PARAMS, PromotionParams


TELEMETRY_KEYS = {
    "final_overall",
    "final_overall_spread",
    "final_share_pct",
    "final_contribution",
    "final_equity",
    "heritage_trap_present",
    "promo_punished",
    "stuck_in_middle_present",
    "promo_equity_erosion",
    "mean_crisis_share_drop",
}


GOLDEN_FINAL_OVERALL = {
    "HERITAGE": 58.21,
    "PIVOT": 93.81,
    "PROMO": 2.26,
    "BALANCED": 74.02,
    "PERF": 52.94,
}
GOLDEN_FINAL_SHARE_PCT = {
    "HERITAGE": 10.254,
    "PIVOT": 11.449,
    "PROMO": 9.051,
    "BALANCED": 11.023,
    "PERF": 10.450,
}
GOLDEN_FINAL_CONTRIBUTION = {
    "HERITAGE": 0.658,
    "PIVOT": 2.165,
    "PROMO": -6.550,
    "BALANCED": 1.457,
    "PERF": 0.379,
}
GOLDEN_FINAL_EQUITY = {
    "HERITAGE": 86.33,
    "PIVOT": 80.28,
    "PROMO": 61.93,
    "BALANCED": 81.83,
    "PERF": 62.47,
}


def test_archetypes_and_crisis_inputs_are_immutable_and_locked():
    assert isinstance(ARCHETYPES, MappingProxyType)
    assert isinstance(CRISIS_INPUTS, MappingProxyType)
    assert tuple(ARCHETYPES) == ARCHETYPE_ORDER
    assert tuple(CRISIS_INPUTS) == ARCHETYPE_ORDER

    assert ARCHETYPES["HERITAGE"] == {
        "brand": 6.0,
        "perf": 2.0,
        "research": 2.0,
        "positioning": 18.0,
        "emphasis": (0.45, 0.15, 0.05, 0.05, 0.30),
        "price": 1.50,
        "promotion": 0.0,
    }
    assert ARCHETYPES["PIVOT"] == {
        "brand": 4.0,
        "perf": 3.0,
        "research": 3.0,
        "positioning": 82.0,
        "emphasis": (0.05, 0.15, 0.40, 0.30, 0.10),
        "price": 1.55,
        "promotion": 0.1,
    }
    assert ARCHETYPES["PROMO"] == {
        "brand": 3.0,
        "perf": 5.0,
        "research": 2.0,
        "positioning": 45.0,
        "emphasis": (0.25, 0.10, 0.10, 0.15, 0.40),
        "price": 1.20,
        "promotion": 0.9,
    }
    assert ARCHETYPES["BALANCED"] == {
        "brand": 4.0,
        "perf": 3.0,
        "research": 3.0,
        "positioning": 50.0,
        "emphasis": (0.20, 0.20, 0.20, 0.20, 0.20),
        "price": 1.50,
        "promotion": 0.0,
    }
    assert ARCHETYPES["PERF"] == {
        "brand": 2.0,
        "perf": 6.0,
        "research": 2.0,
        "positioning": 70.0,
        "emphasis": (0.10, 0.15, 0.30, 0.35, 0.10),
        "price": 1.55,
        "promotion": 0.3,
    }
    assert CRISIS_INPUTS["HERITAGE"] == {"exposure": 0.8, "preempt": 0.1, "response": 0.3}
    assert CRISIS_INPUTS["PIVOT"] == {"exposure": 0.4, "preempt": 0.6, "response": 0.7}
    assert CRISIS_INPUTS["PROMO"] == {"exposure": 0.6, "preempt": 0.2, "response": 0.3}
    assert CRISIS_INPUTS["BALANCED"] == {"exposure": 0.5, "preempt": 0.4, "response": 0.5}
    assert CRISIS_INPUTS["PERF"] == {"exposure": 0.6, "preempt": 0.3, "response": 0.4}

    with pytest.raises(TypeError):
        ARCHETYPES["HERITAGE"]["brand"] = 1.0
    with pytest.raises(TypeError):
        CRISIS_INPUTS["PIVOT"]["exposure"] = 0.0


def test_run_battery_public_shape_and_crisis_round():
    history = run_battery()

    assert len(history) == 14
    for expected_round, record in enumerate(history, start=1):
        assert record["round"] == expected_round
        assert set(ARCHETYPE_ORDER).issubset(record["firms"])
        assert set(ARCHETYPE_ORDER).issubset(record["scorecard"]["firms"])
        assert set(record["equity"]) == set(ARCHETYPE_ORDER)
        if expected_round == 9:
            assert set(record["damage"]) == set(ARCHETYPE_ORDER)
        else:
            assert record["damage"] == {}


def test_calibration_architecture_boundary():
    source = calibration.__loader__.get_source(calibration.__name__)
    tree = ast.parse(source)

    forbidden_references = (
        "customer_agents",
        "advisors",
        "defense",
        "report",
        "live_voice",
        "advisor_voice",
        "defense_grader",
        "openai",
        "anthropic",
        "requests",
        "httpx",
        "urllib.request",
        "socket",
        "random",
        "pathlib",
        "csv",
    )
    forbidden_operations = (
        "open(",
        "print(",
        ".write(",
        ".writelines(",
        "write_text(",
        "write_bytes(",
        "export",
    )
    forbidden_function_terms = (
        "tune",
        "tuning",
        "search",
        "optimizer",
        "optimize",
        "coefficient",
        "export",
    )
    allowed_imports = {
        "__future__",
        "collections.abc",
        "math",
        "statistics",
        "types",
        "typing",
        "crisis",
        "engine",
        "equity",
        "game",
        "promotion",
        "simulation",
    }

    for forbidden in forbidden_references:
        assert forbidden not in source
    for forbidden in forbidden_operations:
        assert forbidden not in source

    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            lower_name = node.name.lower()
            assert not any(term in lower_name for term in forbidden_function_terms)
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name in allowed_imports
        if isinstance(node, ast.ImportFrom):
            assert node.module in allowed_imports
            if node.module == "game":
                assert [alias.name for alias in node.names] == ["run_game"]


def test_run_battery_delegates_once_with_fresh_decisions_and_exact_parameters(monkeypatch):
    supplied_params = replace(DEFAULT_PARAMS, promo_cost_rate=0.4)
    supplied_equity_params = replace(DEFAULT_EQUITY_PARAMS, b_promo=5.0)
    supplied_promotion_params = PromotionParams(dc_decay=0.2, dc_gain=0.4, dc_start=0.1)
    calls = []

    def spy_run_game(
        decisions_by_round,
        params,
        growth_rates,
        equity_params,
        crisis_params,
        promotion_params,
        crisis_inputs,
    ):
        calls.append({
            "decisions_by_round": decisions_by_round,
            "params": params,
            "growth_rates": growth_rates,
            "equity_params": equity_params,
            "crisis_params": crisis_params,
            "promotion_params": promotion_params,
            "crisis_inputs": crisis_inputs,
        })
        return [{"round": 1, "damage": {}}]

    monkeypatch.setattr(calibration, "run_game", spy_run_game)

    assert run_battery(
        num_rounds=3,
        crisis_round=2,
        params=supplied_params,
        equity_params=supplied_equity_params,
        promotion_params=supplied_promotion_params,
    ) == [{"round": 1, "damage": {}}]

    assert len(calls) == 1
    call = calls[0]
    assert call["params"] is supplied_params
    assert call["growth_rates"] is calibration.DEFAULT_GROWTH_RATES
    assert call["equity_params"] is supplied_equity_params
    assert call["promotion_params"] is supplied_promotion_params
    assert call["crisis_inputs"] is CRISIS_INPUTS
    assert call["crisis_params"] == CrisisParams(
        crisis_round=2,
        damage_base=25.0,
        trust_fraction=0.5,
        prod_fraction=0.3,
        eq_fraction=0.2,
        lower_bound=0.0,
        upper_bound=100.0,
    )

    decisions = call["decisions_by_round"]
    assert len(decisions) == 3
    for round_decisions in decisions:
        assert tuple(round_decisions) == ARCHETYPE_ORDER
        assert round_decisions == ARCHETYPES
    assert decisions[0] is not decisions[1]
    assert decisions[0]["HERITAGE"] is not decisions[1]["HERITAGE"]
    decisions[0]["HERITAGE"]["brand"] = 1.0
    assert decisions[1]["HERITAGE"]["brand"] == 6.0
    assert ARCHETYPES["HERITAGE"]["brand"] == 6.0


def test_default_telemetry_matches_locked_golden_snapshot():
    report = telemetry(run_battery())

    assert set(report) == TELEMETRY_KEYS
    assert report["final_overall"] == pytest.approx(GOLDEN_FINAL_OVERALL, abs=0.1)
    assert report["final_overall_spread"] == pytest.approx(91.55, abs=0.1)
    assert report["final_share_pct"] == pytest.approx(GOLDEN_FINAL_SHARE_PCT, abs=0.1)
    assert report["final_contribution"] == pytest.approx(GOLDEN_FINAL_CONTRIBUTION, abs=0.1)
    assert report["final_equity"] == pytest.approx(GOLDEN_FINAL_EQUITY, abs=0.1)
    assert report["mean_crisis_share_drop"] == pytest.approx(0.0476, abs=0.005)
    assert report["heritage_trap_present"] is True
    assert report["promo_punished"] is True
    assert report["stuck_in_middle_present"] is True
    assert report["promo_equity_erosion"] is True


def test_telemetry_diagnostics_are_derived_from_history():
    history = run_battery()
    report = telemetry(history)
    final_overall = report["final_overall"]
    final_contribution = report["final_contribution"]
    final_equity = report["final_equity"]

    assert report["final_overall_spread"] == pytest.approx(
        max(final_overall.values()) - min(final_overall.values())
    )
    assert report["heritage_trap_present"] == (
        final_overall["PIVOT"] > final_overall["HERITAGE"]
    )
    assert report["promo_punished"] == (
        final_overall["PROMO"] < final_overall["PIVOT"]
        and final_contribution["PROMO"] < final_contribution["PIVOT"]
    )
    assert report["stuck_in_middle_present"] == (
        final_overall["BALANCED"] < max(final_overall["HERITAGE"], final_overall["PIVOT"])
    )
    assert report["promo_equity_erosion"] == (
        final_equity["PROMO"] < final_equity["PIVOT"]
    )
    assert report["mean_crisis_share_drop"] == pytest.approx(
        sum(
            (
                history[7]["firms"][team]["total_market_share"]
                - history[8]["firms"][team]["total_market_share"]
            )
            * 100.0
            for team in ARCHETYPE_ORDER
        )
        / len(ARCHETYPE_ORDER)
    )


def test_parameter_passthrough_changes_battery_without_mutating_defaults():
    default_params_before = repr(DEFAULT_PARAMS)
    default_promotion_params_before = repr(DEFAULT_PROMOTION_PARAMS)
    default_report = telemetry(run_battery())
    high_cost_params = replace(DEFAULT_PARAMS, promo_cost_rate=0.9)

    high_cost_report = telemetry(run_battery(params=high_cost_params))
    rerun_report = telemetry(run_battery())

    assert high_cost_report != default_report
    assert high_cost_report["final_contribution"]["PROMO"] < default_report["final_contribution"]["PROMO"]
    assert (
        high_cost_report["final_contribution"]["PROMO"] != default_report["final_contribution"]["PROMO"]
        or high_cost_report["final_overall"]["PROMO"] != default_report["final_overall"]["PROMO"]
    )
    assert rerun_report == default_report
    assert repr(DEFAULT_PARAMS) == default_params_before
    assert repr(DEFAULT_PROMOTION_PARAMS) == default_promotion_params_before


def test_determinism_and_input_purity():
    archetypes_before = {
        team: dict(decision)
        for team, decision in ARCHETYPES.items()
    }
    crisis_inputs_before = {
        team: dict(inputs)
        for team, inputs in CRISIS_INPUTS.items()
    }

    first_history = run_battery()
    second_history = run_battery()
    first_history_before = repr(first_history)
    first_report = telemetry(first_history)
    second_report = telemetry(second_history)
    first_report["final_overall"]["HERITAGE"] = -1.0

    assert first_history == second_history
    assert telemetry(first_history) == second_report
    assert first_report != telemetry(first_history)
    assert repr(first_history) == first_history_before
    assert {
        team: dict(decision)
        for team, decision in ARCHETYPES.items()
    } == archetypes_before
    assert {
        team: dict(inputs)
        for team, inputs in CRISIS_INPUTS.items()
    } == crisis_inputs_before


def test_run_battery_rejects_invalid_round_arguments():
    for bad_rounds in (0, -1, 1.5, True):
        with pytest.raises(ValueError, match="num_rounds"):
            run_battery(num_rounds=bad_rounds)
    for bad_crisis_round in (0, -1, 1.5, True):
        with pytest.raises(ValueError, match="crisis_round"):
            run_battery(crisis_round=bad_crisis_round)


def test_telemetry_validation_errors_are_explicit():
    base = run_battery()

    with pytest.raises(ValueError, match="history"):
        telemetry([])
    with pytest.raises(ValueError, match="history"):
        telemetry(123)
    with pytest.raises(ValueError, match="history record 1"):
        telemetry([[]])

    cases = []

    missing_round = _copy_history(base)
    del missing_round[-1]["round"]
    cases.append((missing_round, "missing round"))

    bool_round = _copy_history(base)
    bool_round[-1]["round"] = True
    cases.append((bool_round, "round must be an integer"))

    duplicate_round = _copy_history(base)
    duplicate_round[-1]["round"] = duplicate_round[-2]["round"]
    cases.append((duplicate_round, "duplicate history round"))

    missing_firms = _copy_history(base)
    del missing_firms[-1]["firms"]
    cases.append((missing_firms, "missing firms"))

    firms_not_mapping = _copy_history(base)
    firms_not_mapping[-1]["firms"] = []
    cases.append((firms_not_mapping, "firms must be a mapping"))

    missing_scorecard = _copy_history(base)
    del missing_scorecard[-1]["scorecard"]
    cases.append((missing_scorecard, "missing scorecard"))

    scorecard_not_mapping = _copy_history(base)
    scorecard_not_mapping[-1]["scorecard"] = []
    cases.append((scorecard_not_mapping, "scorecard must be a mapping"))

    missing_scorecard_firms = _copy_history(base)
    del missing_scorecard_firms[-1]["scorecard"]["firms"]
    cases.append((missing_scorecard_firms, "missing firms"))

    missing_archetype = _copy_history(base)
    del missing_archetype[-1]["scorecard"]["firms"]["PERF"]
    cases.append((missing_archetype, "missing PERF"))

    malformed_scorecard_entry = _copy_history(base)
    malformed_scorecard_entry[-1]["scorecard"]["firms"]["PERF"] = []
    cases.append((malformed_scorecard_entry, "PERF scorecard must be a mapping"))

    malformed_firm_result = _copy_history(base)
    malformed_firm_result[-1]["firms"]["PERF"] = []
    cases.append((malformed_firm_result, "PERF firm record must be a mapping"))

    missing_equity = _copy_history(base)
    del missing_equity[-1]["equity"]["PERF"]
    cases.append((missing_equity, "missing PERF"))

    contradictory_eq = _copy_history(base)
    contradictory_eq[-1]["attribute_state"]["PERF"]["EQ"] += 1.0
    cases.append((contradictory_eq, "attribute_state.EQ must match final equity"))

    multiple_damage = _copy_history(base)
    multiple_damage[0]["damage"] = {"HERITAGE": 1.0}
    cases.append((multiple_damage, "at most one nonempty damage round"))

    malformed_damage = _copy_history(base)
    malformed_damage[0]["damage"] = []
    cases.append((malformed_damage, "damage must be a mapping"))

    bad_damage_value = _copy_history(base)
    bad_damage_value[0]["damage"] = {"HERITAGE": nan}
    cases.append((bad_damage_value, "finite number"))

    malformed_crisis_share = _copy_history(base)
    malformed_crisis_share[8]["firms"]["HERITAGE"]["total_market_share"] = "bad"
    cases.append((malformed_crisis_share, "crisis_share must be a finite number"))

    for history, message in cases:
        with pytest.raises(ValueError, match=message):
            telemetry(history)


def test_telemetry_validates_structural_containers_on_intermediate_rounds():
    base = run_battery()
    cases = []

    missing_firms = _copy_history(base)
    del missing_firms[1]["firms"]
    cases.append((missing_firms, "history record 2 is missing firms"))

    firms_not_mapping = _copy_history(base)
    firms_not_mapping[1]["firms"] = []
    cases.append((firms_not_mapping, "history record 2 firms must be a mapping"))

    missing_scorecard = _copy_history(base)
    del missing_scorecard[1]["scorecard"]
    cases.append((missing_scorecard, "history record 2 is missing scorecard"))

    scorecard_not_mapping = _copy_history(base)
    scorecard_not_mapping[1]["scorecard"] = []
    cases.append((scorecard_not_mapping, "history record 2 scorecard must be a mapping"))

    missing_scorecard_firms = _copy_history(base)
    del missing_scorecard_firms[1]["scorecard"]["firms"]
    cases.append((missing_scorecard_firms, "history record 2 scorecard is missing firms"))

    scorecard_firms_not_mapping = _copy_history(base)
    scorecard_firms_not_mapping[1]["scorecard"]["firms"] = []
    cases.append((scorecard_firms_not_mapping, "history record 2 scorecard firms must be a mapping"))

    missing_damage = _copy_history(base)
    del missing_damage[1]["damage"]
    cases.append((missing_damage, "history record 2 is missing damage"))

    damage_not_mapping = _copy_history(base)
    damage_not_mapping[1]["damage"] = []
    cases.append((damage_not_mapping, "history record 2 damage must be a mapping"))

    for history, message in cases:
        with pytest.raises(ValueError, match=message):
            telemetry(history)


def test_telemetry_rejects_missing_and_malformed_numeric_metrics():
    base = run_battery()
    mutations = [
        (("scorecard", "firms", "HERITAGE", "overall"), None, "missing overall"),
        (("scorecard", "firms", "HERITAGE", "overall"), "bad", "overall must be a finite number"),
        (("scorecard", "firms", "HERITAGE", "overall"), nan, "overall must be a finite number"),
        (("scorecard", "firms", "HERITAGE", "overall"), inf, "overall must be a finite number"),
        (("firms", "HERITAGE", "total_market_share"), None, "missing total_market_share"),
        (("firms", "HERITAGE", "total_market_share"), "bad", "total_market_share must be a finite number"),
        (("firms", "HERITAGE", "total_market_share"), nan, "total_market_share must be a finite number"),
        (("firms", "HERITAGE", "total_market_share"), inf, "total_market_share must be a finite number"),
        (("firms", "HERITAGE", "contribution"), None, "missing contribution"),
        (("firms", "HERITAGE", "contribution"), "bad", "contribution must be a finite number"),
        (("firms", "HERITAGE", "contribution"), nan, "contribution must be a finite number"),
        (("firms", "HERITAGE", "contribution"), inf, "contribution must be a finite number"),
        (("equity", "HERITAGE"), "bad", "equity must be a finite number"),
        (("equity", "HERITAGE"), nan, "equity must be a finite number"),
        (("equity", "HERITAGE"), inf, "equity must be a finite number"),
    ]

    for path, value, message in mutations:
        history = _copy_history(base)
        if value is None:
            _delete_nested(history[-1], path)
        else:
            _set_nested(history[-1], path, value)
        with pytest.raises(ValueError, match=message):
            telemetry(history)


def test_telemetry_requires_complete_final_attribute_state_when_present():
    base = run_battery()
    cases = []

    missing_team_state = _copy_history(base)
    del missing_team_state[-1]["attribute_state"]["PERF"]
    cases.append((missing_team_state, "final attribute_state is missing PERF"))

    team_state_not_mapping = _copy_history(base)
    team_state_not_mapping[-1]["attribute_state"]["PERF"] = []
    cases.append((team_state_not_mapping, "final attribute_state PERF must be a mapping"))

    missing_eq = _copy_history(base)
    del missing_eq[-1]["attribute_state"]["PERF"]["EQ"]
    cases.append((missing_eq, "PERF attribute_state is missing EQ"))

    nonnumeric_eq = _copy_history(base)
    nonnumeric_eq[-1]["attribute_state"]["PERF"]["EQ"] = "bad"
    cases.append((nonnumeric_eq, "PERF.attribute_state.EQ must be a finite number"))

    for history, message in cases:
        with pytest.raises(ValueError, match=message):
            telemetry(history)


def test_open_ended_round_behavior_and_custom_overrides():
    one_round = run_battery(num_rounds=1, crisis_round=1)
    assert len(one_round) == 1
    assert one_round[0]["damage"]
    assert telemetry(one_round)["mean_crisis_share_drop"] == 0.0

    no_crisis = run_battery(num_rounds=3, crisis_round=99)
    assert all(record["damage"] == {} for record in no_crisis)
    assert telemetry(no_crisis)["mean_crisis_share_drop"] == 0.0

    five_rounds = run_battery(num_rounds=5, crisis_round=3)
    assert len(five_rounds) == 5
    assert five_rounds[2]["damage"]

    custom_params = replace(DEFAULT_PARAMS, promo_cost_rate=0.5)
    custom_equity = replace(DEFAULT_EQUITY_PARAMS, b_promo=5.0)
    custom_promotion = PromotionParams(dc_decay=0.2, dc_gain=0.4, dc_start=0.1)
    custom_params_before = repr(custom_params)
    custom_equity_before = repr(custom_equity)
    custom_promotion_before = repr(custom_promotion)

    custom_report = telemetry(run_battery(
        num_rounds=4,
        crisis_round=2,
        params=custom_params,
        equity_params=custom_equity,
        promotion_params=custom_promotion,
    ))

    assert set(custom_report) == TELEMETRY_KEYS
    assert repr(custom_params) == custom_params_before
    assert repr(custom_equity) == custom_equity_before
    assert repr(custom_promotion) == custom_promotion_before


def _copy_history(history):
    copied = []
    for record in history:
        copied.append({
            key: _copy_value(value)
            for key, value in record.items()
        })
    return copied


def _copy_value(value):
    if isinstance(value, dict):
        return {
            key: _copy_value(inner_value)
            for key, inner_value in value.items()
        }
    if isinstance(value, tuple):
        return tuple(_copy_value(item) for item in value)
    if isinstance(value, list):
        return [_copy_value(item) for item in value]
    return value


def _set_nested(mapping, path, value):
    current = mapping
    for key in path[:-1]:
        current = current[key]
    current[path[-1]] = value


def _delete_nested(mapping, path):
    current = mapping
    for key in path[:-1]:
        current = current[key]
    del current[path[-1]]
