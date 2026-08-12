from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from math import isfinite
from types import MappingProxyType
from typing import Any


K_PERCEP = 0.02
SAYDO_GAP = MappingProxyType({
    "LEG": 0.20,
    "FIT": 0.00,
    "FUN": -0.20,
    "GENZ": -0.15,
    "MAIN": 0.05,
})
GAP_GAIN = 1.0
REL_ALPHA = 0.30
REL_START = 0.5
SEGMENTS = tuple(SAYDO_GAP)


@dataclass(frozen=True)
class Persona:
    id: str
    name: str
    segment: str
    idiosyncrasy: float


GroundedResponse = dict[str, object]
TextGenerator = Callable[[GroundedResponse], str]


def clip01(value: float) -> float:
    converted = _finite_number(value, "value")
    return min(1.0, max(0.0, converted))


def revealed_warmth(
    perception_by_firm: Mapping[str, Mapping[str, object]],
    firm: str,
    segment: str,
    k_percep: float = K_PERCEP,
) -> float:
    _validate_firm_name(firm)
    _validate_segment(segment)
    k_percep_value = _nonnegative_finite(k_percep, "k_percep")
    firm_perceptions = _validate_perception_by_firm(perception_by_firm, segment)
    if firm not in firm_perceptions:
        raise ValueError(f"unknown firm: {firm}")

    segment_values = [perception[segment] for perception in firm_perceptions.values()]
    segment_mean = sum(segment_values) / len(segment_values)
    return clip01(0.5 + k_percep_value * (firm_perceptions[firm][segment] - segment_mean))


def stated_warmth(revealed: object, persona: Persona, gap_gain: float = GAP_GAIN) -> float:
    validated_persona = _validate_persona(persona)
    revealed_value = _finite_number(revealed, "revealed")
    if revealed_value < 0.0 or revealed_value > 1.0:
        raise ValueError("revealed must be within [0, 1]")
    gap_gain_value = _nonnegative_finite(gap_gain, "gap_gain")
    return clip01(
        revealed_value
        + gap_gain_value * SAYDO_GAP[validated_persona.segment]
        + validated_persona.idiosyncrasy
    )


def update_relationship(
    relationship: object,
    revealed: object,
    rel_alpha: float = REL_ALPHA,
) -> float:
    relationship_value = _finite_number(relationship, "relationship")
    revealed_value = _finite_number(revealed, "revealed")
    alpha_value = _finite_number(rel_alpha, "rel_alpha")
    if relationship_value < 0.0 or relationship_value > 1.0:
        raise ValueError("relationship must be within [0, 1]")
    if revealed_value < 0.0 or revealed_value > 1.0:
        raise ValueError("revealed must be within [0, 1]")
    if alpha_value < 0.0 or alpha_value > 1.0:
        raise ValueError("rel_alpha must be within [0, 1]")
    return clip01(relationship_value + alpha_value * (revealed_value - relationship_value))


def focus_group(
    perception_by_firm: Mapping[str, Mapping[str, object]],
    firm: str,
    personas: Sequence[Persona],
    relationships: Mapping[str, object] | None = None,
    generator: TextGenerator | None = None,
    gap_gain: float = GAP_GAIN,
) -> list[dict[str, object]]:
    persona_list = _validate_personas(personas)
    relationship_values = _validate_relationships(relationships, persona_list)
    return [
        _student_response(
            "focus_group",
            perception_by_firm,
            firm,
            persona,
            relationship_values[persona.id],
            generator or mock_generator,
            gap_gain,
        )
        for persona in persona_list
    ]


def campaign_response(
    perception_by_firm: Mapping[str, Mapping[str, object]],
    share_by_firm: Mapping[str, Mapping[str, object]],
    firm: str,
    personas: Sequence[Persona],
    relationships: Mapping[str, object] | None = None,
    generator: TextGenerator | None = None,
    gap_gain: float = GAP_GAIN,
) -> list[dict[str, object]]:
    persona_list = _validate_personas(personas)
    relationship_values = _validate_relationships(relationships, persona_list)
    return [
        _student_response(
            "campaign_response",
            perception_by_firm,
            firm,
            persona,
            relationship_values[persona.id],
            generator or mock_generator,
            gap_gain,
            _campaign_strength(share_by_firm, firm, persona.segment),
        )
        for persona in persona_list
    ]


def run_focus_groups_over_history(
    history: Sequence[Mapping[str, Any]],
    firm: str,
    personas: Sequence[Persona],
    relationships: Mapping[str, object] | None = None,
    generator: TextGenerator | None = None,
    gap_gain: float = GAP_GAIN,
    rel_alpha: float = REL_ALPHA,
) -> list[dict[str, object]]:
    if isinstance(history, (str, bytes)) or not isinstance(history, Sequence):
        raise ValueError("history must be a sequence of round records")
    if not history:
        raise ValueError("history must contain at least one round record")
    persona_list = _validate_personas(personas)
    relationship_state = _validate_relationships(relationships, persona_list)

    records: list[dict[str, object]] = []
    seen_rounds: set[int] = set()
    for record_index, record in enumerate(history, start=1):
        if not isinstance(record, Mapping):
            raise ValueError(f"history record {record_index} must be a mapping")
        round_number = _round_number(record, record_index)
        if round_number in seen_rounds:
            raise ValueError(f"duplicate round number: {round_number}")
        seen_rounds.add(round_number)
        perception_by_firm = _perception_from_record(record, round_number)
        relationships_entering = dict(relationship_state)
        responses = focus_group(
            perception_by_firm,
            firm,
            persona_list,
            relationships_entering,
            generator or mock_generator,
            gap_gain,
        )
        relationships_next = {
            persona.id: update_relationship(
                relationships_entering[persona.id],
                revealed_warmth(perception_by_firm, firm, persona.segment),
                rel_alpha,
            )
            for persona in persona_list
        }
        records.append({
            "round": round_number,
            "responses": responses,
            "relationships_entering": dict(relationships_entering),
            "relationships_next": dict(relationships_next),
        })
        relationship_state = relationships_next

    return records


def mock_generator(response: GroundedResponse) -> str:
    mode = str(response["mode"]).replace("_", " ")
    stated = _warmth_label(float(response["stated_warmth"]))
    relationship = _relationship_label(float(response["relationship"]))
    if response["mode"] == "campaign_response":
        strength = _campaign_label(str(response.get("campaign_strength", "steady")))
        return (
            f"{response['persona_name']} sounds {stated} after the {strength} campaign "
            f"and feels {relationship} toward the brand."
        )
    return (
        f"{response['persona_name']} sounds {stated} in this {mode} "
        f"and feels {relationship} toward the brand."
    )


def _student_response(
    mode: str,
    perception_by_firm: Mapping[str, Mapping[str, object]],
    firm: str,
    persona: Persona,
    relationship: float,
    generator: TextGenerator,
    gap_gain: float,
    campaign_strength: str | None = None,
) -> dict[str, object]:
    if not callable(generator):
        raise ValueError("generator must be callable")
    revealed = revealed_warmth(perception_by_firm, firm, persona.segment)
    stated = stated_warmth(revealed, persona, gap_gain)
    grounded = {
        "mode": mode,
        "persona_id": persona.id,
        "persona_name": persona.name,
        "stated_warmth": stated,
        "relationship": relationship,
    }
    if campaign_strength is not None:
        grounded["campaign_strength"] = campaign_strength
    text = generator(dict(grounded))
    if not isinstance(text, str):
        raise ValueError("generator must return text")
    response = {
        key: value
        for key, value in grounded.items()
        if key != "campaign_strength"
    }
    response["text"] = text
    return response


def _perception_from_record(record: Mapping[str, Any], round_number: int) -> Mapping[str, Mapping[str, object]]:
    firms = _mapping_field(record, "firms", f"round {round_number}")
    perception_by_firm: dict[str, Mapping[str, object]] = {}
    for firm_name, firm_result in firms.items():
        if not isinstance(firm_name, str) or firm_name == "":
            raise ValueError(f"round {round_number} firm names must be non-empty strings")
        if not isinstance(firm_result, Mapping):
            raise ValueError(f"round {round_number}.{firm_name} must be a mapping")
        perception_by_firm[firm_name] = _mapping_field(
            firm_result,
            "perception",
            f"round {round_number}.{firm_name}",
        )
    return perception_by_firm


def _validate_perception_by_firm(
    perception_by_firm: Mapping[str, Mapping[str, object]],
    segment: str,
) -> dict[str, dict[str, float]]:
    if not isinstance(perception_by_firm, Mapping) or not perception_by_firm:
        raise ValueError("perception_by_firm must be a nonempty mapping")
    validated: dict[str, dict[str, float]] = {}
    for firm_name, perception in perception_by_firm.items():
        if not isinstance(firm_name, str) or firm_name == "":
            raise ValueError("firm names must be non-empty strings")
        if not isinstance(perception, Mapping):
            raise ValueError(f"perception for {firm_name} must be a mapping")
        if segment not in perception:
            raise ValueError(f"perception for {firm_name} is missing segment {segment}")
        validated[firm_name] = {
            segment: _finite_number(perception[segment], f"{firm_name}.{segment}")
        }
    return validated


def _validate_share_by_firm(
    share_by_firm: Mapping[str, Mapping[str, object]],
    segment: str,
) -> dict[str, dict[str, float]]:
    if not isinstance(share_by_firm, Mapping) or not share_by_firm:
        raise ValueError("share_by_firm must be a nonempty mapping")
    validated: dict[str, dict[str, float]] = {}
    for firm_name, share in share_by_firm.items():
        if not isinstance(firm_name, str) or firm_name == "":
            raise ValueError("firm names must be non-empty strings")
        if not isinstance(share, Mapping):
            raise ValueError(f"share for {firm_name} must be a mapping")
        if segment not in share:
            raise ValueError(f"share for {firm_name} is missing segment {segment}")
        share_value = _finite_number(share[segment], f"{firm_name}.{segment}")
        if share_value < 0.0:
            raise ValueError(f"{firm_name}.{segment} share cannot be negative")
        validated[firm_name] = {segment: share_value}
    return validated


def _campaign_strength(
    share_by_firm: Mapping[str, Mapping[str, object]],
    firm: str,
    segment: str,
) -> str:
    _validate_firm_name(firm)
    _validate_segment(segment)
    firm_shares = _validate_share_by_firm(share_by_firm, segment)
    if firm not in firm_shares:
        raise ValueError(f"unknown firm: {firm}")
    values = [share[segment] for share in firm_shares.values()]
    average = sum(values) / len(values)
    target = firm_shares[firm][segment]
    if target > average * 1.10:
        return "strong"
    if target < average * 0.90:
        return "soft"
    return "steady"


def _validate_personas(personas: Sequence[Persona]) -> tuple[Persona, ...]:
    if isinstance(personas, (str, bytes)) or not isinstance(personas, Sequence):
        raise ValueError("personas must be a sequence")
    if not personas:
        raise ValueError("personas must be nonempty")
    validated = tuple(_validate_persona(persona) for persona in personas)
    ids = [persona.id for persona in validated]
    if len(set(ids)) != len(ids):
        raise ValueError("persona ids must be unique")
    return validated


def _validate_persona(persona: Persona) -> Persona:
    if not isinstance(persona, Persona):
        raise ValueError("personas must contain Persona instances")
    if not isinstance(persona.id, str) or not isinstance(persona.name, str) or persona.id == "" or persona.name == "":
        raise ValueError("persona id and name must be non-empty strings")
    _validate_segment(persona.segment)
    _finite_number(persona.idiosyncrasy, f"{persona.id}.idiosyncrasy")
    return persona


def _validate_relationships(
    relationships: Mapping[str, object] | None,
    personas: Sequence[Persona],
) -> dict[str, float]:
    if relationships is None:
        return {persona.id: REL_START for persona in personas}
    if not isinstance(relationships, Mapping):
        raise ValueError("relationships must be a mapping")
    expected_ids = {persona.id for persona in personas}
    if set(relationships) != expected_ids:
        raise ValueError("relationships must contain exactly the persona ids")
    validated = {
        persona_id: _finite_number(relationships[persona_id], f"{persona_id}.relationship")
        for persona_id in relationships
    }
    for persona_id, relationship in validated.items():
        if relationship < 0.0 or relationship > 1.0:
            raise ValueError(f"{persona_id}.relationship must be within [0, 1]")
    return validated


def _round_number(record: Mapping[str, Any], record_index: int) -> int:
    if "round" not in record:
        raise ValueError(f"history record {record_index} is missing round")
    round_number = record["round"]
    if not isinstance(round_number, int) or isinstance(round_number, bool):
        raise ValueError(f"history record {record_index} round must be an integer")
    return round_number


def _mapping_field(mapping: Mapping[str, Any], field_name: str, context: str) -> Mapping[str, Any]:
    if field_name not in mapping or not isinstance(mapping[field_name], Mapping):
        raise ValueError(f"{context} must contain mapping field {field_name}")
    return mapping[field_name]


def _validate_firm_name(firm: str) -> None:
    if not isinstance(firm, str) or firm == "":
        raise ValueError("firm must be a non-empty string")


def _validate_segment(segment: str) -> None:
    if segment not in SAYDO_GAP:
        raise ValueError(f"unsupported segment: {segment}")


def _finite_number(value: object, field_name: str) -> float:
    try:
        converted = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be a finite number") from exc
    if not isfinite(converted):
        raise ValueError(f"{field_name} must be a finite number")
    return converted


def _nonnegative_finite(value: object, field_name: str) -> float:
    converted = _finite_number(value, field_name)
    if converted < 0.0:
        raise ValueError(f"{field_name} must be nonnegative")
    return converted


def _warmth_label(value: float) -> str:
    if value >= 0.66:
        return "enthusiastic"
    if value >= 0.50:
        return "open"
    if value >= 0.34:
        return "mixed"
    return "reserved"


def _relationship_label(value: float) -> str:
    if value >= 0.66:
        return "close"
    if value >= 0.50:
        return "steady"
    if value >= 0.34:
        return "tentative"
    return "distant"


def _campaign_label(value: str) -> str:
    if value == "strong":
        return "visible"
    if value == "soft":
        return "quiet"
    return "steady"
