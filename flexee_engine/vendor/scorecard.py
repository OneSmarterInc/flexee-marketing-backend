from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from types import MappingProxyType
from typing import Mapping, TypedDict

from engine import DEFAULT_PARAMS


MetricValues = dict[str, float]
IndustryPerspective = dict[str, float]
IndustrySummary = dict[str, IndustryPerspective]
TOLERANCE = 1e-9
SUPPORTED_NORMALIZATION_METHOD = "minmax"


class FirmScorecard(TypedDict):
    firm_type: str
    raw: MetricValues
    normalized: MetricValues
    overall: float


class ScorecardResults(TypedDict):
    firms: dict[str, FirmScorecard]
    industry: IndustrySummary


@dataclass(frozen=True)
class ScorecardParams:
    perspectives: tuple[str, ...]
    perspective_weights: Mapping[str, float]
    raw_metric_fields: Mapping[str, str]
    raw_metric_scales: Mapping[str, float]
    normalization_method: str
    team_firm_type: str
    tie_normalized_score: float


DEFAULT_SCORECARD_PARAMS = ScorecardParams(
    perspectives=("brand_health", "customer_value", "market_position", "financial"),
    perspective_weights=MappingProxyType({
        "brand_health": 0.25,
        "customer_value": 0.25,
        "market_position": 0.25,
        "financial": 0.25,
    }),
    raw_metric_fields=MappingProxyType({
        "brand_health": "eq",
        "customer_value": "growth_segment_share",
        "market_position": "total_market_share",
        "financial": "contribution",
    }),
    raw_metric_scales=MappingProxyType({
        "brand_health": 1.0,
        "customer_value": 100.0,
        "market_position": 100.0,
        "financial": 1.0,
    }),
    normalization_method=SUPPORTED_NORMALIZATION_METHOD,
    team_firm_type="team",
    tie_normalized_score=50.0,
)


def compute_scorecard(
    round_results: Mapping[str, Mapping[str, object]],
    params: ScorecardParams = DEFAULT_SCORECARD_PARAMS,
) -> ScorecardResults:
    """Compute a single-round balanced scorecard from compute_round output."""
    _validate_params(params)
    team_raw_scores = _extract_team_raw_scores(round_results, params)
    normalized_scores = _normalize_by_perspective(team_raw_scores, params)

    firms = {
        firm_name: {
            "firm_type": params.team_firm_type,
            "raw": team_raw_scores[firm_name],
            "normalized": normalized_scores[firm_name],
            "overall": sum(
                normalized_scores[firm_name][perspective] * params.perspective_weights[perspective]
                for perspective in params.perspectives
            ),
        }
        for firm_name in team_raw_scores
    }

    return {
        "firms": firms,
        "industry": _industry_summary(firms, params),
    }


def _validate_params(params: ScorecardParams) -> None:
    perspectives = params.perspectives
    if not perspectives:
        raise ValueError("scorecard perspectives must be nonempty")
    if len(set(perspectives)) != len(perspectives):
        raise ValueError("scorecard perspective names must be unique")

    perspective_set = set(perspectives)
    _validate_mapping_keys(params.perspective_weights, perspective_set, "perspective_weights")
    _validate_mapping_keys(params.raw_metric_fields, perspective_set, "raw_metric_fields")
    _validate_mapping_keys(params.raw_metric_scales, perspective_set, "raw_metric_scales")

    weights = {
        perspective: _finite_number(params.perspective_weights[perspective], f"weight for {perspective}")
        for perspective in perspectives
    }
    for perspective, weight in weights.items():
        if weight < 0.0:
            raise ValueError(f"weight for {perspective} must be nonnegative")
    if abs(sum(weights.values()) - 1.0) > TOLERANCE:
        raise ValueError("scorecard perspective weights must sum to 1.0")

    for perspective in perspectives:
        _finite_number(params.raw_metric_scales[perspective], f"raw metric scale for {perspective}")

    tie_score = _finite_number(params.tie_normalized_score, "tie_normalized_score")
    if tie_score < 0.0 or tie_score > 100.0:
        raise ValueError("tie_normalized_score must be within [0, 100]")

    if params.normalization_method != SUPPORTED_NORMALIZATION_METHOD:
        raise ValueError(f"unsupported scorecard normalization method: {params.normalization_method}")


def _validate_mapping_keys(mapping: Mapping[str, object], expected_keys: set[str], mapping_name: str) -> None:
    actual_keys = set(mapping)
    if actual_keys != expected_keys:
        raise ValueError(f"{mapping_name} must contain exactly the configured perspectives")


def _finite_number(value: object, name: str) -> float:
    try:
        converted = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be numeric and finite") from exc
    if not isfinite(converted):
        raise ValueError(f"{name} must be numeric and finite")
    return converted


def _extract_team_raw_scores(
    round_results: Mapping[str, Mapping[str, object]],
    params: ScorecardParams,
) -> dict[str, MetricValues]:
    team_raw_scores: dict[str, MetricValues] = {}

    for firm_name, firm_result in round_results.items():
        if firm_result.get("firm_type") != params.team_firm_type:
            continue

        team_raw_scores[firm_name] = {
            perspective: _finite_metric(firm_result, field_name, firm_name, perspective)
            * params.raw_metric_scales[perspective]
            for perspective, field_name in params.raw_metric_fields.items()
        }

    if not team_raw_scores:
        raise ValueError("scorecard requires at least one team result")

    return team_raw_scores


def _finite_metric(
    firm_result: Mapping[str, object],
    field_name: str,
    firm_name: str,
    perspective: str,
) -> float:
    if field_name not in firm_result:
        raise ValueError(f"{firm_name} is missing scorecard field for {perspective}: {field_name}")

    try:
        value = float(firm_result[field_name])
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{firm_name}.{field_name} must be a finite number") from exc

    if not isfinite(value):
        raise ValueError(f"{firm_name}.{field_name} must be a finite number")
    return value


def _normalize_by_perspective(
    team_raw_scores: Mapping[str, MetricValues],
    params: ScorecardParams,
) -> dict[str, MetricValues]:
    normalized = {firm_name: {} for firm_name in team_raw_scores}

    for perspective in params.perspectives:
        values = [raw_scores[perspective] for raw_scores in team_raw_scores.values()]
        low = min(values)
        high = max(values)

        for firm_name, raw_scores in team_raw_scores.items():
            if high == low:
                normalized[firm_name][perspective] = params.tie_normalized_score
            else:
                normalized[firm_name][perspective] = (raw_scores[perspective] - low) / (high - low) * 100.0

    return normalized


def _industry_summary(
    firms: Mapping[str, FirmScorecard],
    params: ScorecardParams,
) -> IndustrySummary:
    summary: IndustrySummary = {}
    published_perspectives = ("overall",) + params.perspectives

    for perspective in published_perspectives:
        if perspective == "overall":
            values = [firm_scorecard["overall"] for firm_scorecard in firms.values()]
        else:
            values = [firm_scorecard["normalized"][perspective] for firm_scorecard in firms.values()]

        summary[perspective] = {
            "best": max(values),
            "average": sum(values) / len(values),
            "worst": min(values),
        }

    return summary
