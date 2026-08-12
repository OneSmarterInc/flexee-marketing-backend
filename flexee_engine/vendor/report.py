from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from math import isfinite
from typing import Any


DIMENSIONS = ("brand_health", "customer_value", "market_position", "financial")


def build_report(history: Sequence[Mapping[str, Any]]) -> list[dict[str, object]]:
    """Build anonymized per-team scorecard reports from completed round history."""
    if isinstance(history, (str, bytes)) or not isinstance(history, Sequence):
        raise ValueError("history must be a sequence of round records")
    if not history:
        raise ValueError("history must contain at least one round record")

    report: list[dict[str, object]] = []
    seen_rounds: set[int] = set()
    for record_index, record in enumerate(history, start=1):
        if not isinstance(record, Mapping):
            raise ValueError(f"history record {record_index} must be a mapping")
        round_number = _round_number(record, record_index)
        if round_number in seen_rounds:
            raise ValueError(f"duplicate round number: {round_number}")
        seen_rounds.add(round_number)
        scorecard = _mapping_field(record, "scorecard", f"round {round_number}")
        firms = _mapping_field(scorecard, "firms", f"round {round_number}.scorecard")
        if not firms:
            raise ValueError(f"round {round_number} scorecard must contain at least one team")

        team_values = {
            _team_name(team_name, round_number): _team_score(
                firm_scorecard,
                _team_name(team_name, round_number),
                round_number,
            )
            for team_name, firm_scorecard in firms.items()
        }
        overalls = [team_score["overall"] for team_score in team_values.values()]
        cohort_overall = {
            "best": max(overalls),
            "average": sum(overalls) / len(overalls),
            "worst": min(overalls),
        }
        cohort_dimension_average = {
            dimension: sum(team_score["normalized"][dimension] for team_score in team_values.values())
            / len(team_values)
            for dimension in DIMENSIONS
        }

        teams = {
            team_name: {
                "own": {
                    "overall": team_score["overall"],
                    "normalized": deepcopy(team_score["normalized"]),
                },
                "cohort_overall": dict(cohort_overall),
                "cohort_dimension_average": dict(cohort_dimension_average),
                "rank": 1 + sum(
                    other_score["overall"] > team_score["overall"]
                    for other_score in team_values.values()
                ),
                "n_teams": len(team_values),
            }
            for team_name, team_score in team_values.items()
        }
        report.append({"round": round_number, "teams": teams})

    return report


def _round_number(record: Mapping[str, Any], record_index: int) -> int:
    if "round" not in record:
        raise ValueError(f"history record {record_index} is missing round")
    round_number = record["round"]
    if not isinstance(round_number, int) or isinstance(round_number, bool):
        raise ValueError(f"history record {record_index} round must be an integer")
    return round_number


def _team_name(team_name: Any, round_number: int) -> str:
    if not isinstance(team_name, str) or team_name == "":
        raise ValueError(f"round {round_number} team names must be non-empty strings")
    return team_name


def _team_score(firm_scorecard: Any, team_name: str, round_number: int) -> dict[str, Any]:
    if not isinstance(firm_scorecard, Mapping):
        raise ValueError(f"round {round_number} scorecard for {team_name} must be a mapping")
    normalized = _mapping_field(firm_scorecard, "normalized", f"round {round_number}.{team_name}")
    normalized_keys = set(normalized)
    expected_keys = set(DIMENSIONS)
    if normalized_keys != expected_keys:
        raise ValueError(f"round {round_number}.{team_name}.normalized must contain exactly the required dimensions")
    return {
        "overall": _finite_number(
            firm_scorecard.get("overall"),
            f"round {round_number}.{team_name}.overall",
        ),
        "normalized": {
            dimension: _finite_number(
                normalized.get(dimension),
                f"round {round_number}.{team_name}.normalized.{dimension}",
            )
            for dimension in DIMENSIONS
        },
    }


def _mapping_field(mapping: Mapping[str, Any], field_name: str, context: str) -> Mapping[str, Any]:
    if field_name not in mapping or not isinstance(mapping[field_name], Mapping):
        raise ValueError(f"{context} must contain mapping field {field_name}")
    return mapping[field_name]


def _finite_number(value: Any, field_name: str) -> float:
    try:
        converted = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be a finite number") from exc
    if not isfinite(converted):
        raise ValueError(f"{field_name} must be a finite number")
    return converted
