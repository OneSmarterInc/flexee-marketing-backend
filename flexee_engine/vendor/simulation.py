from __future__ import annotations

from collections.abc import Iterable
from collections.abc import Mapping as MappingABC
from collections.abc import Sequence as SequenceABC
from dataclasses import replace
from math import isfinite
from types import MappingProxyType
from typing import Mapping, Sequence, TypedDict

from engine import DEFAULT_PARAMS, DecisionValue, Segment, SimulationParams, compute_round
from scorecard import ScorecardResults, compute_scorecard


DEFAULT_GROWTH_RATES = MappingProxyType({
    "LEG": -0.01,
    "FIT": 0.003,
    "FUN": 0.025,
    "GENZ": 0.04,
    "MAIN": 0.005,
})


RoundDecisions = Mapping[str, Mapping[str, DecisionValue]]
SegmentSizes = dict[str, float]


class RoundRecord(TypedDict):
    round: int
    segment_sizes: SegmentSizes
    firms: dict[str, object]
    scorecard: ScorecardResults


SimulationHistory = list[RoundRecord]


def run_simulation(
    decisions_by_round: Sequence[RoundDecisions],
    params: SimulationParams = DEFAULT_PARAMS,
    growth_rates: Mapping[str, float] = DEFAULT_GROWTH_RATES,
) -> SimulationHistory:
    """Run independent single-round simulations while only segment sizes evolve."""
    _validate_decisions_by_round(decisions_by_round)
    base_sizes = _validate_base_segment_sizes(params)
    validated_growth_rates = _validate_growth_rates(base_sizes.keys(), growth_rates)

    history: SimulationHistory = []

    for round_number, round_decisions in enumerate(decisions_by_round, start=1):
        segment_sizes = {
            segment_name: base_size * (1.0 + validated_growth_rates[segment_name]) ** (round_number - 1)
            for segment_name, base_size in base_sizes.items()
        }
        round_params = _params_with_segment_sizes(params, segment_sizes)
        round_firms = compute_round(round_decisions, round_params)
        scorecard = compute_scorecard(round_firms)

        history.append({
            "round": round_number,
            "segment_sizes": segment_sizes,
            "firms": round_firms,
            "scorecard": scorecard,
        })

    return history


def _validate_decisions_by_round(decisions_by_round: Sequence[RoundDecisions]) -> None:
    if isinstance(decisions_by_round, (str, bytes)) or not isinstance(decisions_by_round, SequenceABC):
        raise ValueError("decisions_by_round must be a non-string sequence")
    if not decisions_by_round:
        raise ValueError("decisions_by_round must contain at least one round")
    for index, round_decisions in enumerate(decisions_by_round, start=1):
        if not isinstance(round_decisions, MappingABC):
            raise ValueError(f"round {index} decisions must be a mapping")


def _validate_base_segment_sizes(params: SimulationParams) -> dict[str, float]:
    base_sizes: dict[str, float] = {}

    for segment in params.segments:
        if segment.name in base_sizes:
            raise ValueError(f"configured segment names must be unique: {segment.name}")
        try:
            size = float(segment.size)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"segment size for {segment.name} must be numeric") from exc
        if not isfinite(size):
            raise ValueError(f"segment size for {segment.name} must be finite")
        if size < 0.0:
            raise ValueError(f"segment size for {segment.name} cannot be negative")
        base_sizes[segment.name] = size

    return base_sizes


def _params_with_segment_sizes(
    params: SimulationParams,
    segment_sizes: Mapping[str, float],
) -> SimulationParams:
    return replace(
        params,
        segments=tuple(
            Segment(
                name=segment.name,
                size=segment_sizes[segment.name],
                positioning_ideal=segment.positioning_ideal,
                beta=segment.beta,
                weights=segment.weights,
            )
            for segment in params.segments
        ),
    )


def _validate_growth_rates(
    segment_names: Iterable[str],
    growth_rates: Mapping[str, float],
) -> dict[str, float]:
    if not isinstance(growth_rates, MappingABC):
        raise ValueError("growth_rates must be a mapping")

    expected_segments = set(segment_names)
    if set(growth_rates) != expected_segments:
        raise ValueError("growth_rates must contain exactly the configured segment names")

    validated: dict[str, float] = {}
    for segment_name, growth_rate in growth_rates.items():
        try:
            converted = float(growth_rate)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"growth rate for {segment_name} must be numeric") from exc
        if not isfinite(converted):
            raise ValueError(f"growth rate for {segment_name} must be finite")
        if converted <= -1.0:
            raise ValueError(f"growth rate for {segment_name} must be greater than -1.0")
        validated[segment_name] = converted

    return validated
