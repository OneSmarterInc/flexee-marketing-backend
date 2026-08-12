from __future__ import annotations

from collections.abc import Mapping as MappingABC
from math import isfinite
from typing import Mapping, Sequence, TypedDict

from crisis import (
    DEFAULT_CRISIS_PARAMS,
    CrisisInputs,
    CrisisParams,
    crisis_damage,
    _clip,
    _validate_carried_attributes,
    _validate_crisis_inputs,
    _validate_crisis_params,
)
from engine import DEFAULT_PARAMS, DecisionValue, SimulationParams, _validate_promotion, compute_round
from equity import DEFAULT_EQUITY_PARAMS, EquityParams, next_equity
from promotion import DEFAULT_PROMOTION_PARAMS, PromotionParams, _validate_promotion_params
from scorecard import ScorecardResults, compute_scorecard
from simulation import (
    DEFAULT_GROWTH_RATES,
    _params_with_segment_sizes,
    _validate_base_segment_sizes,
    _validate_decisions_by_round,
    _validate_growth_rates,
)


RoundDecisions = Mapping[str, Mapping[str, DecisionValue]]
SegmentSizes = dict[str, float]
EquityStocks = dict[str, float]
AttributeStocks = dict[str, dict[str, float]]
CrisisDamage = dict[str, float]
DealConditioning = dict[str, float]
PromotionDepths = dict[str, float]


class GameRoundRecord(TypedDict):
    round: int
    segment_sizes: SegmentSizes
    firms: dict[str, object]
    scorecard: ScorecardResults
    equity: EquityStocks
    attribute_state: AttributeStocks
    damage: CrisisDamage
    deal_conditioning: DealConditioning
    promotion_depth: PromotionDepths


GameHistory = list[GameRoundRecord]


def run_game(
    decisions_by_round: Sequence[RoundDecisions],
    params: SimulationParams = DEFAULT_PARAMS,
    growth_rates: Mapping[str, float] = DEFAULT_GROWTH_RATES,
    equity_params: EquityParams = DEFAULT_EQUITY_PARAMS,
    crisis_params: CrisisParams = DEFAULT_CRISIS_PARAMS,
    promotion_params: PromotionParams = DEFAULT_PROMOTION_PARAMS,
    crisis_inputs: CrisisInputs | None = None,
) -> GameHistory:
    """Run the integrated state loop for equity, crisis scarring, and promotion.

    This is intentionally a direct game-state runner. Faculty gating, advisors,
    customer agents, persistence, and post-crisis recovery mechanics stay outside
    this chunk.
    """
    _validate_decisions_by_round(decisions_by_round)
    _validate_crisis_params(crisis_params)
    _validate_promotion_params(promotion_params)
    base_sizes = _validate_base_segment_sizes(params)
    validated_growth_rates = _validate_growth_rates(base_sizes.keys(), growth_rates)
    expected_team_names = set(decisions_by_round[0])
    validated_crisis_inputs = _validate_game_crisis_inputs(
        crisis_inputs,
        expected_team_names,
    )

    attribute_indexes = _validate_carried_attributes(params)
    equity_by_team: EquityStocks = {
        team_name: equity_params.eq_start
        for team_name in expected_team_names
    }
    trust_by_team: EquityStocks = {
        team_name: params.team_baseline_attributes[attribute_indexes["TRUST"]]
        for team_name in expected_team_names
    }
    prod_by_team: EquityStocks = {
        team_name: params.team_baseline_attributes[attribute_indexes["PROD"]]
        for team_name in expected_team_names
    }
    deal_conditioning_by_team: DealConditioning = {
        team_name: promotion_params.dc_start
        for team_name in expected_team_names
    }

    history: GameHistory = []

    for round_number, round_decisions in enumerate(decisions_by_round, start=1):
        if set(round_decisions) != expected_team_names:
            raise ValueError("each game round must contain exactly the same team names")

        segment_sizes = {
            segment_name: base_size * (1.0 + validated_growth_rates[segment_name]) ** (round_number - 1)
            for segment_name, base_size in base_sizes.items()
        }
        round_params = _params_with_segment_sizes(params, segment_sizes)

        for team_name, decision in round_decisions.items():
            if not isinstance(decision, MappingABC):
                raise ValueError(f"decision for {team_name} in round {round_number} must be a mapping")

        round_depth: PromotionDepths = {
            team_name: _validate_promotion(decision.get("promotion", 0.0), team_name, params)
            for team_name, decision in round_decisions.items()
        }

        for team_name, decision in round_decisions.items():
            equity_by_team[team_name] = next_equity(
                equity_by_team[team_name],
                _decision_number(decision, "brand", team_name),
                _decision_number(decision, "perf", team_name),
                equity_params,
                params.budget,
                promo_depth=round_depth[team_name],
            )

        round_damage: CrisisDamage = {}
        if round_number == crisis_params.crisis_round:
            for team_name in round_decisions:
                inputs = validated_crisis_inputs[team_name]
                damage = crisis_damage(
                    equity_by_team[team_name],
                    inputs["exposure"],
                    inputs["preempt"],
                    inputs["response"],
                    crisis_params,
                )
                round_damage[team_name] = damage
                equity_by_team[team_name] = _clip(
                    equity_by_team[team_name] - crisis_params.eq_fraction * damage,
                    crisis_params,
                )
                trust_by_team[team_name] = _clip(
                    trust_by_team[team_name] - crisis_params.trust_fraction * damage,
                    crisis_params,
                )
                prod_by_team[team_name] = _clip(
                    prod_by_team[team_name] - crisis_params.prod_fraction * damage,
                    crisis_params,
                )

        entering_dc: DealConditioning = {
            team_name: deal_conditioning_by_team[team_name]
            for team_name in expected_team_names
        }
        attribute_state = {
            team_name: {
                "EQ": equity_by_team[team_name],
                "TRUST": trust_by_team[team_name],
                "PROD": prod_by_team[team_name],
            }
            for team_name in round_decisions
        }
        round_firms = compute_round(
            round_decisions,
            round_params,
            team_attribute_overrides=attribute_state,
            team_deal_conditioning=entering_dc,
        )
        scorecard = compute_scorecard(round_firms)

        history.append({
            "round": round_number,
            "segment_sizes": segment_sizes,
            "firms": round_firms,
            "scorecard": scorecard,
            "equity": dict(equity_by_team),
            "attribute_state": {
                team_name: dict(attributes)
                for team_name, attributes in attribute_state.items()
            },
            "damage": dict(round_damage),
            "deal_conditioning": dict(entering_dc),
            "promotion_depth": dict(round_depth),
        })

        deal_conditioning_by_team = {
            team_name: entering_dc[team_name] * (1.0 - promotion_params.dc_decay)
            + promotion_params.dc_gain * round_depth[team_name]
            for team_name in expected_team_names
        }

    return history


def _validate_game_crisis_inputs(
    crisis_inputs: CrisisInputs | None,
    team_names: set[str],
) -> dict[str, dict[str, float]]:
    if crisis_inputs is None:
        return {
            team_name: {
                "exposure": 0.0,
                "preempt": 0.0,
                "response": 0.0,
            }
            for team_name in team_names
        }
    return _validate_crisis_inputs(crisis_inputs, team_names)


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
