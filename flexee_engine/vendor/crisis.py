from __future__ import annotations

from collections.abc import Mapping as MappingABC
from dataclasses import dataclass
from math import isfinite
from typing import Mapping, Sequence, TypedDict

from engine import DEFAULT_PARAMS, DecisionValue, SimulationParams, compute_round
from equity import DEFAULT_EQUITY_PARAMS, EquityParams, next_equity
from scorecard import ScorecardResults, compute_scorecard
from simulation import (
    DEFAULT_GROWTH_RATES,
    _params_with_segment_sizes,
    _validate_base_segment_sizes,
    _validate_decisions_by_round,
    _validate_growth_rates,
)


TOLERANCE = 1e-9
CARRIED_ATTRIBUTES = ("EQ", "TRUST", "PROD")


@dataclass(frozen=True)
class CrisisParams:
    crisis_round: int
    damage_base: float
    trust_fraction: float
    prod_fraction: float
    eq_fraction: float
    lower_bound: float
    upper_bound: float


DEFAULT_CRISIS_PARAMS = CrisisParams(
    crisis_round=4,
    damage_base=25.0,
    trust_fraction=0.5,
    prod_fraction=0.3,
    eq_fraction=0.2,
    lower_bound=0.0,
    upper_bound=100.0,
)


RoundDecisions = Mapping[str, Mapping[str, DecisionValue]]
CrisisInput = Mapping[str, float]
CrisisInputs = Mapping[str, CrisisInput]
SegmentSizes = dict[str, float]
EquityStocks = dict[str, float]
AttributeStocks = dict[str, dict[str, float]]
CrisisDamage = dict[str, float]


class CrisisRoundRecord(TypedDict):
    round: int
    segment_sizes: SegmentSizes
    firms: dict[str, object]
    scorecard: ScorecardResults
    equity: EquityStocks
    attribute_state: AttributeStocks
    damage: CrisisDamage


CrisisSimulationHistory = list[CrisisRoundRecord]


def crisis_damage(
    eq: float,
    exposure: float,
    preempt: float,
    response: float,
    crisis_params: CrisisParams = DEFAULT_CRISIS_PARAMS,
) -> float:
    """Compute the one-round crisis damage from the locked Chunk 3c formula."""
    _validate_crisis_params(crisis_params)
    eq_value = _finite_number(eq, "eq")
    exposure_value = _fraction(exposure, "exposure")
    preempt_value = _fraction(preempt, "preempt")
    response_value = _fraction(response, "response")
    if eq_value < crisis_params.lower_bound or eq_value > crisis_params.upper_bound:
        raise ValueError("eq must be within configured crisis bounds")

    return (
        crisis_params.damage_base
        * exposure_value
        * (1.0 - eq_value / 100.0)
        * (1.0 - preempt_value)
        * (1.0 - response_value)
    )


def run_simulation_with_crisis(
    decisions_by_round: Sequence[RoundDecisions],
    params: SimulationParams = DEFAULT_PARAMS,
    growth_rates: Mapping[str, float] = DEFAULT_GROWTH_RATES,
    equity_params: EquityParams = DEFAULT_EQUITY_PARAMS,
    crisis_params: CrisisParams = DEFAULT_CRISIS_PARAMS,
    crisis_inputs: CrisisInputs | None = None,
) -> CrisisSimulationHistory:
    """Run the Chunk 3c crisis simulation carrying EQ, TRUST, and PROD state."""
    _validate_crisis_params(crisis_params)
    _validate_decisions_by_round(decisions_by_round)
    base_sizes = _validate_base_segment_sizes(params)
    validated_growth_rates = _validate_growth_rates(base_sizes.keys(), growth_rates)
    expected_team_names = set(decisions_by_round[0])
    validated_crisis_inputs = _validate_crisis_inputs(crisis_inputs, expected_team_names)

    attribute_indexes = _validate_carried_attributes(params)
    baseline_attributes = {
        "TRUST": params.team_baseline_attributes[attribute_indexes["TRUST"]],
        "PROD": params.team_baseline_attributes[attribute_indexes["PROD"]],
    }

    equity_by_team: EquityStocks = {
        team_name: equity_params.eq_start
        for team_name in expected_team_names
    }
    trust_by_team: EquityStocks = {
        team_name: baseline_attributes["TRUST"]
        for team_name in expected_team_names
    }
    prod_by_team: EquityStocks = {
        team_name: baseline_attributes["PROD"]
        for team_name in expected_team_names
    }

    history: CrisisSimulationHistory = []

    for round_number, round_decisions in enumerate(decisions_by_round, start=1):
        if set(round_decisions) != expected_team_names:
            raise ValueError("each crisis simulation round must contain exactly the same team names")

        round_damage: CrisisDamage = {}
        for team_name, decision in round_decisions.items():
            equity_by_team[team_name] = next_equity(
                equity_by_team[team_name],
                _decision_number(decision, "brand", team_name),
                _decision_number(decision, "perf", team_name),
                equity_params,
                params.budget,
            )

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

        segment_sizes = {
            segment_name: base_size * (1.0 + validated_growth_rates[segment_name]) ** (round_number - 1)
            for segment_name, base_size in base_sizes.items()
        }
        round_params = _params_with_segment_sizes(params, segment_sizes)
        attribute_state = {
            team_name: {
                "EQ": equity_by_team[team_name],
                "TRUST": trust_by_team[team_name],
                "PROD": prod_by_team[team_name],
            }
            for team_name in round_decisions
        }
        round_firms = compute_round(round_decisions, round_params, team_attribute_overrides=attribute_state)
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
        })

    return history


def _validate_crisis_params(crisis_params: CrisisParams) -> None:
    if not isinstance(crisis_params.crisis_round, int):
        raise ValueError("crisis_round must be an integer")
    if crisis_params.crisis_round < 1:
        raise ValueError("crisis_round must be at least 1")
    for field_name in (
        "damage_base",
        "trust_fraction",
        "prod_fraction",
        "eq_fraction",
        "lower_bound",
        "upper_bound",
    ):
        _finite_number(getattr(crisis_params, field_name), field_name)
    if crisis_params.damage_base < 0.0:
        raise ValueError("damage_base must be nonnegative")
    split_total = sum(
        _fraction(getattr(crisis_params, field_name), field_name)
        for field_name in ("trust_fraction", "prod_fraction", "eq_fraction")
    )
    if abs(split_total - 1.0) > TOLERANCE:
        raise ValueError("crisis damage fractions must sum to 1.0")
    if crisis_params.lower_bound > crisis_params.upper_bound:
        raise ValueError("lower_bound must be less than or equal to upper_bound")


def _validate_carried_attributes(params: SimulationParams) -> dict[str, int]:
    if len(set(params.attributes)) != len(params.attributes):
        raise ValueError("attribute names must be unique")
    if len(params.team_baseline_attributes) != len(params.attributes):
        raise ValueError("team_baseline_attributes length must match attributes length")

    indexes: dict[str, int] = {}
    for attribute in CARRIED_ATTRIBUTES:
        if attribute not in params.attributes:
            raise ValueError(f"params.attributes must contain {attribute}")
        indexes[attribute] = params.attributes.index(attribute)
    return indexes


def _validate_crisis_inputs(crisis_inputs: CrisisInputs | None, team_names: set[str]) -> dict[str, dict[str, float]]:
    if crisis_inputs is None:
        raise ValueError("crisis_inputs must be provided")
    if not isinstance(crisis_inputs, MappingABC):
        raise ValueError("crisis_inputs must be a mapping")
    if set(crisis_inputs) != team_names:
        raise ValueError("crisis_inputs must contain exactly the participating team names")

    validated: dict[str, dict[str, float]] = {}
    for team_name, raw_inputs in crisis_inputs.items():
        if not isinstance(raw_inputs, MappingABC):
            raise ValueError(f"crisis inputs for {team_name} must be a mapping")
        if set(raw_inputs) != {"exposure", "preempt", "response"}:
            raise ValueError(f"crisis inputs for {team_name} must contain exposure, preempt, and response")
        validated[team_name] = {
            "exposure": _fraction(raw_inputs["exposure"], f"{team_name}.exposure"),
            "preempt": _fraction(raw_inputs["preempt"], f"{team_name}.preempt"),
            "response": _fraction(raw_inputs["response"], f"{team_name}.response"),
        }
    return validated


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


def _fraction(value: object, field_name: str) -> float:
    converted = _finite_number(value, field_name)
    if converted < 0.0 or converted > 1.0:
        raise ValueError(f"{field_name} must be within [0, 1]")
    return converted


def _clip(value: float, crisis_params: CrisisParams) -> float:
    return min(crisis_params.upper_bound, max(crisis_params.lower_bound, value))
