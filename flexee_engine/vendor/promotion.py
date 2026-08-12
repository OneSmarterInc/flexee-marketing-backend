from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Mapping, Sequence, TypedDict

from engine import (
    DEFAULT_PARAMS,
    DecisionValue,
    SimulationParams,
    _validate_promotion,
    compute_round,
)
from equity import DEFAULT_EQUITY_PARAMS, EquityParams, next_equity
from scorecard import ScorecardResults, compute_scorecard
from simulation import (
    DEFAULT_GROWTH_RATES,
    _params_with_segment_sizes,
    _validate_base_segment_sizes,
    _validate_decisions_by_round,
    _validate_growth_rates,
)


@dataclass(frozen=True)
class PromotionParams:
    dc_decay: float
    dc_gain: float
    dc_start: float


DEFAULT_PROMOTION_PARAMS = PromotionParams(
    dc_decay=0.30,
    dc_gain=0.50,
    dc_start=0.0,
)


RoundDecisions = Mapping[str, Mapping[str, DecisionValue]]
SegmentSizes = dict[str, float]
EquityStocks = dict[str, float]
DealConditioning = dict[str, float]
PromotionDepths = dict[str, float]


class PromotionRoundRecord(TypedDict):
    round: int
    segment_sizes: SegmentSizes
    firms: dict[str, object]
    scorecard: ScorecardResults
    equity: EquityStocks
    deal_conditioning: DealConditioning
    promotion_depth: PromotionDepths


PromotionSimulationHistory = list[PromotionRoundRecord]


def run_simulation_with_promotion(
    decisions_by_round: Sequence[RoundDecisions],
    params: SimulationParams = DEFAULT_PARAMS,
    growth_rates: Mapping[str, float] = DEFAULT_GROWTH_RATES,
    equity_params: EquityParams = DEFAULT_EQUITY_PARAMS,
    promotion_params: PromotionParams = DEFAULT_PROMOTION_PARAMS,
) -> PromotionSimulationHistory:
    """Run multiple rounds carrying brand-equity stock and deal-conditioning state.

    Each round: (1) grow segment sizes from the original base sizes; (2) update
    every team's EQ stock with the promo debit applied before market resolution;
    (3) resolve the market with the carried EQ overrides and the deal-conditioning
    state ENTERING the round; (4) score; (5) advance deal-conditioning for the
    next round. Promotion depth 0 and DC 0 reproduce the Chunk 3b equity run.
    """
    _validate_decisions_by_round(decisions_by_round)
    _validate_promotion_params(promotion_params)
    base_sizes = _validate_base_segment_sizes(params)
    validated_growth_rates = _validate_growth_rates(base_sizes.keys(), growth_rates)
    expected_team_names = set(decisions_by_round[0])

    history: PromotionSimulationHistory = []
    equity_by_team: EquityStocks = {
        team_name: equity_params.eq_start for team_name in expected_team_names
    }
    deal_conditioning_by_team: DealConditioning = {
        team_name: promotion_params.dc_start for team_name in expected_team_names
    }

    for round_number, round_decisions in enumerate(decisions_by_round, start=1):
        if set(round_decisions) != expected_team_names:
            raise ValueError("each promotion simulation round must contain exactly the same team names")

        # Step 2: read and validate this round's promotion depths (omitted -> 0.0).
        round_depth: PromotionDepths = {
            team_name: _validate_promotion(decision.get("promotion", 0.0), team_name, params)
            for team_name, decision in round_decisions.items()
        }

        # Step 3: update EQ before market resolution; the promo debit lands here.
        round_equity: EquityStocks = {}
        for team_name, decision in round_decisions.items():
            round_equity[team_name] = next_equity(
                equity_by_team[team_name],
                _decision_number(decision, "brand", team_name),
                _decision_number(decision, "perf", team_name),
                equity_params,
                params.budget,
                promo_depth=round_depth[team_name],
            )
        equity_by_team.update(round_equity)

        # Step 4: capture the deal-conditioning state ENTERING this round.
        entering_dc: DealConditioning = {
            team_name: deal_conditioning_by_team[team_name]
            for team_name in expected_team_names
        }

        # Step 1: grow segment sizes directly from the original base sizes.
        segment_sizes = {
            segment_name: base_size * (1.0 + validated_growth_rates[segment_name]) ** (round_number - 1)
            for segment_name, base_size in base_sizes.items()
        }
        round_params = _params_with_segment_sizes(params, segment_sizes)

        overrides = {team_name: {"EQ": eq_stock} for team_name, eq_stock in round_equity.items()}
        round_firms = compute_round(
            round_decisions,
            round_params,
            team_attribute_overrides=overrides,
            team_deal_conditioning=entering_dc,
        )
        scorecard = compute_scorecard(round_firms)

        history.append({
            "round": round_number,
            "segment_sizes": segment_sizes,
            "firms": round_firms,
            "scorecard": scorecard,
            "equity": dict(round_equity),
            "deal_conditioning": dict(entering_dc),
            "promotion_depth": dict(round_depth),
        })

        # Step 5: advance deal-conditioning for the NEXT round (not used this round).
        deal_conditioning_by_team = {
            team_name: entering_dc[team_name] * (1.0 - promotion_params.dc_decay)
            + promotion_params.dc_gain * round_depth[team_name]
            for team_name in expected_team_names
        }

    return history


def _validate_promotion_params(promotion_params: PromotionParams) -> None:
    dc_decay = _finite_number(promotion_params.dc_decay, "dc_decay")
    dc_gain = _finite_number(promotion_params.dc_gain, "dc_gain")
    dc_start = _finite_number(promotion_params.dc_start, "dc_start")
    if dc_decay < 0.0 or dc_decay > 1.0:
        raise ValueError("dc_decay must be within [0.0, 1.0]")
    if dc_gain < 0.0:
        raise ValueError("dc_gain must be nonnegative")
    if dc_start < 0.0:
        raise ValueError("dc_start must be nonnegative")


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
