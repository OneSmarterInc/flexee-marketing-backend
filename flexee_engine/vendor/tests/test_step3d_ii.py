"""Chunk 3d-ii — Promotion. Golden test, backward-compatibility, and invariants.

Assumptions note: promotion depth is a per-team, per-round decision in
[promotion_min, promotion_max] = [0, 1], default 0. It (1) lifts perception this
round by promotion_lift * depth * deal_sensitivity[segment]; (2) costs margin via
contribution = volume * (price - variable_cost - promo_cost_rate * depth) - budget;
(3) debits carried brand equity by b_promo * depth inside the current round's
equity recurrence, applied BEFORE market resolution; and (4) builds a carried
per-team deal-conditioning stock DC that penalises perception by
deal_conditioning_penalty * DC[t] * deal_sensitivity[segment], where the round-t
perception uses the DC entering round t and DC advances as
DC[t+1] = DC[t] * (1 - dc_decay) + dc_gain * depth[t]. Prices are held at the
reference (1.50) throughout the golden so the numbers isolate promotion. At depth 0
and DC 0 every added term is exactly 0, so all prior chunks are byte-identical. The
DC penalty is scaled by the same per-segment deal-sensitivity vector as the lift,
so it lands on the deal/value segments (MAIN, GENZ, LEG) and barely touches the
premium FUN segment. The promotion runner is parallel to the equity runner (it
carries EQ and DC only, never TRUST/PROD or crisis state).
"""

from __future__ import annotations

import copy
import math

import pytest
from dataclasses import replace

import engine
from engine import DEFAULT_PARAMS, compute_round
from equity import (
    DEFAULT_EQUITY_PARAMS,
    EquityParams,
    next_equity,
    run_simulation_with_equity,
)
from promotion import (
    DEFAULT_PROMOTION_PARAMS,
    PromotionParams,
    run_simulation_with_promotion,
)


SEGMENTS = ("LEG", "FIT", "FUN", "GENZ", "MAIN")
DEAL_SENSITIVITY = {"LEG": 0.70, "FIT": 0.20, "FUN": 0.05, "GENZ": 0.80, "MAIN": 1.00}

BASE_DECISIONS = {
    "A": {"brand": 3, "perf": 6, "research": 1, "positioning": 20,
          "emphasis": (0.45, 0.15, 0.05, 0.05, 0.30)},
    "B": {"brand": 6, "perf": 2, "research": 2, "positioning": 82,
          "emphasis": (0.10, 0.20, 0.40, 0.25, 0.05)},
    "C": {"brand": 4, "perf": 4, "research": 2, "positioning": 52,
          "emphasis": (0.25, 0.20, 0.25, 0.15, 0.15)},
    "D": {"brand": 5, "perf": 3, "research": 2, "positioning": 72,
          "emphasis": (0.15, 0.10, 0.30, 0.40, 0.05)},
}

DEPTHS = {
    "A": [0.8, 0.8, 0.8, 0.8],
    "B": [0.0, 0.0, 0.0, 0.0],
    "C": [0.4, 0.4, 0.4, 0.4],
    "D": [0.2, 0.4, 0.6, 0.8],
}

GOLDEN_DC = {
    "A": [0.000, 0.400, 0.680, 0.876],
    "B": [0.000, 0.000, 0.000, 0.000],
    "C": [0.000, 0.200, 0.340, 0.438],
    "D": [0.000, 0.100, 0.270, 0.489],
}

GOLDEN_EQ = {
    "A": [61.8211, 61.6869, 61.5862, 61.5107],
    "B": [68.2150, 72.8762, 76.3721, 78.9940],
    "C": [65.4570, 68.0497, 69.9942, 71.4526],
    "D": [66.9080, 69.7889, 71.1497, 71.3702],
}

GOLDEN_PERCEPTION = {
    1: {
        "A": [82.285, 52.812, 36.293, 52.311, 73.832],
        "B": [49.354, 56.121, 66.982, 69.711, 59.521],
        "C": [65.154, 66.086, 52.479, 64.233, 75.946],
        "D": [54.897, 58.811, 61.366, 74.775, 64.991],
    },
    4: {
        "A": [78.528, 51.730, 35.984, 48.091, 68.545],
        "B": [52.049, 57.199, 68.599, 70.250, 60.599],
        "C": [64.814, 66.160, 53.246, 62.430, 73.917],
        "D": [57.319, 59.630, 62.129, 76.491, 67.303],
    },
}

# Total market share % — R1 as originally specified; R4 recomputed from the locked
# engine growth vector (LEG -0.01, FIT 0.003, FUN 0.025, GENZ 0.04, MAIN 0.005).
GOLDEN_SHARE = {
    1: {"A": 12.8171, "B": 11.3189, "C": 12.4875, "D": 11.8614},
    4: {"A": 11.4099, "B": 12.0080, "C": 12.2564, "D": 12.5826},
}

GOLDEN_CONTRIBUTION = {
    1: {"A": -1.5407, "B": 0.1870, "C": -0.2598, "D": -0.0364},
    4: {"A": -2.2497, "B": 1.1225, "C": -0.1610, "D": -1.4531},
}


def _rounds_with_depths(n_rounds=4):
    """Independent deep copies of the base decisions, with promotion depths added."""
    rounds = []
    for t in range(n_rounds):
        rounds.append({
            team: {**copy.deepcopy(BASE_DECISIONS[team]), "promotion": DEPTHS[team][t]}
            for team in BASE_DECISIONS
        })
    return rounds


def _rounds_no_promotion(n_rounds=4, explicit_zero=False):
    rounds = []
    for _ in range(n_rounds):
        row = {}
        for team in BASE_DECISIONS:
            decision = copy.deepcopy(BASE_DECISIONS[team])
            if explicit_zero:
                decision["promotion"] = 0.0
            row[team] = decision
        rounds.append(row)
    return rounds


# --------------------------------------------------------------------------- #
# Part 4 — Golden test
# --------------------------------------------------------------------------- #

def test_golden_deal_conditioning_entering_each_round():
    history = run_simulation_with_promotion(_rounds_with_depths())
    for round_index, record in enumerate(history):
        for team in BASE_DECISIONS:
            assert record["deal_conditioning"][team] == pytest.approx(
                GOLDEN_DC[team][round_index], abs=1e-3
            )


def test_golden_equity_stock():
    history = run_simulation_with_promotion(_rounds_with_depths())
    for round_index, record in enumerate(history):
        for team in BASE_DECISIONS:
            assert record["equity"][team] == pytest.approx(
                GOLDEN_EQ[team][round_index], abs=1e-2
            )


def test_golden_cross_field_identities():
    history = run_simulation_with_promotion(_rounds_with_depths())
    for record in history:
        for team in BASE_DECISIONS:
            firm = record["firms"][team]
            assert firm["eq"] == record["equity"][team]
            assert firm["attributes"]["EQ"] == record["equity"][team]
            assert firm["promotion"] == record["promotion_depth"][team]
            assert firm["deal_conditioning"] == record["deal_conditioning"][team]


def test_golden_perception_round_1_and_4():
    history = run_simulation_with_promotion(_rounds_with_depths())
    for round_number in (1, 4):
        record = history[round_number - 1]
        for team in BASE_DECISIONS:
            for seg_index, segment in enumerate(SEGMENTS):
                assert record["firms"][team]["perception"][segment] == pytest.approx(
                    GOLDEN_PERCEPTION[round_number][team][seg_index], abs=1e-2
                )


def test_golden_market_share_round_1_and_4():
    history = run_simulation_with_promotion(_rounds_with_depths())
    for round_number in (1, 4):
        record = history[round_number - 1]
        for team in BASE_DECISIONS:
            assert record["firms"][team]["total_market_share"] * 100.0 == pytest.approx(
                GOLDEN_SHARE[round_number][team], abs=1e-2
            )


def test_golden_contribution_round_1_and_4():
    history = run_simulation_with_promotion(_rounds_with_depths())
    for round_number in (1, 4):
        record = history[round_number - 1]
        for team in BASE_DECISIONS:
            assert record["firms"][team]["contribution"] == pytest.approx(
                GOLDEN_CONTRIBUTION[round_number][team], abs=1e-2
            )


# --------------------------------------------------------------------------- #
# Part 5 — Backward-compatibility invariants
# --------------------------------------------------------------------------- #

def test_full_no_promotion_matches_equity_runner():
    for explicit_zero in (False, True):
        promo_history = run_simulation_with_promotion(_rounds_no_promotion(explicit_zero=explicit_zero))
        equity_history = run_simulation_with_equity(_rounds_no_promotion())
        assert len(promo_history) == len(equity_history)
        for promo_record, equity_record in zip(promo_history, equity_history):
            assert promo_record["segment_sizes"] == equity_record["segment_sizes"]
            assert promo_record["equity"] == equity_record["equity"]
            assert promo_record["scorecard"] == equity_record["scorecard"]
            for team in BASE_DECISIONS:
                pf = promo_record["firms"][team]
                ef = equity_record["firms"][team]
                assert pf["perception"] == ef["perception"]
                assert pf["share"] == ef["share"]
                assert pf["volume"] == ef["volume"]
                assert pf["contribution"] == ef["contribution"]
                assert pf["total_market_share"] == ef["total_market_share"]
            # backdrops match too
            for firm_name, ef in equity_record["firms"].items():
                if ef["firm_type"] == "backdrop":
                    assert promo_record["firms"][firm_name]["perception"] == ef["perception"]
            # extra promotion fields are all zero
            for team in BASE_DECISIONS:
                assert promo_record["deal_conditioning"][team] == 0.0
                assert promo_record["promotion_depth"][team] == 0.0


def test_team_b_depth_zero_matches_chunk3b_eq_but_share_may_differ():
    promo_history = run_simulation_with_promotion(_rounds_with_depths())
    equity_history = run_simulation_with_equity(_rounds_no_promotion())
    share_differs = False
    for promo_record, equity_record in zip(promo_history, equity_history):
        # B's EQ trajectory equals the Chunk 3b (no-promotion) trajectory exactly.
        assert promo_record["equity"]["B"] == equity_record["equity"]["B"]
        # B's own promotion lift and DC penalty are zero.
        assert promo_record["promotion_depth"]["B"] == 0.0
        assert promo_record["deal_conditioning"]["B"] == 0.0
        if promo_record["firms"]["B"]["total_market_share"] != equity_record["firms"]["B"]["total_market_share"]:
            share_differs = True
    # Other teams promote, so B's shares/volumes shift versus the all-zero run.
    assert share_differs


def test_direct_compute_round_api_compatibility():
    decisions = {team: copy.deepcopy(BASE_DECISIONS[team]) for team in BASE_DECISIONS}
    plain = compute_round(decisions, DEFAULT_PARAMS)
    explicit = compute_round(
        {team: {**copy.deepcopy(BASE_DECISIONS[team]), "promotion": 0.0} for team in BASE_DECISIONS},
        DEFAULT_PARAMS,
        team_deal_conditioning={team: 0.0 for team in BASE_DECISIONS},
    )
    assert plain == explicit


def test_direct_next_equity_api_compatibility():
    assert next_equity(62.0, 5.0, 3.0, DEFAULT_EQUITY_PARAMS, DEFAULT_PARAMS.budget) == next_equity(
        62.0, 5.0, 3.0, DEFAULT_EQUITY_PARAMS, DEFAULT_PARAMS.budget, promo_depth=0.0
    )


# --------------------------------------------------------------------------- #
# Part 6 — Structural and pedagogical invariants
# --------------------------------------------------------------------------- #

def test_deal_conditioning_recurrence():
    history = run_simulation_with_promotion(_rounds_with_depths())
    for team in BASE_DECISIONS:
        assert history[0]["deal_conditioning"][team] == DEFAULT_PROMOTION_PARAMS.dc_start
    decay = DEFAULT_PROMOTION_PARAMS.dc_decay
    gain = DEFAULT_PROMOTION_PARAMS.dc_gain
    for team in BASE_DECISIONS:
        for t in range(3):
            entering = history[t]["deal_conditioning"][team]
            depth = history[t]["promotion_depth"][team]
            expected_next = entering * (1.0 - decay) + gain * depth
            assert history[t + 1]["deal_conditioning"][team] == pytest.approx(expected_next, abs=1e-9)


def test_equity_debit_bites_for_a_and_not_for_b():
    history = run_simulation_with_promotion(_rounds_with_depths())
    # A: pure EQ recurrence with the golden depths vs the same brand/perf at depth 0.
    eq_promo = DEFAULT_EQUITY_PARAMS.eq_start
    eq_zero = DEFAULT_EQUITY_PARAMS.eq_start
    for t in range(4):
        eq_promo = next_equity(eq_promo, 3.0, 6.0, DEFAULT_EQUITY_PARAMS, DEFAULT_PARAMS.budget,
                               promo_depth=DEPTHS["A"][t])
        eq_zero = next_equity(eq_zero, 3.0, 6.0, DEFAULT_EQUITY_PARAMS, DEFAULT_PARAMS.budget,
                              promo_depth=0.0)
        assert eq_promo < eq_zero
        assert history[t]["equity"]["A"] == pytest.approx(eq_promo, abs=1e-9)
    # B: depth 0 every round, so EQ equals the Chunk 3b trajectory.
    equity_history = run_simulation_with_equity(_rounds_no_promotion())
    for t in range(4):
        assert history[t]["equity"]["B"] == equity_history[t]["equity"]["B"]


def test_promotion_trade_off_shape():
    history = run_simulation_with_promotion(_rounds_with_depths())
    share = {team: [rec["firms"][team]["total_market_share"] for rec in history] for team in BASE_DECISIONS}
    contribution = {team: [rec["firms"][team]["contribution"] for rec in history] for team in BASE_DECISIONS}
    # A leads round-1 share.
    assert share["A"][0] == max(share[team][0] for team in BASE_DECISIONS)
    # A's share strictly declines across the four rounds.
    assert all(share["A"][t] > share["A"][t + 1] for t in range(3))
    # A has the lowest contribution every round.
    for t in range(4):
        assert contribution["A"][t] == min(contribution[team][t] for team in BASE_DECISIONS)
    # B has the highest contribution by round 4 and rising share.
    assert contribution["B"][3] == max(contribution[team][3] for team in BASE_DECISIONS)
    assert all(share["B"][t] < share["B"][t + 1] for t in range(3))


def test_segment_weighting_of_lift():
    decisions = {team: copy.deepcopy(BASE_DECISIONS[team]) for team in BASE_DECISIONS}
    baseline = compute_round(decisions, DEFAULT_PARAMS)
    promoted = compute_round(
        {team: {**copy.deepcopy(BASE_DECISIONS[team]), "promotion": 0.5} for team in BASE_DECISIONS},
        DEFAULT_PARAMS,
    )
    increment = {
        seg: promoted["A"]["perception"][seg] - baseline["A"]["perception"][seg]
        for seg in SEGMENTS
    }
    assert increment["MAIN"] == max(increment.values())
    assert increment["MAIN"] > increment["FUN"]
    ordered = sorted(SEGMENTS, key=lambda s: DEAL_SENSITIVITY[s], reverse=True)
    increments_in_ds_order = [increment[s] for s in ordered]
    assert increments_in_ds_order == sorted(increments_in_ds_order, reverse=True)


def test_segment_weighting_of_dc_penalty():
    decisions = {team: copy.deepcopy(BASE_DECISIONS[team]) for team in BASE_DECISIONS}
    baseline = compute_round(decisions, DEFAULT_PARAMS)
    penalised = compute_round(
        decisions,
        DEFAULT_PARAMS,
        team_deal_conditioning={team: 0.5 for team in BASE_DECISIONS},
    )
    decrement = {
        seg: baseline["A"]["perception"][seg] - penalised["A"]["perception"][seg]
        for seg in SEGMENTS
    }
    assert decrement["MAIN"] == max(decrement.values())
    assert decrement["MAIN"] > decrement["FUN"]
    # Zero entering DC -> no penalty.
    zero_dc = compute_round(decisions, DEFAULT_PARAMS, team_deal_conditioning={team: 0.0 for team in BASE_DECISIONS})
    for seg in SEGMENTS:
        assert zero_dc["A"]["perception"][seg] == baseline["A"]["perception"][seg]


def test_backdrop_stability_under_promotion():
    decisions = {team: copy.deepcopy(BASE_DECISIONS[team]) for team in BASE_DECISIONS}
    baseline = compute_round(decisions, DEFAULT_PARAMS)
    promoted = compute_round(
        {team: {**copy.deepcopy(BASE_DECISIONS[team]), "promotion": 0.9} for team in BASE_DECISIONS},
        DEFAULT_PARAMS,
        team_deal_conditioning={team: 0.5 for team in BASE_DECISIONS},
    )
    for backdrop in ("BigRed", "Monssta", "Pulse"):
        assert promoted[backdrop]["promotion"] == 0.0
        assert promoted[backdrop]["deal_conditioning"] == 0.0
        assert promoted[backdrop]["perception"] == baseline[backdrop]["perception"]
        assert promoted[backdrop]["attributes"] == baseline[backdrop]["attributes"]


def test_reconciliation_shares_and_volumes():
    history = run_simulation_with_promotion(_rounds_with_depths())
    for record in history:
        firms = record["firms"]
        for segment in SEGMENTS:
            share_sum = sum(firm["share"][segment] for firm in firms.values())
            assert share_sum == pytest.approx(1.0, abs=1e-9)
            volume_sum = sum(firm["volume"][segment] for firm in firms.values())
            assert volume_sum == pytest.approx(record["segment_sizes"][segment], abs=1e-9)
        total_volume = sum(firm["total_volume"] for firm in firms.values())
        assert total_volume == pytest.approx(sum(record["segment_sizes"].values()), abs=1e-9)


def test_team_scope_of_carried_state():
    history = run_simulation_with_promotion(_rounds_with_depths())
    teams = set(BASE_DECISIONS)
    for record in history:
        assert set(record["equity"]) == teams
        assert set(record["deal_conditioning"]) == teams
        assert set(record["promotion_depth"]) == teams
        assert set(record["scorecard"]["firms"]) == teams
        for backdrop in ("BigRed", "Monssta", "Pulse"):
            assert backdrop in record["firms"]
            assert backdrop not in record["equity"]
            assert backdrop not in record["deal_conditioning"]
            assert backdrop not in record["promotion_depth"]
            assert backdrop not in record["scorecard"]["firms"]


def test_open_ended_five_team_scenario():
    teams = {**{team: copy.deepcopy(BASE_DECISIONS[team]) for team in BASE_DECISIONS},
             "E": {"brand": 4, "perf": 3, "research": 3, "positioning": 40,
                   "emphasis": (0.20, 0.20, 0.20, 0.20, 0.20)}}
    depths = {"A": 0.5, "B": 0.0, "C": 0.3, "D": 0.7, "E": 0.9}
    rounds = [{team: {**copy.deepcopy(teams[team]), "promotion": depths[team]} for team in teams}
              for _ in range(3)]
    history = run_simulation_with_promotion(rounds)
    assert len(history) == 3
    for record in history:
        assert set(record["equity"]) == set(teams)
        assert record["promotion_depth"]["E"] == 0.9


def test_open_ended_two_round_scenario():
    rounds = _rounds_with_depths(n_rounds=2)
    history = run_simulation_with_promotion(rounds)
    assert len(history) == 2
    assert history[0]["deal_conditioning"]["A"] == 0.0
    assert history[1]["deal_conditioning"]["A"] == pytest.approx(0.4, abs=1e-9)


def test_changed_promotion_in_round_two():
    rounds = _rounds_with_depths()
    changed_depth = 0.9
    rounds[1]["B"]["promotion"] = changed_depth  # B promotes in round 2 only
    history = run_simulation_with_promotion(rounds)
    # Round 2 EQ uses the changed depth.
    expected_eq = next_equity(
        history[0]["equity"]["B"], 6.0, 2.0, DEFAULT_EQUITY_PARAMS, DEFAULT_PARAMS.budget,
        promo_depth=changed_depth,
    )
    assert history[1]["equity"]["B"] == pytest.approx(expected_eq, abs=1e-9)
    # Round 2 immediate lift uses the changed depth (entering DC for B is still 0).
    assert history[1]["promotion_depth"]["B"] == changed_depth
    lift_main = DEFAULT_PARAMS.promotion_lift * changed_depth * DEAL_SENSITIVITY["MAIN"]
    assert lift_main > 0.0
    # Next-round entering DC reflects the changed depth.
    assert history[2]["deal_conditioning"]["B"] == pytest.approx(
        0.0 * (1 - DEFAULT_PROMOTION_PARAMS.dc_decay) + DEFAULT_PROMOTION_PARAMS.dc_gain * changed_depth,
        abs=1e-9,
    )


# --------------------------------------------------------------------------- #
# Part 7 — Validation tests
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("bad", [-0.1, 1.1, "x", float("nan"), float("inf"), float("-inf")])
def test_promotion_decision_validation(bad):
    decisions = {team: copy.deepcopy(BASE_DECISIONS[team]) for team in BASE_DECISIONS}
    decisions["A"]["promotion"] = bad
    snapshot = copy.deepcopy(decisions)
    with pytest.raises(ValueError):
        compute_round(decisions, DEFAULT_PARAMS)
    assert decisions == snapshot  # failed validation does not mutate


@pytest.mark.parametrize("bad_mapping,exc", [
    ([("A", 0.5)], ValueError),           # non-mapping
    ({"Z": 0.5}, ValueError),             # unknown team
    ({"BigRed": 0.5}, ValueError),        # backdrop firm
    ({"A": "x"}, ValueError),             # nonnumeric
    ({"A": float("nan")}, ValueError),    # NaN
    ({"A": float("inf")}, ValueError),    # +inf
    ({"A": float("-inf")}, ValueError),   # -inf
    ({"A": -0.1}, ValueError),            # negative DC
])
def test_deal_conditioning_input_validation(bad_mapping, exc):
    decisions = {team: copy.deepcopy(BASE_DECISIONS[team]) for team in BASE_DECISIONS}
    with pytest.raises(exc):
        compute_round(decisions, DEFAULT_PARAMS, team_deal_conditioning=bad_mapping)


def test_engine_promotion_configuration_validation():
    decisions = {team: copy.deepcopy(BASE_DECISIONS[team]) for team in BASE_DECISIONS}
    bad_params = [
        replace(DEFAULT_PARAMS, promotion_lift=float("inf")),
        replace(DEFAULT_PARAMS, promo_cost_rate=-0.1),
        replace(DEFAULT_PARAMS, promo_cost_rate=float("nan")),
        replace(DEFAULT_PARAMS, deal_conditioning_penalty=float("inf")),
        replace(DEFAULT_PARAMS, promotion_min=1.0, promotion_max=0.0),
        replace(DEFAULT_PARAMS, promotion_min=0.5),  # 0.0 not within bounds
    ]
    for params in bad_params:
        with pytest.raises(ValueError):
            compute_round(decisions, params)
    # non-finite / negative deal sensitivity
    for bad_ds in (float("inf"), -0.1):
        segs = list(DEFAULT_PARAMS.segments)
        segs[0] = replace(segs[0], deal_sensitivity=bad_ds)
        params = replace(DEFAULT_PARAMS, segments=tuple(segs))
        with pytest.raises(ValueError):
            compute_round(decisions, params)


@pytest.mark.parametrize("params_kwargs", [
    {"b_promo": -1.0},
    {"b_promo": float("inf")},
])
def test_equity_params_promotion_validation(params_kwargs):
    bad = replace(DEFAULT_EQUITY_PARAMS, **params_kwargs)
    with pytest.raises(ValueError):
        next_equity(62.0, 5.0, 3.0, bad, DEFAULT_PARAMS.budget)


@pytest.mark.parametrize("bad_depth", [-0.1, 1.1, "x", float("nan"), float("inf")])
def test_next_equity_promo_depth_validation(bad_depth):
    with pytest.raises(ValueError):
        next_equity(62.0, 5.0, 3.0, DEFAULT_EQUITY_PARAMS, DEFAULT_PARAMS.budget, promo_depth=bad_depth)


@pytest.mark.parametrize("kwargs", [
    {"dc_decay": -0.1},
    {"dc_decay": 1.1},
    {"dc_decay": float("inf")},
    {"dc_gain": -0.1},
    {"dc_gain": float("inf")},
    {"dc_start": -0.1},
    {"dc_start": float("inf")},
])
def test_promotion_params_validation(kwargs):
    bad = replace(DEFAULT_PROMOTION_PARAMS, **kwargs)
    with pytest.raises(ValueError):
        run_simulation_with_promotion(_rounds_with_depths(), promotion_params=bad)


def test_runner_validation():
    with pytest.raises(ValueError):
        run_simulation_with_promotion([])  # empty rounds
    with pytest.raises(ValueError):
        # inconsistent team set: drop D in round 2
        rounds = _rounds_with_depths()
        del rounds[1]["D"]
        run_simulation_with_promotion(rounds)
    with pytest.raises(ValueError):
        # malformed round decision (missing required field)
        rounds = _rounds_with_depths()
        del rounds[0]["A"]["brand"]
        run_simulation_with_promotion(rounds)
    with pytest.raises(ValueError):
        run_simulation_with_promotion(_rounds_with_depths(), growth_rates={"LEG": 0.0})
    with pytest.raises(ValueError):
        run_simulation_with_promotion(
            _rounds_with_depths(),
            equity_params=replace(DEFAULT_EQUITY_PARAMS, k_brand=0.0),
        )
    with pytest.raises(ValueError):
        run_simulation_with_promotion(
            _rounds_with_depths(),
            promotion_params=replace(DEFAULT_PROMOTION_PARAMS, dc_decay=2.0),
        )


# --------------------------------------------------------------------------- #
# Part 8 — Purity and determinism
# --------------------------------------------------------------------------- #

def test_purity_and_determinism():
    rounds = _rounds_with_depths()
    rounds_snapshot = copy.deepcopy(rounds)
    growth = dict(engine_default_growth())
    growth_snapshot = copy.deepcopy(growth)
    # Frozen module defaults hold a MappingProxyType (not deep-copyable); snapshot
    # via repr and confirm they are unchanged after the run.
    equity_params_before = repr(DEFAULT_EQUITY_PARAMS)
    promotion_params_before = repr(DEFAULT_PROMOTION_PARAMS)
    params_before = repr(DEFAULT_PARAMS)

    first = run_simulation_with_promotion(rounds, growth_rates=growth)
    second = run_simulation_with_promotion(copy.deepcopy(rounds), growth_rates=dict(growth))

    assert rounds == rounds_snapshot
    assert growth == growth_snapshot
    assert repr(DEFAULT_EQUITY_PARAMS) == equity_params_before
    assert repr(DEFAULT_PROMOTION_PARAMS) == promotion_params_before
    assert repr(DEFAULT_PARAMS) == params_before
    # two equivalent runs return equal histories
    assert _histories_equal(first, second)


def engine_default_growth():
    from simulation import DEFAULT_GROWTH_RATES
    return DEFAULT_GROWTH_RATES


def _histories_equal(a, b):
    if len(a) != len(b):
        return False
    for ra, rb in zip(a, b):
        if ra["equity"] != rb["equity"]:
            return False
        if ra["deal_conditioning"] != rb["deal_conditioning"]:
            return False
        if ra["promotion_depth"] != rb["promotion_depth"]:
            return False
        for team in ra["firms"]:
            if ra["firms"][team]["perception"] != rb["firms"][team]["perception"]:
                return False
            if ra["firms"][team]["contribution"] != rb["firms"][team]["contribution"]:
                return False
    return True
