from __future__ import annotations

from dataclasses import dataclass
from math import exp, isfinite
from types import MappingProxyType
from collections.abc import Mapping as MappingABC
from typing import Literal, Mapping, Sequence, TypedDict, cast


SegmentValues = dict[str, float]
AttributeValues = dict[str, float]
DecisionValue = object


class TeamDecision(TypedDict):
    brand: DecisionValue
    perf: DecisionValue
    research: DecisionValue
    positioning: DecisionValue
    emphasis: Sequence[DecisionValue]
    price: DecisionValue
    promotion: DecisionValue


class FirmResult(TypedDict):
    firm_type: Literal["team", "backdrop"]
    attributes: AttributeValues
    price: float
    eq: float
    perception: SegmentValues
    attraction: SegmentValues
    share: SegmentValues
    volume: SegmentValues
    total_volume: float
    total_market_share: float
    growth_segment_share: float
    revenue: float
    contribution: float
    promotion: float
    deal_conditioning: float


DEFAULT_REFERENCE_PRICE = 1.5
SEGMENT_IDEAL_PRICES = MappingProxyType({
    "LEG": 1.30,
    "FIT": 1.60,
    "FUN": 1.75,
    "GENZ": 1.45,
    "MAIN": 1.20,
})
DEFAULT_DEAL_SENSITIVITY = 0.0
SEGMENT_DEAL_SENSITIVITIES = MappingProxyType({
    "LEG": 0.70,
    "FIT": 0.20,
    "FUN": 0.05,
    "GENZ": 0.80,
    "MAIN": 1.00,
})


@dataclass(frozen=True)
class Segment:
    name: str
    size: float
    positioning_ideal: float
    beta: float
    weights: tuple[float, ...]
    ideal_price: float | None = None
    deal_sensitivity: float | None = None

    def __post_init__(self) -> None:
        if self.ideal_price is None:
            object.__setattr__(
                self,
                "ideal_price",
                SEGMENT_IDEAL_PRICES.get(self.name, DEFAULT_REFERENCE_PRICE),
            )
        if self.deal_sensitivity is None:
            object.__setattr__(
                self,
                "deal_sensitivity",
                SEGMENT_DEAL_SENSITIVITIES.get(self.name, DEFAULT_DEAL_SENSITIVITY),
            )


@dataclass(frozen=True)
class FirmConfig:
    attributes: tuple[float, ...]
    positioning: float
    emphasis: tuple[float, ...]


@dataclass(frozen=True)
class SimulationParams:
    attributes: tuple[str, ...]
    segments: tuple[Segment, ...]
    positioning_bonus_max: float
    targeting_bonus_coef: float
    eq_start: float
    mu: float
    eq_gain_max: float
    eq_k: float
    price: float
    variable_cost: float
    value_fit_width: float
    value_fit_vp: float
    price_min: float
    price_max: float
    promotion_lift: float
    promo_cost_rate: float
    deal_conditioning_penalty: float
    promotion_min: float
    promotion_max: float
    budget: float
    team_baseline_attributes: tuple[float, ...]
    backdrop_firms: Mapping[str, FirmConfig]
    growth_segments: tuple[str, ...]


TOLERANCE = 1e-9
REQUIRED_DECISION_FIELDS = ("brand", "perf", "research", "positioning", "emphasis")


DEFAULT_PARAMS = SimulationParams(
    attributes=("VAL", "EQ", "PROD", "CRE", "DIST", "SOC", "TRUST"),
    segments=(
        Segment("LEG", 30.0, 10.0, 3.0, (0.20, 0.25, 0.05, 0.25, 0.15, 0.05, 0.05), 1.30, 0.70),
        Segment("FIT", 18.0, 55.0, 3.0, (0.10, 0.10, 0.30, 0.10, 0.10, 0.15, 0.15), 1.60, 0.20),
        Segment("FUN", 25.0, 90.0, 3.5, (0.05, 0.15, 0.30, 0.10, 0.10, 0.10, 0.20), 1.75, 0.05),
        Segment("GENZ", 12.0, 75.0, 4.0, (0.15, 0.05, 0.10, 0.25, 0.10, 0.30, 0.05), 1.45, 0.80),
        Segment("MAIN", 15.0, 45.0, 2.5, (0.35, 0.10, 0.10, 0.05, 0.30, 0.05, 0.05), 1.20, 1.00),
    ),
    positioning_bonus_max=20.0,
    targeting_bonus_coef=15.0,
    eq_start=62.0,
    mu=0.5,
    eq_gain_max=30.0,
    eq_k=4.0,
    price=1.5,
    variable_cost=0.6,
    value_fit_width=0.60,
    value_fit_vp=10.0,
    price_min=1.10,
    price_max=1.90,
    promotion_lift=8.0,
    promo_cost_rate=0.30,
    deal_conditioning_penalty=6.0,
    promotion_min=0.0,
    promotion_max=1.0,
    budget=10.0,
    team_baseline_attributes=(55.0, 62.0, 30.0, 58.0, 60.0, 40.0, 32.0),
    backdrop_firms=MappingProxyType({
        "BigRed": FirmConfig(
            attributes=(45.0, 85.0, 45.0, 70.0, 85.0, 65.0, 50.0),
            positioning=50.0,
            emphasis=(0.20, 0.20, 0.20, 0.20, 0.20),
        ),
        "Monssta": FirmConfig(
            attributes=(48.0, 80.0, 40.0, 68.0, 82.0, 60.0, 45.0),
            positioning=40.0,
            emphasis=(0.20, 0.20, 0.20, 0.20, 0.20),
        ),
        "Pulse": FirmConfig(
            attributes=(40.0, 55.0, 75.0, 60.0, 45.0, 70.0, 70.0),
            positioning=88.0,
            emphasis=(0.05, 0.20, 0.35, 0.35, 0.05),
        ),
    }),
    growth_segments=("FUN", "GENZ"),
)


def compute_round(
    decisions: Mapping[str, Mapping[str, DecisionValue]],
    params: SimulationParams = DEFAULT_PARAMS,
    team_attribute_overrides: Mapping[str, Mapping[str, float]] | None = None,
    team_deal_conditioning: Mapping[str, float] | None = None,
) -> dict[str, FirmResult]:
    """Compute one deterministic market-share round for teams plus backdrop firms."""
    _validate_pricing_params(params)
    _validate_promotion_params(params)
    segment_names = tuple(segment.name for segment in params.segments)
    eq_index = params.attributes.index("EQ")

    firms: dict[str, FirmConfig] = {}
    firm_types: dict[str, Literal["team", "backdrop"]] = {}
    eq_by_firm: dict[str, float] = {}
    effective_prices: dict[str, float] = {}
    promotion_by_firm: dict[str, float] = {}
    deal_conditioning_by_firm: dict[str, float] = {}

    validated_decisions = _validate_decisions(decisions, params)
    _validate_team_attribute_overrides(team_attribute_overrides, validated_decisions.keys(), params)
    validated_deal_conditioning = _validate_team_deal_conditioning(
        team_deal_conditioning, validated_decisions.keys(), params
    )

    for firm_name, decision in validated_decisions.items():
        brand = decision["brand"]
        positioning = decision["positioning"]
        emphasis = decision["emphasis"]
        price = _validate_price(decision.get("price", params.price), firm_name, params)
        promotion_depth = _validate_promotion(decision.get("promotion", 0.0), firm_name, params)
        eq_new = params.eq_start + params.mu * params.eq_gain_max * (1.0 - exp(-brand / params.eq_k))
        attributes = list(params.team_baseline_attributes)
        attributes[eq_index] = eq_new
        if team_attribute_overrides is not None and firm_name in team_attribute_overrides:
            _apply_team_attribute_overrides(attributes, team_attribute_overrides[firm_name], params)
        firms[firm_name] = FirmConfig(tuple(attributes), positioning, emphasis)
        eq_by_firm[firm_name] = attributes[eq_index]
        firm_types[firm_name] = "team"
        effective_prices[firm_name] = price
        promotion_by_firm[firm_name] = promotion_depth
        deal_conditioning_by_firm[firm_name] = validated_deal_conditioning.get(firm_name, 0.0)

    for firm_name, firm_config in params.backdrop_firms.items():
        firms[firm_name] = firm_config
        eq_by_firm[firm_name] = firm_config.attributes[eq_index]
        firm_types[firm_name] = "backdrop"
        effective_prices[firm_name] = params.price
        promotion_by_firm[firm_name] = 0.0
        deal_conditioning_by_firm[firm_name] = 0.0

    perception = {
        firm_name: {
            segment.name: _perception(
                firm_config,
                segment,
                segment_index,
                params,
                effective_prices[firm_name],
                promotion_by_firm[firm_name],
                deal_conditioning_by_firm[firm_name],
            )
            for segment_index, segment in enumerate(params.segments)
        }
        for firm_name, firm_config in firms.items()
    }

    attraction = {
        firm_name: {
            segment.name: exp(segment.beta * perception[firm_name][segment.name] / 100.0)
            for segment in params.segments
        }
        for firm_name in firms
    }

    segment_denominators = {
        segment.name: sum(attraction[firm_name][segment.name] for firm_name in firms)
        for segment in params.segments
    }

    share = {
        firm_name: {
            segment.name: attraction[firm_name][segment.name] / segment_denominators[segment.name]
            for segment in params.segments
        }
        for firm_name in firms
    }

    volume = {
        firm_name: {
            segment.name: share[firm_name][segment.name] * segment.size
            for segment in params.segments
        }
        for firm_name in firms
    }

    total_volume = {
        firm_name: sum(volume[firm_name][segment_name] for segment_name in segment_names)
        for firm_name in firms
    }
    market_volume = sum(total_volume.values())
    growth_size = sum(segment.size for segment in params.segments if segment.name in params.growth_segments)

    return {
        firm_name: {
            "firm_type": firm_types[firm_name],
            "attributes": dict(zip(params.attributes, firms[firm_name].attributes, strict=True)),
            "price": effective_prices[firm_name],
            "eq": eq_by_firm[firm_name],
            "perception": perception[firm_name],
            "attraction": attraction[firm_name],
            "share": share[firm_name],
            "volume": volume[firm_name],
            "total_volume": total_volume[firm_name],
            "total_market_share": total_volume[firm_name] / market_volume,
            "growth_segment_share": (
                sum(volume[firm_name][segment_name] for segment_name in params.growth_segments) / growth_size
            ),
            "revenue": total_volume[firm_name] * effective_prices[firm_name],
            "contribution": total_volume[firm_name] * (
                effective_prices[firm_name]
                - params.variable_cost
                - params.promo_cost_rate * promotion_by_firm[firm_name]
            ) - params.budget,
            "promotion": promotion_by_firm[firm_name],
            "deal_conditioning": deal_conditioning_by_firm[firm_name],
        }
        for firm_name in firms
    }


def _validate_team_attribute_overrides(
    team_attribute_overrides: Mapping[str, Mapping[str, float]] | None,
    team_names: object,
    params: SimulationParams,
) -> None:
    if team_attribute_overrides is None:
        return
    if not isinstance(team_attribute_overrides, MappingABC):
        raise ValueError("team_attribute_overrides must be a mapping")

    valid_team_names = set(team_names)  # type: ignore[arg-type]
    for firm_name, overrides in team_attribute_overrides.items():
        if firm_name in params.backdrop_firms:
            raise ValueError(f"cannot override backdrop firm attributes: {firm_name}")
        if firm_name not in valid_team_names:
            raise ValueError(f"unknown team attribute override: {firm_name}")
        if not isinstance(overrides, MappingABC):
            raise ValueError(f"attribute overrides for {firm_name} must be a mapping")
        for attribute_name, value in overrides.items():
            if attribute_name not in params.attributes:
                raise ValueError(f"unknown attribute override: {attribute_name}")
            _finite_number(value, attribute_name)


def _apply_team_attribute_overrides(
    attributes: list[float],
    overrides: Mapping[str, float],
    params: SimulationParams,
) -> None:
    for attribute_name, value in overrides.items():
        if attribute_name not in params.attributes:
            raise ValueError(f"unknown attribute override: {attribute_name}")
        attributes[params.attributes.index(attribute_name)] = _finite_number(value, attribute_name)


def _validate_decisions(
    decisions: Mapping[str, Mapping[str, DecisionValue]],
    params: SimulationParams,
) -> dict[str, TeamDecision]:
    if not isinstance(decisions, Mapping):
        raise ValueError("decisions must be a mapping of team names to decision values")

    validated: dict[str, TeamDecision] = {}
    seen_names: set[str] = set()

    for raw_team_name, raw_decision in decisions.items():
        if not isinstance(raw_team_name, str) or raw_team_name == "":
            raise ValueError("team names must be non-empty strings")
        if raw_team_name in seen_names:
            raise ValueError(f"duplicate team name: {raw_team_name}")
        if raw_team_name in params.backdrop_firms:
            raise ValueError(f"team name conflicts with configured backdrop firm: {raw_team_name}")
        if not isinstance(raw_decision, Mapping):
            raise ValueError(f"decision for {raw_team_name} must be a mapping")

        seen_names.add(raw_team_name)
        missing_fields = [field for field in REQUIRED_DECISION_FIELDS if field not in raw_decision]
        if missing_fields:
            raise ValueError(f"decision for {raw_team_name} is missing required field: {missing_fields[0]}")

        brand = _finite_number(raw_decision["brand"], f"{raw_team_name}.brand")
        perf = _finite_number(raw_decision["perf"], f"{raw_team_name}.perf")
        research = _finite_number(raw_decision["research"], f"{raw_team_name}.research")
        for field_name, value in (("brand", brand), ("perf", perf), ("research", research)):
            if value < 0.0:
                raise ValueError(f"{raw_team_name}.{field_name} cannot be negative")

        if abs((brand + perf + research) - params.budget) > TOLERANCE:
            raise ValueError(f"{raw_team_name} spending must sum to configured budget {params.budget}")

        positioning = _finite_number(raw_decision["positioning"], f"{raw_team_name}.positioning")
        if positioning < 0.0 or positioning > 100.0:
            raise ValueError(f"{raw_team_name}.positioning must be between 0 and 100")

        emphasis = _validate_emphasis(raw_decision["emphasis"], raw_team_name, len(params.segments))

        validated[raw_team_name] = {
            "brand": brand,
            "perf": perf,
            "research": research,
            "positioning": positioning,
            "emphasis": emphasis,
            "price": raw_decision.get("price", params.price),
            "promotion": raw_decision.get("promotion", 0.0),
        }

    return validated


def _finite_number(value: DecisionValue, field_name: str) -> float:
    try:
        converted = float(cast(float, value))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be a finite number") from exc
    if not isfinite(converted):
        raise ValueError(f"{field_name} must be a finite number")
    return converted


def _validate_price(value: DecisionValue, team_name: str, params: SimulationParams) -> float:
    price = _finite_number(value, f"{team_name}.price")
    if price < params.price_min or price > params.price_max:
        raise ValueError(f"{team_name}.price must be within configured price bounds")
    return price


def _validate_promotion(value: DecisionValue, team_name: str, params: SimulationParams) -> float:
    promotion = _finite_number(value, f"{team_name}.promotion")
    if promotion < params.promotion_min or promotion > params.promotion_max:
        raise ValueError(f"{team_name}.promotion must be within configured promotion bounds")
    return promotion


def _validate_team_deal_conditioning(
    team_deal_conditioning: Mapping[str, float] | None,
    team_names: object,
    params: SimulationParams,
) -> dict[str, float]:
    if team_deal_conditioning is None:
        return {}
    if not isinstance(team_deal_conditioning, MappingABC):
        raise ValueError("team_deal_conditioning must be a mapping")

    valid_team_names = set(team_names)  # type: ignore[arg-type]
    validated: dict[str, float] = {}
    for firm_name, value in team_deal_conditioning.items():
        if firm_name in params.backdrop_firms:
            raise ValueError(f"cannot set deal conditioning for backdrop firm: {firm_name}")
        if firm_name not in valid_team_names:
            raise ValueError(f"unknown team deal conditioning: {firm_name}")
        dc_value = _finite_number(value, f"{firm_name}.deal_conditioning")
        if dc_value < 0.0:
            raise ValueError(f"{firm_name}.deal_conditioning cannot be negative")
        validated[firm_name] = dc_value
    return validated


def _validate_promotion_params(params: SimulationParams) -> None:
    _finite_number(params.promotion_lift, "promotion_lift")
    promo_cost_rate = _finite_number(params.promo_cost_rate, "promo_cost_rate")
    if promo_cost_rate < 0.0:
        raise ValueError("promo_cost_rate must be nonnegative")
    _finite_number(params.deal_conditioning_penalty, "deal_conditioning_penalty")
    promotion_min = _finite_number(params.promotion_min, "promotion_min")
    promotion_max = _finite_number(params.promotion_max, "promotion_max")
    if promotion_min > promotion_max:
        raise ValueError("promotion_min must be less than or equal to promotion_max")
    if promotion_min > 0.0 or promotion_max < 0.0:
        raise ValueError("0.0 must be within configured promotion bounds")
    for segment in params.segments:
        deal_sensitivity = _finite_number(segment.deal_sensitivity, f"{segment.name}.deal_sensitivity")
        if deal_sensitivity < 0.0:
            raise ValueError(f"{segment.name}.deal_sensitivity must be nonnegative")


def _validate_pricing_params(params: SimulationParams) -> None:
    reference_price = _finite_number(params.price, "price")
    _finite_number(params.variable_cost, "variable_cost")
    value_fit_width = _finite_number(params.value_fit_width, "value_fit_width")
    _finite_number(params.value_fit_vp, "value_fit_vp")
    price_min = _finite_number(params.price_min, "price_min")
    price_max = _finite_number(params.price_max, "price_max")
    if value_fit_width <= 0.0:
        raise ValueError("value_fit_width must be greater than 0")
    if price_min > price_max:
        raise ValueError("price_min must be less than or equal to price_max")
    if reference_price < price_min or reference_price > price_max:
        raise ValueError("price must be within configured price bounds")
    for segment in params.segments:
        _finite_number(segment.ideal_price, f"{segment.name}.ideal_price")


def _validate_emphasis(value: DecisionValue, team_name: str, segment_count: int) -> tuple[float, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValueError(f"{team_name}.emphasis must be a sequence with one value per segment")
    if len(value) != segment_count:
        raise ValueError(f"{team_name}.emphasis must contain exactly {segment_count} values")

    emphasis = tuple(_finite_number(item, f"{team_name}.emphasis") for item in value)
    if any(item < 0.0 for item in emphasis):
        raise ValueError(f"{team_name}.emphasis values cannot be negative")
    if abs(sum(emphasis) - 1.0) > TOLERANCE:
        raise ValueError(f"{team_name}.emphasis values must sum to 1.0")
    return emphasis


def _perception(
    firm_config: FirmConfig,
    segment: Segment,
    segment_index: int,
    params: SimulationParams,
    price: float,
    promotion_depth: float = 0.0,
    entering_dc: float = 0.0,
) -> float:
    attribute_fit = sum(
        weight * attribute
        for weight, attribute in zip(segment.weights, firm_config.attributes, strict=True)
    )
    positioning_fit = params.positioning_bonus_max * (
        1.0 - 2.0 * abs(firm_config.positioning - segment.positioning_ideal) / 100.0
    )
    targeting_fit = params.targeting_bonus_coef * firm_config.emphasis[segment_index]
    value_fit = params.value_fit_vp * _incremental_value_fit(segment, price, params)
    base_perception = attribute_fit + positioning_fit + targeting_fit + value_fit
    if promotion_depth == 0.0 and entering_dc == 0.0:
        return base_perception
    promotion_lift = params.promotion_lift * promotion_depth * segment.deal_sensitivity
    deal_conditioning_penalty = (
        params.deal_conditioning_penalty * entering_dc * segment.deal_sensitivity
    )
    return base_perception + promotion_lift - deal_conditioning_penalty


def _incremental_value_fit(segment: Segment, price: float, params: SimulationParams) -> float:
    return _raw_value_fit(segment, price, params) - _raw_value_fit(segment, params.price, params)


def _raw_value_fit(segment: Segment, price: float, params: SimulationParams) -> float:
    value_fit = 1.0 - ((price - segment.ideal_price) / params.value_fit_width) ** 2
    return min(1.0, max(-1.0, value_fit))
