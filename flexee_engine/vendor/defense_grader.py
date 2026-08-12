from __future__ import annotations

import json
from collections.abc import MutableMapping, Mapping, Sequence
from copy import deepcopy
from hashlib import sha256
from math import isfinite
from typing import Any

from live_voice import MockModelClient, ModelClient


PROSE_DIMENSIONS = (
    "argument_coherence",
    "honest_evidence_citation",
    "ownership_of_tradeoffs",
)
SEGS = ("LEG", "FIT", "FUN", "GENZ", "MAIN")
REPORT_NOTE = "reported separately; faculty weight them"


def _packet_facts(packet: Mapping[str, Any]) -> dict[str, object]:
    if not isinstance(packet, Mapping):
        raise ValueError("packet must be a mapping")
    focus_group_evidence = packet.get("focus_group_evidence", [])
    decisions = _packet_decisions(packet)
    emphasis_totals = [0.0 for _ in SEGS]
    valid_emphasis_count = 0
    for decision in decisions:
        emphasis = _emphasis_or_none(decision.get("emphasis") if isinstance(decision, Mapping) else None)
        if emphasis is None:
            continue
        valid_emphasis_count += 1
        for index, value in enumerate(emphasis):
            emphasis_totals[index] += value
    dominant_emphasis = None
    if valid_emphasis_count:
        dominant_emphasis = SEGS[max(range(len(SEGS)), key=lambda index: emphasis_totals[index])]
    return {
        "n_focus_groups": _safe_len(focus_group_evidence),
        "n_decisions": len(decisions),
        "dominant_emphasis": dominant_emphasis,
        "final_overall": _final_overall(packet),
    }


def build_grading_prompt(defense_text, packet) -> tuple[str, str]:
    if not isinstance(defense_text, str) or defense_text == "":
        raise ValueError("defense_text must be a nonempty string")
    if not isinstance(packet, Mapping):
        raise ValueError("packet must be a mapping")
    facts = _packet_facts(packet)
    dominant_emphasis = facts["dominant_emphasis"] if facts["dominant_emphasis"] is not None else "none"
    final_overall = facts["final_overall"] if facts["final_overall"] is not None else "unavailable"
    system = (
        "You are a written-defense grader. Score ONLY these three dimensions from 0 to 100: "
        "argument_coherence, honest_evidence_citation, ownership_of_tradeoffs. "
        "Return STRICT JSON with exactly "
        '{"argument_coherence":int,"honest_evidence_citation":int,'
        '"ownership_of_tradeoffs":int,"justification":str} and no prose outside the JSON. '
        "argument_coherence means the case is logical and well-structured. "
        "honest_evidence_citation means claims match the factual record, the team gathered and cited "
        "the evidence they invoke, and the described strategy matches what they actually did. "
        "ownership_of_tradeoffs means they acknowledge what they gave up and the risks they took."
    )
    user = (
        "FACTUAL RECORD: "
        f"focus groups run={facts['n_focus_groups']}; "
        f"decisions recorded={facts['n_decisions']}; "
        f"emphasized most={dominant_emphasis}; "
        f"final overall={final_overall}. "
        "Judge honest_evidence_citation by comparing the student's claims to that factual record. "
        f"STUDENT DEFENSE: {defense_text}"
    )
    return system, user


def grade_written_defense(
    defense_text,
    packet,
    model_client: ModelClient,
    cache: dict[str, dict[str, object]] | None = None,
) -> dict[str, object]:
    if not callable(model_client):
        raise ValueError("model_client must be callable")
    if not isinstance(packet, Mapping):
        raise ValueError("packet must be a mapping")
    facts = _packet_facts(packet)
    key = _cache_key(defense_text, facts)
    cache_mapping = _validate_cache(cache)
    if cache_mapping is not None and key in cache_mapping:
        cached_result = _validate_cached_record(cache_mapping[key])
        return {**cached_result, "cache_key": key, "cached": True}

    system, user = build_grading_prompt(defense_text, packet)
    response = model_client(system, user, 0.0)
    if not isinstance(response, str):
        raise ValueError("model_client must return a string")
    parsed = _parse_grade_response(response)
    prose_dimensions = {dimension: parsed[dimension] for dimension in PROSE_DIMENSIONS}
    result = {
        "prose_dimensions": prose_dimensions,
        "prose_overall": sum(prose_dimensions.values()) / len(PROSE_DIMENSIONS),
        "justification": parsed["justification"],
    }
    if cache_mapping is not None:
        try:
            cache_mapping[key] = deepcopy(result)
        except Exception as exc:
            raise ValueError("cache must allow item assignment") from exc
    return {**deepcopy(result), "cache_key": key, "cached": False}


def combine_report(deterministic_defense, prose_grade) -> dict[str, object]:
    if not isinstance(deterministic_defense, Mapping):
        raise ValueError("deterministic_defense must be a mapping")
    if not isinstance(prose_grade, Mapping):
        raise ValueError("prose_grade must be a mapping")
    return {
        "deterministic": deepcopy(deterministic_defense),
        "prose": deepcopy(prose_grade),
        "note": REPORT_NOTE,
    }


def _packet_decisions(packet: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    compact = packet.get("decisions")
    if _is_non_string_sequence(compact):
        return tuple(item for item in compact if isinstance(item, Mapping))
    trajectory = packet.get("decision_trajectory")
    if _is_non_string_sequence(trajectory):
        decisions = []
        for item in trajectory:
            if isinstance(item, Mapping) and isinstance(item.get("decision"), Mapping):
                decisions.append(item["decision"])
        return tuple(decisions)
    return ()


def _final_overall(packet: Mapping[str, Any]) -> object:
    compact_scorecard = packet.get("final_scorecard")
    if isinstance(compact_scorecard, Mapping):
        return compact_scorecard.get("overall")
    scorecard_arc = packet.get("scorecard_arc")
    if _is_non_string_sequence(scorecard_arc):
        for item in reversed(tuple(scorecard_arc)):
            if isinstance(item, Mapping) and isinstance(item.get("scorecard"), Mapping):
                return item["scorecard"].get("overall")
    return None


def _emphasis_or_none(value: object) -> tuple[float, ...] | None:
    if not _is_non_string_sequence(value) or len(value) != len(SEGS):  # type: ignore[arg-type]
        return None
    emphasis = []
    for item in value:  # type: ignore[union-attr]
        try:
            converted = float(item)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return None
        if not isfinite(converted):
            return None
        emphasis.append(converted)
    return tuple(emphasis)


def _safe_len(value: object) -> int:
    if isinstance(value, Mapping):
        return len(value)
    if _is_non_string_sequence(value):
        return len(value)  # type: ignore[arg-type]
    return 0


def _cache_key(defense_text: str, facts: Mapping[str, object]) -> str:
    payload = {
        "defense_text": defense_text,
        "facts": dict(facts),
    }
    return sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def _parse_grade_response(response: str) -> dict[str, object]:
    try:
        parsed = json.loads(response, parse_constant=_reject_constant)
    except json.JSONDecodeError as exc:
        raise ValueError("model_client must return valid JSON") from exc
    if not isinstance(parsed, dict):
        raise ValueError("model_client JSON must be an object")
    expected = set(PROSE_DIMENSIONS) | {"justification"}
    if set(parsed) != expected:
        raise ValueError("model_client JSON must contain exactly the prose dimensions and justification")
    scores: dict[str, int] = {}
    for dimension in PROSE_DIMENSIONS:
        value = parsed[dimension]
        if not isinstance(value, int) or isinstance(value, bool):
            raise ValueError(f"{dimension} must be an integer")
        if value < 0 or value > 100:
            raise ValueError(f"{dimension} must be within [0, 100]")
        scores[dimension] = value
    justification = parsed["justification"]
    _validate_justification(justification)
    return {**scores, "justification": justification}


def _reject_constant(value: str) -> None:
    raise ValueError(f"nonstandard JSON constant: {value}")


def _validate_cached_record(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError("cached record must be a mapping")
    if set(value) != {"prose_dimensions", "prose_overall", "justification"}:
        raise ValueError("cached record must contain exactly prose_dimensions, prose_overall, and justification")
    dimensions_value = value["prose_dimensions"]
    if not isinstance(dimensions_value, Mapping):
        raise ValueError("cached prose_dimensions must be a mapping")
    if set(dimensions_value) != set(PROSE_DIMENSIONS):
        raise ValueError("cached prose_dimensions must contain exactly the prose dimensions")
    dimensions = {
        dimension: _validate_dimension_score(dimensions_value[dimension], dimension)
        for dimension in PROSE_DIMENSIONS
    }
    prose_overall = _finite_number(value["prose_overall"], "cached prose_overall")
    expected_overall = sum(dimensions.values()) / len(PROSE_DIMENSIONS)
    if prose_overall != expected_overall:
        raise ValueError("cached prose_overall must equal the mean of prose_dimensions")
    justification = value["justification"]
    _validate_justification(justification)
    return {
        "prose_dimensions": dimensions,
        "prose_overall": prose_overall,
        "justification": justification,
    }


def _validate_cache(cache: object) -> MutableMapping[str, dict[str, object]] | None:
    if cache is None:
        return None
    if not isinstance(cache, MutableMapping):
        raise ValueError("cache must be a mutable mapping or None")
    return cache


def _validate_dimension_score(value: object, dimension: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{dimension} must be an integer")
    if value < 0 or value > 100:
        raise ValueError(f"{dimension} must be within [0, 100]")
    return value


def _validate_justification(value: object) -> None:
    if not isinstance(value, str):
        raise ValueError("justification must be a string")
    if value.strip() == "":
        raise ValueError("justification must be nonempty")


def _finite_number(value: object, label: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"{label} must be a finite number")
    try:
        converted = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be a finite number") from exc
    if not isfinite(converted):
        raise ValueError(f"{label} must be a finite number")
    return converted


def _is_non_string_sequence(value: object) -> bool:
    return not isinstance(value, (str, bytes)) and isinstance(value, Sequence)
