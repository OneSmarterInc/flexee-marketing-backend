"""Locks the calibrated shipping configuration and confirms it is a config layer only
(the reference defaults the goldens use are untouched). Uses the calibration harness.
"""
from dataclasses import replace

import pytest

from engine import DEFAULT_PARAMS
from equity import DEFAULT_EQUITY_PARAMS
from calibration import ARCHETYPES, CRISIS_INPUTS
from game import run_game
from crisis import CrisisParams
from flexee_calibrated_config import (
    CALIBRATED_PARAMS,
    CALIBRATED_EQUITY_PARAMS,
    CALIBRATED_PROMOTION_PARAMS,
    CALIBRATED_CRISIS_DAMAGE_BASE,
)


def _telemetry_calibrated():
    teams = list(ARCHETYPES)
    decisions = [{t: dict(ARCHETYPES[t]) for t in ARCHETYPES} for _ in range(14)]
    cp = CrisisParams(crisis_round=9, damage_base=CALIBRATED_CRISIS_DAMAGE_BASE,
                      trust_fraction=0.5, prod_fraction=0.3, eq_fraction=0.2,
                      lower_bound=0.0, upper_bound=100.0)
    history = run_game(decisions, params=CALIBRATED_PARAMS, equity_params=CALIBRATED_EQUITY_PARAMS,
                       promotion_params=CALIBRATED_PROMOTION_PARAMS, crisis_params=cp,
                       crisis_inputs=CRISIS_INPUTS)
    last = history[-1]
    overall = {t: last["scorecard"]["firms"][t]["overall"] for t in teams}
    contribution = {t: last["firms"][t]["contribution"] for t in teams}
    equity = {t: last["equity"][t] for t in teams}
    return overall, contribution, equity


CALIBRATED_OVERALL = {"PIVOT": 93.8, "BALANCED": 62.5, "PERF": 51.9, "HERITAGE": 50.0, "PROMO": 7.4}


def test_calibrated_values():
    assert CALIBRATED_PARAMS.positioning_bonus_max == 32.0
    assert CALIBRATED_PARAMS.promo_cost_rate == 0.20
    assert CALIBRATED_PARAMS.deal_conditioning_penalty == 4.0
    assert CALIBRATED_EQUITY_PARAMS.b_promo == 2.5
    assert CALIBRATED_CRISIS_DAMAGE_BASE == 25.0


def test_reference_defaults_untouched():
    # the calibrated config must NOT mutate the reference defaults the goldens are computed from
    assert DEFAULT_PARAMS.positioning_bonus_max == 20.0
    assert DEFAULT_PARAMS.promo_cost_rate == 0.30
    assert DEFAULT_PARAMS.deal_conditioning_penalty == 6.0
    assert DEFAULT_EQUITY_PARAMS.b_promo == 4.0


def test_calibrated_telemetry_snapshot():
    overall, _, _ = _telemetry_calibrated()
    for team, expected in CALIBRATED_OVERALL.items():
        assert overall[team] == pytest.approx(expected, abs=0.2)


def test_pedagogy_diagnostics_hold():
    overall, contribution, equity = _telemetry_calibrated()
    assert overall["PIVOT"] > overall["HERITAGE"]                       # heritage trap
    assert overall["PROMO"] < overall["PIVOT"] and contribution["PROMO"] < contribution["PIVOT"]  # promo punished
    assert overall["BALANCED"] < max(overall["HERITAGE"], overall["PIVOT"])  # stuck in the middle
    assert equity["PROMO"] < equity["PIVOT"]                            # promo equity erosion
    # eased-but-still-worst: promo is clearly last but not obliterated
    assert overall["PROMO"] == min(overall.values())
    assert overall["PROMO"] > 5.0
