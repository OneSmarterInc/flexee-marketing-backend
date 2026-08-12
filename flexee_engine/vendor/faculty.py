from __future__ import annotations

from collections.abc import Mapping as MappingABC
from collections.abc import Sequence as SequenceABC
from copy import deepcopy
from dataclasses import dataclass
from math import isfinite
from types import MappingProxyType
from typing import Mapping, Sequence, TypedDict

from engine import DEFAULT_PARAMS, DecisionValue, SimulationParams, compute_round
from equity import DEFAULT_EQUITY_PARAMS, EquityParams
from promotion import DEFAULT_PROMOTION_PARAMS, PromotionParams, run_simulation_with_promotion
from simulation import DEFAULT_GROWTH_RATES


LEVER_FIELDS = MappingProxyType({
    "allocation": ("brand", "perf", "research"),
    "emphasis": ("emphasis",),
    "positioning": ("positioning",),
    "promotion": ("promotion",),
    "price": ("price",),
})
DECISION_FIELDS = frozenset(field for fields in LEVER_FIELDS.values() for field in fields)
FIELD_TO_LEVER = MappingProxyType({
    field: lever
    for lever, fields in LEVER_FIELDS.items()
    for field in fields
})
ALLOCATION_FIELDS = frozenset(LEVER_FIELDS["allocation"])
TOLERANCE = 1e-12


@dataclass(frozen=True)
class FacultyConfig:
    num_rounds: int
    takeover_round: int
    lever_open_round: Mapping[str, int]
    baseline_decision: Mapping[str, object]


DEFAULT_FACULTY_CONFIG = FacultyConfig(
    num_rounds=14,
    takeover_round=4,
    lever_open_round=MappingProxyType({
        "allocation": 4,
        "emphasis": 4,
        "positioning": 5,
        "promotion": 6,
        "price": 8,
    }),
    baseline_decision=MappingProxyType({
        "brand": 5.0,
        "perf": 2.0,
        "research": 3.0,
        "positioning": 35.0,
        "emphasis": (0.20, 0.20, 0.20, 0.20, 0.20),
        "price": 1.50,
        "promotion": 0.0,
    }),
)


RoundSubmissions = Mapping[str, Mapping[str, DecisionValue]]
EffectiveRound = dict[str, dict[str, object]]


class FacultyRoundRecord(TypedDict):
    round: int
    segment_sizes: dict[str, float]
    firms: dict[str, object]
    scorecard: dict[str, object]
    equity: dict[str, float]
    deal_conditioning: dict[str, float]
    promotion_depth: dict[str, float]
    open_levers: frozenset[str]
    effective_decisions: EffectiveRound


def open_levers_for_round(
    round_number: int,
    faculty_config: FacultyConfig = DEFAULT_FACULTY_CONFIG,
    params: SimulationParams = DEFAULT_PARAMS,
) -> frozenset[str]:
    _validate_faculty_config(faculty_config, params)
    if not isinstance(round_number, int):
        raise ValueError("round_number must be an integer")
    if round_number < 1:
        raise ValueError("round_number must be at least 1")
    if round_number < faculty_config.takeover_round:
        return frozenset()
    return frozenset(
        lever
        for lever, open_round in faculty_config.lever_open_round.items()
        if open_round <= round_number <= faculty_config.num_rounds
    )


def build_effective_decisions(
    team_names: Sequence[str],
    submissions_by_round: Sequence[RoundSubmissions],
    faculty_config: FacultyConfig = DEFAULT_FACULTY_CONFIG,
    params: SimulationParams = DEFAULT_PARAMS,
) -> list[EffectiveRound]:
    _validate_faculty_config(faculty_config, params)
    ordered_team_names = _validate_team_names(team_names, params)
    _validate_submissions_sequence(submissions_by_round, faculty_config)

    baseline = _baseline_copy(faculty_config)
    effective_rounds: list[EffectiveRound] = []

    for round_number, round_submissions in enumerate(submissions_by_round, start=1):
        if not isinstance(round_submissions, MappingABC):
            raise ValueError(f"round {round_number} submissions must be a mapping")

        normalized_submissions = _validate_round_submissions(
            round_submissions,
            ordered_team_names,
            round_number,
        )
        round_effective = {
            team_name: deepcopy(baseline)
            for team_name in ordered_team_names
        }

        if round_number >= faculty_config.takeover_round:
            for team_name in ordered_team_names:
                submission = normalized_submissions.get(team_name, {})
                _apply_submission(
                    round_effective[team_name],
                    submission,
                    round_number,
                    faculty_config,
                    baseline,
                )

        _validate_effective_round(round_effective, params)
        effective_rounds.append({
            team_name: deepcopy(decision)
            for team_name, decision in round_effective.items()
        })

    return effective_rounds


def run_simulation_with_faculty(
    team_names: Sequence[str],
    submissions_by_round: Sequence[RoundSubmissions],
    faculty_config: FacultyConfig = DEFAULT_FACULTY_CONFIG,
    params: SimulationParams = DEFAULT_PARAMS,
    growth_rates: Mapping[str, float] = DEFAULT_GROWTH_RATES,
    equity_params: EquityParams = DEFAULT_EQUITY_PARAMS,
    promotion_params: PromotionParams = DEFAULT_PROMOTION_PARAMS,
) -> list[FacultyRoundRecord]:
    _validate_faculty_config(faculty_config, params)
    effective_decisions_by_round = build_effective_decisions(
        team_names,
        submissions_by_round,
        faculty_config,
        params,
    )
    promotion_history = run_simulation_with_promotion(
        effective_decisions_by_round,
        params,
        growth_rates,
        equity_params,
        promotion_params,
    )

    return [
        {
            **record,
            "open_levers": open_levers_for_round(record["round"], faculty_config, params),
            "effective_decisions": deepcopy(effective_decisions_by_round[record["round"] - 1]),
        }
        for record in promotion_history
    ]


def _validate_faculty_config(faculty_config: FacultyConfig, params: SimulationParams) -> None:
    if not isinstance(faculty_config.num_rounds, int):
        raise ValueError("num_rounds must be an integer")
    if faculty_config.num_rounds < 1:
        raise ValueError("num_rounds must be at least 1")
    if not isinstance(faculty_config.takeover_round, int):
        raise ValueError("takeover_round must be an integer")
    if faculty_config.takeover_round < 1:
        raise ValueError("takeover_round must be at least 1")
    if faculty_config.takeover_round > faculty_config.num_rounds + 1:
        raise ValueError("takeover_round must be no greater than num_rounds + 1")

    if not isinstance(faculty_config.lever_open_round, MappingABC):
        raise ValueError("lever_open_round must be a mapping")
    expected_levers = set(LEVER_FIELDS)
    actual_levers = set(faculty_config.lever_open_round)
    missing = expected_levers - actual_levers
    extra = actual_levers - expected_levers
    if missing:
        raise ValueError(f"lever_open_round missing required lever: {sorted(missing)[0]}")
    if extra:
        raise ValueError(f"lever_open_round contains unknown lever: {sorted(extra)[0]}")
    for lever, open_round in faculty_config.lever_open_round.items():
        if not isinstance(open_round, int):
            raise ValueError(f"{lever} open round must be an integer")
        if open_round < 1:
            raise ValueError(f"{lever} open round must be at least 1")

    if not isinstance(faculty_config.baseline_decision, MappingABC):
        raise ValueError("baseline_decision must be a mapping")
    baseline_keys = set(faculty_config.baseline_decision)
    missing_fields = DECISION_FIELDS - baseline_keys
    extra_fields = baseline_keys - DECISION_FIELDS
    if missing_fields:
        raise ValueError(f"baseline_decision missing required field: {sorted(missing_fields)[0]}")
    if extra_fields:
        raise ValueError(f"baseline_decision contains unknown field: {sorted(extra_fields)[0]}")
    _validate_effective_round({"__baseline__": _baseline_copy(faculty_config)}, params)


def _validate_team_names(team_names: Sequence[str], params: SimulationParams) -> tuple[str, ...]:
    if isinstance(team_names, (str, bytes)) or not isinstance(team_names, SequenceABC):
        raise ValueError("team_names must be a non-string sequence")
    if not team_names:
        raise ValueError("team_names must be nonempty")

    seen: set[str] = set()
    ordered: list[str] = []
    for team_name in team_names:
        if not isinstance(team_name, str) or team_name == "":
            raise ValueError("team names must be non-empty strings")
        if team_name in seen:
            raise ValueError(f"duplicate team name: {team_name}")
        if team_name in params.backdrop_firms:
            raise ValueError(f"team name conflicts with configured backdrop firm: {team_name}")
        seen.add(team_name)
        ordered.append(team_name)
    return tuple(ordered)


def _validate_submissions_sequence(
    submissions_by_round: Sequence[RoundSubmissions],
    faculty_config: FacultyConfig,
) -> None:
    if isinstance(submissions_by_round, (str, bytes)) or not isinstance(submissions_by_round, SequenceABC):
        raise ValueError("submissions_by_round must be a non-string sequence")
    if len(submissions_by_round) != faculty_config.num_rounds:
        raise ValueError("submissions_by_round length must equal num_rounds")


def _validate_round_submissions(
    round_submissions: RoundSubmissions,
    team_names: tuple[str, ...],
    round_number: int,
) -> dict[str, Mapping[str, DecisionValue]]:
    team_set = set(team_names)
    validated: dict[str, Mapping[str, DecisionValue]] = {}
    for team_name, submission in round_submissions.items():
        if team_name not in team_set:
            raise ValueError(f"unknown team in round {round_number}: {team_name}")
        if not isinstance(submission, MappingABC):
            raise ValueError(f"submission for {team_name} in round {round_number} must be a mapping")
        unknown_fields = set(submission) - DECISION_FIELDS
        if unknown_fields:
            raise ValueError(f"unknown decision field in round {round_number}: {sorted(unknown_fields)[0]}")
        validated[team_name] = submission
    return validated


def _apply_submission(
    effective_decision: dict[str, object],
    submission: Mapping[str, DecisionValue],
    round_number: int,
    faculty_config: FacultyConfig,
    baseline: Mapping[str, object],
) -> None:
    submitted_allocation = ALLOCATION_FIELDS & set(submission)
    if submitted_allocation and round_number >= faculty_config.lever_open_round["allocation"]:
        if submitted_allocation != ALLOCATION_FIELDS:
            raise ValueError("open allocation submissions must include brand, perf, and research")

    for field_name, value in submission.items():
        lever = FIELD_TO_LEVER[field_name]
        open_round = faculty_config.lever_open_round[lever]
        if round_number >= open_round:
            effective_decision[field_name] = deepcopy(value)
            continue
        if not _same_decision_value(value, baseline[field_name]):
            raise ValueError(f"{lever} is closed in round {round_number}; opens round {open_round}")


def _same_decision_value(value: object, baseline_value: object) -> bool:
    if isinstance(baseline_value, (tuple, list)):
        if isinstance(value, (str, bytes)) or not isinstance(value, SequenceABC):
            return False
        if len(value) != len(baseline_value):  # type: ignore[arg-type]
            return False
        return all(_same_number(item, baseline_item) for item, baseline_item in zip(value, baseline_value))
    if isinstance(baseline_value, (int, float)):
        return _same_number(value, baseline_value)
    return value == baseline_value


def _same_number(value: object, baseline_value: object) -> bool:
    try:
        converted = float(value)  # type: ignore[arg-type]
        baseline_converted = float(baseline_value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False
    if not isfinite(converted) or not isfinite(baseline_converted):
        return False
    return abs(converted - baseline_converted) <= TOLERANCE


def _validate_effective_round(effective_round: EffectiveRound, params: SimulationParams) -> None:
    compute_round(effective_round, params)


def _baseline_copy(faculty_config: FacultyConfig) -> dict[str, object]:
    return deepcopy(dict(faculty_config.baseline_decision))
