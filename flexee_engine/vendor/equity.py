from __future__ import annotations

from dataclasses import dataclass
from math import exp, isfinite
from typing import Mapping, Sequence, TypedDict

from engine import DEFAULT_PARAMS, DecisionValue, SimulationParams, compute_round
from scorecard import ScorecardResults, compute_scorecard
from simulation import (
    DEFAULT_GROWTH_RATES,
    _params_with_segment_sizes,
    _validate_base_segment_sizes,
    _validate_decisions_by_round,
    _validate_growth_rates,
)


@dataclass(frozen=True)
class EquityParams:
    eq_base: float
    eq_start: float
    a_brand: float
    k_brand: float
    b_perfskew: float
    tau: float
    lambda_decay: float
    lower_bound: float
    upper_bound: float
    b_promo: float = 4.0


DEFAULT_EQUITY_PARAMS = EquityParams(
    eq_base=62.0,
    eq_start=62.0,
    a_brand=8.0,
    k_brand=4.0,
    b_perfskew=6.0,
    tau=0.1,
    lambda_decay=0.25,
    lower_bound=0.0,
    upper_bound=100.0,
    b_promo=4.0,
)


RoundDecisions = Mapping[str, Mapping[str, DecisionValue]]
SegmentSizes = dict[str, float]
EquityStocks = dict[str, float]


class EquityRoundRecord(TypedDict):
    round: int
    segment_sizes: SegmentSizes
    firms: dict[str, object]
    scorecard: ScorecardResults
    equity: EquityStocks


EquitySimulationHistory = list[EquityRoundRecord]


def next_equity(
    prev_eq: float,
    brand: float,
    perf: float,
    equity_params: EquityParams = DEFAULT_EQUITY_PARAMS,
    budget: float = DEFAULT_PARAMS.budget,
    promo_depth: float = 0.0,
) -> float:
    """Compute the next brand-equity stock from the locked Chunk 3b recurrence.

    An optional per-round promotion depth adds a ``b_promo * promo_depth`` debit
    (Chunk 3d-ii). At ``promo_depth == 0.0`` the recurrence is byte-identical to
    the Chunk 3b/3c behaviour.
    """
    _validate_equity_params(equity_params)
    prev_eq_value = _finite_number(prev_eq, "prev_eq")
    brand_value = _finite_number(brand, "brand")
    perf_value = _finite_number(perf, "perf")
    budget_value = _finite_number(budget, "budget")
    promo_depth_value = _finite_number(promo_depth, "promo_depth")
    if brand_value < 0.0:
        raise ValueError("brand must be nonnegative")
    if perf_value < 0.0:
        raise ValueError("perf must be nonnegative")
    _validate_within_bounds(prev_eq_value, "prev_eq", equity_params)
    if budget_value <= 0.0:
        raise ValueError("budget must be greater than 0")
    if promo_depth_value < 0.0 or promo_depth_value > 1.0:
        raise ValueError("promo_depth must be within [0.0, 1.0]")

    credit = equity_params.a_brand * (1.0 - exp(-brand_value / equity_params.k_brand))
    debit = equity_params.b_perfskew * max(
        0.0,
        (perf_value - brand_value) / budget_value - equity_params.tau,
    )
    promo_debit = equity_params.b_promo * promo_depth_value
    reversion = equity_params.lambda_decay * (prev_eq_value - equity_params.eq_base)

    return min(
        equity_params.upper_bound,
        max(equity_params.lower_bound, prev_eq_value + credit - debit - promo_debit - reversion),
    )


def run_simulation_with_equity(
    decisions_by_round: Sequence[RoundDecisions],
    params: SimulationParams = DEFAULT_PARAMS,
    growth_rates: Mapping[str, float] = DEFAULT_GROWTH_RATES,
    equity_params: EquityParams = DEFAULT_EQUITY_PARAMS,
) -> EquitySimulationHistory:
    """Run multiple rounds using carried EQ stock as a team attribute override."""
    _validate_decisions_by_round(decisions_by_round)
    base_sizes = _validate_base_segment_sizes(params)
    validated_growth_rates = _validate_growth_rates(base_sizes.keys(), growth_rates)
    expected_team_names = set(decisions_by_round[0])

    history: EquitySimulationHistory = []
    equity_by_team: EquityStocks = {
        team_name: equity_params.eq_start
        for team_name in expected_team_names
    }

    for round_number, round_decisions in enumerate(decisions_by_round, start=1):
        if set(round_decisions) != expected_team_names:
            raise ValueError("each equity simulation round must contain exactly the same team names")

        round_equity: EquityStocks = {}
        for team_name, decision in round_decisions.items():
            prev_eq = equity_by_team[team_name]
            round_equity[team_name] = next_equity(
                prev_eq,
                _decision_number(decision, "brand", team_name),
                _decision_number(decision, "perf", team_name),
                equity_params,
                params.budget,
            )
        equity_by_team.update(round_equity)

        segment_sizes = {
            segment_name: base_size * (1.0 + validated_growth_rates[segment_name]) ** (round_number - 1)
            for segment_name, base_size in base_sizes.items()
        }
        round_params = _params_with_segment_sizes(params, segment_sizes)
        overrides = {
            team_name: {"EQ": eq_stock}
            for team_name, eq_stock in round_equity.items()
        }
        round_firms = compute_round(round_decisions, round_params, team_attribute_overrides=overrides)
        scorecard = compute_scorecard(round_firms)

        history.append({
            "round": round_number,
            "segment_sizes": segment_sizes,
            "firms": round_firms,
            "scorecard": scorecard,
            "equity": dict(round_equity),
        })

    return history


def _validate_equity_params(equity_params: EquityParams) -> None:
    for field_name in (
        "eq_base",
        "eq_start",
        "a_brand",
        "k_brand",
        "b_perfskew",
        "tau",
        "lambda_decay",
        "lower_bound",
        "upper_bound",
        "b_promo",
    ):
        _finite_number(getattr(equity_params, field_name), field_name)
    if equity_params.k_brand <= 0.0:
        raise ValueError("k_brand must be greater than 0")
    if equity_params.b_promo < 0.0:
        raise ValueError("b_promo must be nonnegative")
    if equity_params.lambda_decay < 0.0:
        raise ValueError("lambda_decay must be nonnegative")
    if equity_params.lower_bound > equity_params.upper_bound:
        raise ValueError("lower_bound must be less than or equal to upper_bound")
    _validate_within_bounds(equity_params.eq_start, "eq_start", equity_params)
    _validate_within_bounds(equity_params.eq_base, "eq_base", equity_params)


def _validate_within_bounds(value: float, field_name: str, equity_params: EquityParams) -> None:
    if value < equity_params.lower_bound or value > equity_params.upper_bound:
        raise ValueError(f"{field_name} must be within configured equity bounds")


def _decision_number(decision: Mapping[str, DecisionValue], field_name: str, team_name: str) -> float:
    if field_name not in decision:
        raise ValueError(f"decision for {team_name} is missing required field: {field_name}")
    return _finite_number(decision[field_name], f"{team_name}.{field_name}")


def _finite_number(value: object, field_name: str) -> float:
    try:
        converted = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be a finite number") from exc
    if not isfinite(converted):
        raise ValueError(f"{field_name} must be a finite number")
    return converted
