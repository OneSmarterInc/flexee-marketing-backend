from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from math import isfinite
from types import MappingProxyType
from typing import Any

from customer_agents import Persona, revealed_warmth, stated_warmth


SEGS = ("LEG", "FIT", "FUN", "GENZ", "MAIN")
DIMENSIONS = ("market_read", "outcome", "engagement", "coherence")
DEFAULT_WEIGHTS = MappingProxyType({
    "market_read": 0.35,
    "outcome": 0.35,
    "engagement": 0.15,
    "coherence": 0.15,
})
TIE_NORMALIZED_SCORE = 50.0


def build_defense(
    history: Sequence[Mapping[str, Any]],
    decisions_by_round: Sequence[Mapping[str, Mapping[str, Any]]],
    engagement: Mapping[str, Any],
    weights: Mapping[str, Any] = DEFAULT_WEIGHTS,
    live_from: int = 1,
) -> dict[str, dict[str, object]]:
    """Build deterministic board-defense scores from completed game history."""
    history_records = _validate_history(history)
    decision_records = _validate_decisions_by_round(decisions_by_round)
    if len(decision_records) != len(history_records):
        raise ValueError("decisions_by_round length must match history length")
    live_start = _validate_live_from(live_from, len(history_records))
    validated_weights = _validate_weights(weights)
    teams = _team_names(history_records[-1])
    _validate_live_scorecards(history_records, teams, live_start)
    _validate_engagement(engagement, teams)

    raw = {
        team: {
            "market_read": _market_read(team, history_records, decision_records, live_start),
            "outcome": _outcome(team, history_records[-1]),
            "engagement": _finite_number(engagement.get(team, 0.0), f"engagement.{team}"),
            "coherence": _coherence(team, decision_records, live_start),
        }
        for team in teams
    }
    normalized = {
        dimension: normalize_across_cohort({team: raw[team][dimension] for team in teams})
        for dimension in DIMENSIONS
    }
    team_dimensions = {
        team: {
            dimension: normalized[dimension][team]
            for dimension in DIMENSIONS
        }
        for team in teams
    }
    defense_scores = {
        team: sum(team_dimensions[team][dimension] * validated_weights[dimension] for dimension in DIMENSIONS)
        for team in teams
    }
    cohort = _cohort_block(team_dimensions, defense_scores)

    return {
        team: {
            "dimensions": dict(team_dimensions[team]),
            "defense_score": defense_scores[team],
            "rank": 1 + sum(other_score > defense_scores[team] for other_score in defense_scores.values()),
            "n_teams": len(teams),
            "cohort": deepcopy(cohort),
            "raw": dict(raw[team]),
        }
        for team in teams
    }


def assemble_defense_packet(
    team: str,
    history: Sequence[Mapping[str, Any]],
    decisions_by_round: Sequence[Mapping[str, Mapping[str, Any]]],
    focus_group_evidence: Sequence[Mapping[str, Any]] | Mapping[str, Any] | None = None,
    advisor_usage: Sequence[Mapping[str, Any]] | Mapping[str, Any] | None = None,
    live_from: int = 1,
) -> dict[str, object]:
    """Gather board-defense inputs for one team without recomputing any scores."""
    if not isinstance(team, str) or team == "":
        raise ValueError("team must be a non-empty string")
    history_records = _validate_history(history)
    decision_records = _validate_decisions_by_round(decisions_by_round)
    if len(decision_records) != len(history_records):
        raise ValueError("decisions_by_round length must match history length")
    live_start = _validate_live_from(live_from, len(history_records))
    for round_number, decisions in enumerate(decision_records[live_start - 1:], start=live_start):
        if team not in decisions:
            raise ValueError(f"round {round_number} decisions are missing team {team}")
    final_teams = _team_names(history_records[-1])
    if team not in final_teams:
        raise ValueError(f"history scorecard is missing team {team}")
    for index, record in enumerate(history_records[live_start - 1:], start=live_start):
        scorecard_firms = _mapping_field(_mapping_field(record, "scorecard", f"round {index}"), "firms", f"round {index}.scorecard")
        if team not in scorecard_firms:
            raise ValueError(f"round {index} scorecard is missing team {team}")

    return {
        "team": team,
        "decision_trajectory": [
            {
                "round": round_number,
                "decision": deepcopy(decisions[team]),
            }
            for round_number, decisions in enumerate(decision_records[live_start - 1:], start=live_start)
        ],
        "focus_group_evidence": deepcopy(_team_evidence(team, focus_group_evidence, "focus_group_evidence")),
        "advisor_usage": deepcopy(_team_evidence(team, advisor_usage, "advisor_usage")),
        "scorecard_arc": [
            {
                "round": _round_number(record, index),
                "scorecard": deepcopy(record["scorecard"]["firms"][team]),
            }
            for index, record in enumerate(history_records[live_start - 1:], start=live_start)
        ],
    }


def _market_read(
    team: str,
    history: Sequence[Mapping[str, Any]],
    decisions_by_round: Sequence[Mapping[str, Mapping[str, Any]]],
    live_start: int,
) -> float:
    values: list[float] = []
    for round_index in range(live_start - 1, len(history)):
        record = history[round_index]
        decisions = decisions_by_round[round_index]
        if team not in decisions:
            raise ValueError(f"round {round_index + 1} decisions are missing team {team}")
        emphasis = _emphasis(decisions[team].get("emphasis"), f"round {round_index + 1}.{team}.emphasis")
        perception_by_firm = _perception_by_firm(record, round_index + 1)
        values.append(sum(
            emphasis[index] * (
                revealed_warmth(perception_by_firm, team, segment)
                - stated_warmth(
                    revealed_warmth(perception_by_firm, team, segment),
                    Persona(f"{segment.lower()}-defense", segment, segment, 0.0),
                )
            )
            for index, segment in enumerate(SEGS)
        ))
    return sum(values) / len(values)


def _outcome(team: str, final_record: Mapping[str, Any]) -> float:
    firms = _mapping_field(_mapping_field(final_record, "scorecard", "final record"), "firms", "final scorecard")
    if team not in firms:
        raise ValueError(f"final scorecard is missing team {team}")
    firm_scorecard = firms[team]
    if not isinstance(firm_scorecard, Mapping):
        raise ValueError(f"final scorecard for {team} must be a mapping")
    return _finite_number(firm_scorecard.get("overall"), f"final scorecard.{team}.overall")


def _coherence(
    team: str,
    decisions_by_round: Sequence[Mapping[str, Mapping[str, Any]]],
    live_start: int,
) -> float:
    changes: list[float] = []
    for round_index in range(max(1, live_start - 1), len(decisions_by_round)):
        current = _positioning(decisions_by_round[round_index], team, round_index + 1)
        previous = _positioning(decisions_by_round[round_index - 1], team, round_index)
        changes.append(abs(current - previous))
    if not changes:
        return -0.0
    return -(sum(changes) / len(changes))


def _positioning(round_decisions: Mapping[str, Mapping[str, Any]], team: str, round_number: int) -> float:
    if team not in round_decisions:
        raise ValueError(f"round {round_number} decisions are missing team {team}")
    decision = round_decisions[team]
    if not isinstance(decision, Mapping):
        raise ValueError(f"round {round_number}.{team} decision must be a mapping")
    if "positioning" not in decision:
        raise ValueError(f"round {round_number}.{team} decision is missing positioning")
    return _finite_number(decision["positioning"], f"round {round_number}.{team}.positioning")


def normalize_across_cohort(values_by_team: Mapping[str, Any]) -> dict[str, float]:
    if not isinstance(values_by_team, Mapping):
        raise ValueError("values_by_team must be a mapping")
    if not values_by_team:
        raise ValueError("values_by_team must be nonempty")
    values: dict[str, float] = {}
    for team, value in values_by_team.items():
        if not isinstance(team, str) or team == "":
            raise ValueError("values_by_team team names must be non-empty strings")
        values[team] = _finite_number(value, f"values_by_team.{team}")

    low = min(values.values())
    high = max(values.values())
    if high == low:
        return {team: TIE_NORMALIZED_SCORE for team in values}
    return {
        team: (value - low) / (high - low) * 100.0
        for team, value in values.items()
    }


def _normalize_dimension(values: Mapping[str, float]) -> dict[str, float]:
    return normalize_across_cohort(values)


def _cohort_block(
    dimensions: Mapping[str, Mapping[str, float]],
    defense_scores: Mapping[str, float],
) -> dict[str, dict[str, float]]:
    cohort = {
        "defense_score": _summary(defense_scores.values()),
    }
    for dimension in DIMENSIONS:
        cohort[dimension] = _summary(team_dimensions[dimension] for team_dimensions in dimensions.values())
    return cohort


def _summary(values_iterable: Any) -> dict[str, float]:
    values = list(values_iterable)
    return {
        "best": max(values),
        "average": sum(values) / len(values),
        "worst": min(values),
    }


def _team_names(final_record: Mapping[str, Any]) -> tuple[str, ...]:
    firms = _mapping_field(_mapping_field(final_record, "scorecard", "final record"), "firms", "final scorecard")
    teams: list[str] = []
    for team, scorecard in firms.items():
        if not isinstance(team, str) or team == "":
            raise ValueError("scorecard team names must be non-empty strings")
        if not isinstance(scorecard, Mapping):
            raise ValueError(f"scorecard for {team} must be a mapping")
        teams.append(team)
    if not teams:
        raise ValueError("final scorecard must contain at least one team")
    return tuple(teams)


def _validate_live_scorecards(
    history: Sequence[Mapping[str, Any]],
    teams: Sequence[str],
    live_start: int,
) -> None:
    for index, record in enumerate(history[live_start - 1:], start=live_start):
        scorecard_firms = _mapping_field(
            _mapping_field(record, "scorecard", f"round {index}"),
            "firms",
            f"round {index}.scorecard",
        )
        for team in teams:
            if team not in scorecard_firms:
                raise ValueError(f"round {index} scorecard is missing team {team}")
            if not isinstance(scorecard_firms[team], Mapping):
                raise ValueError(f"round {index} scorecard for {team} must be a mapping")


def _perception_by_firm(record: Mapping[str, Any], round_number: int) -> dict[str, Mapping[str, Any]]:
    firms = _mapping_field(record, "firms", f"round {round_number}")
    perception: dict[str, Mapping[str, Any]] = {}
    for firm_name, firm_result in firms.items():
        if not isinstance(firm_name, str) or firm_name == "":
            raise ValueError(f"round {round_number} firm names must be non-empty strings")
        if not isinstance(firm_result, Mapping):
            raise ValueError(f"round {round_number}.{firm_name} must be a mapping")
        perception[firm_name] = _mapping_field(firm_result, "perception", f"round {round_number}.{firm_name}")
    return perception


def _validate_history(history: Sequence[Mapping[str, Any]]) -> tuple[Mapping[str, Any], ...]:
    if isinstance(history, (str, bytes)) or not isinstance(history, Sequence):
        raise ValueError("history must be a sequence")
    if not history:
        raise ValueError("history must be nonempty")
    records = tuple(history)
    seen_rounds: set[int] = set()
    for index, record in enumerate(records, start=1):
        if not isinstance(record, Mapping):
            raise ValueError(f"history record {index} must be a mapping")
        round_number = _round_number(record, index)
        if round_number in seen_rounds:
            raise ValueError(f"duplicate round number: {round_number}")
        seen_rounds.add(round_number)
        _mapping_field(record, "firms", f"round {round_number}")
        _mapping_field(_mapping_field(record, "scorecard", f"round {round_number}"), "firms", f"round {round_number}.scorecard")
    return records


def _validate_decisions_by_round(
    decisions_by_round: Sequence[Mapping[str, Mapping[str, Any]]],
) -> tuple[Mapping[str, Mapping[str, Any]], ...]:
    if isinstance(decisions_by_round, (str, bytes)) or not isinstance(decisions_by_round, Sequence):
        raise ValueError("decisions_by_round must be a sequence")
    if not decisions_by_round:
        raise ValueError("decisions_by_round must be nonempty")
    records = tuple(decisions_by_round)
    for round_number, decisions in enumerate(records, start=1):
        if not isinstance(decisions, Mapping):
            raise ValueError(f"round {round_number} decisions must be a mapping")
        for team, decision in decisions.items():
            if not isinstance(team, str) or team == "":
                raise ValueError(f"round {round_number} team names must be non-empty strings")
            if not isinstance(decision, Mapping):
                raise ValueError(f"round {round_number}.{team} decision must be a mapping")
            _emphasis(decision.get("emphasis"), f"round {round_number}.{team}.emphasis")
            _positioning(decisions, team, round_number)
    return records


def _validate_engagement(engagement: Mapping[str, Any], teams: Sequence[str]) -> None:
    if not isinstance(engagement, Mapping):
        raise ValueError("engagement must be a mapping")
    allowed_teams = set(teams)
    for team, value in engagement.items():
        if not isinstance(team, str) or team == "":
            raise ValueError("engagement team names must be non-empty strings")
        if team not in allowed_teams:
            raise ValueError(f"engagement contains unknown team {team}")
        engagement_value = _finite_number(value, f"engagement.{team}")
        if engagement_value < 0.0:
            raise ValueError(f"engagement.{team} cannot be negative")


def _validate_weights(weights: Mapping[str, Any]) -> dict[str, float]:
    if not isinstance(weights, Mapping):
        raise ValueError("weights must be a mapping")
    if set(weights) != set(DIMENSIONS):
        raise ValueError("weights must contain exactly the defense dimensions")
    return {dimension: _finite_number(weights[dimension], f"weights.{dimension}") for dimension in DIMENSIONS}


def _validate_live_from(live_from: int, history_length: int) -> int:
    if not isinstance(live_from, int) or isinstance(live_from, bool):
        raise ValueError("live_from must be an integer")
    if live_from < 1 or live_from > history_length:
        raise ValueError("live_from must be within the history range")
    return live_from


def _round_number(record: Mapping[str, Any], index: int) -> int:
    if "round" not in record:
        raise ValueError(f"history record {index} is missing round")
    round_number = record["round"]
    if not isinstance(round_number, int) or isinstance(round_number, bool):
        raise ValueError(f"history record {index} round must be an integer")
    return round_number


def _mapping_field(mapping: Mapping[str, Any], field: str, context: str) -> Mapping[str, Any]:
    if field not in mapping or not isinstance(mapping[field], Mapping):
        raise ValueError(f"{context} must contain mapping field {field}")
    return mapping[field]


def _emphasis(value: Any, label: str) -> tuple[float, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValueError(f"{label} must be a sequence")
    if len(value) != len(SEGS):
        raise ValueError(f"{label} must contain five values")
    emphasis = tuple(_finite_number(item, f"{label}.{segment}") for item, segment in zip(value, SEGS, strict=True))
    for item in emphasis:
        if item < 0.0:
            raise ValueError(f"{label} values cannot be negative")
    if abs(sum(emphasis) - 1.0) > 1e-9:
        raise ValueError(f"{label} values must sum to 1.0")
    return emphasis


def _team_evidence(team: str, evidence: Sequence[Mapping[str, Any]] | Mapping[str, Any] | None, label: str) -> object:
    if evidence is None:
        return []
    if isinstance(evidence, Mapping):
        if team in evidence:
            return evidence[team]
        tagged_team = evidence.get("team_id", evidence.get("team"))
        if tagged_team is not None:
            return evidence if tagged_team == team else []
        return evidence
    if isinstance(evidence, (str, bytes)) or not isinstance(evidence, Sequence):
        raise ValueError(f"{label} must be a mapping, sequence, or None")
    filtered = []
    for index, item in enumerate(evidence, start=1):
        if not isinstance(item, Mapping):
            raise ValueError(f"{label} item {index} must be a mapping")
        tagged_team = item.get("team_id", item.get("team"))
        if tagged_team is None or tagged_team == team:
            filtered.append(item)
    return filtered


def _finite_number(value: object, label: str) -> float:
    try:
        converted = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be a finite number") from exc
    if not isfinite(converted):
        raise ValueError(f"{label} must be a finite number")
    return converted
