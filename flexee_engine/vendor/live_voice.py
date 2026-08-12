from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from math import isfinite
from re import IGNORECASE, search, sub
from types import MappingProxyType
from typing import Any, Protocol

import personas
from customer_agents import GroundedResponse, mock_generator


SEGMENT_LABELS = MappingProxyType({
    "LEG": ("Legacy", "Legacy Core"),
    "FIT": ("Fitness", "Performance segment"),
    "FUN": ("Functional", "Clean-Wellness", "Wellness segment"),
    "GENZ": ("Gen-Z", "Gen Z", "GenZ"),
    "MAIN": ("Mainstream",),
})
COMPETITORS = ("BigRed", "Monssta", "Pulse")
PROMPT_COMPETITORS = COMPETITORS + ("Alani", "Celsius", "Monster")
SCOREBOARD_TERMS = ("scoreboard", "market share", "share of segment", "rank", "percentile")
REQUIRED_PROFILE_FIELDS = (
    "id",
    "name",
    "age",
    "location",
    "voice_profile",
    "moved_by",
    "segment",
)
REQUIRED_GROUNDED_FIELDS = ("mode", "persona_id", "persona_name", "stated_warmth", "relationship")


class ModelClient(Protocol):
    def __call__(
        self,
        system: str,
        user: str,
        temperature: float = 0.7,
    ) -> str:
        ...


class MockModelClient:
    """Deterministic model test double; when responses are exhausted, repeats the last."""

    def __init__(self, responses: Sequence[str] | str | None = None) -> None:
        if responses is None:
            self._responses = ("Marcus says the brand feels familiar, and Marcus is still deciding how close it fits.",)
        elif isinstance(responses, str):
            self._responses = (responses,)
        else:
            if isinstance(responses, (str, bytes)) or not isinstance(responses, Sequence) or not responses:
                raise ValueError("responses must be a nonempty string sequence")
            self._responses = tuple(responses)
        if not all(isinstance(response, str) for response in self._responses):
            raise ValueError("responses must contain only strings")
        self.calls: list[dict[str, object]] = []
        self._index = 0

    def __call__(self, system: str, user: str, temperature: float = 0.7) -> str:
        self.calls.append({"system": system, "user": user, "temperature": temperature})
        response = self._responses[min(self._index, len(self._responses) - 1)]
        self._index += 1
        return response


def band(stated_warmth: object) -> str:
    value = _bounded_float(stated_warmth, "stated_warmth")
    if value >= 0.6:
        return "warm"
    if value <= 0.4:
        return "cool"
    return "lukewarm"


def lean(relationship: object) -> str:
    value = _bounded_float(relationship, "relationship")
    if value >= 0.55:
        return "warming toward"
    if value <= 0.45:
        return "cooling on"
    return "neutral toward"


def build_persona_prompt(
    profile: Mapping[str, Any],
    stated_warmth: object,
    relationship: object,
) -> tuple[str, str]:
    validated = _validate_profile(profile)
    hidden_segment = str(validated["segment"])
    persona_name = _sanitize_prompt_text(str(validated["name"]))
    location = _sanitize_prompt_text(str(validated["location"]))
    voice_profile = _sanitize_prompt_text(str(validated["voice_profile"]))
    moved_by = ", ".join(
        _sanitize_prompt_text(str(item))
        for item in _validate_moved_by(validated["moved_by"], str(validated["id"]))
    )
    warmth_band = band(stated_warmth)
    relationship_lean = lean(relationship)
    system = (
        f"You are {persona_name}, an adult customer from {location}. "
        f"Voice: {voice_profile}. Moved by: {moved_by}. "
        "Stay fully in character. Answer in one or two sentences. "
        "Never name or describe a market segment. Never mention a competitor brand. "
        "Never quote a number, score, share, or ranking."
    )
    user = (
        f"You feel {warmth_band} about the brand and have been {relationship_lean} it lately. "
        "Say what you would say in the room."
    )
    if leakage_scan(system + "\n" + user, hidden_segment):
        raise ValueError("prompt contains hidden or unsafe content")
    return system, user


def leakage_scan(text: str, hidden_segment: str) -> list[tuple[str, str]]:
    if not isinstance(text, str):
        raise ValueError("text must be a string")
    if hidden_segment not in SEGMENT_LABELS:
        raise ValueError("hidden_segment must be a known segment code")

    findings: list[tuple[str, str]] = []
    for label in SEGMENT_LABELS[hidden_segment]:
        if _whole_phrase_search(label, text):
            findings.append(("segment_label", label))
    for code in SEGMENT_LABELS:
        if search(rf"\b{code}\b", text) is not None:
            findings.append(("segment_code", code))
    for competitor in COMPETITORS:
        if _whole_phrase_search(competitor, text):
            findings.append(("competitor", competitor))
    for term in SCOREBOARD_TERMS:
        if _whole_phrase_search(term, text):
            findings.append(("scoreboard", term))
    return findings


def _finite_float(value: object, label: str) -> float:
    try:
        converted = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be a finite number") from exc
    if not isfinite(converted):
        raise ValueError(f"{label} must be a finite number")
    return converted


def _validate_profile(profile: Mapping[str, Any]) -> Mapping[str, Any]:
    if not isinstance(profile, Mapping):
        raise ValueError("profile must be a mapping")
    for field in REQUIRED_PROFILE_FIELDS:
        if field not in profile:
            raise ValueError(f"profile is missing {field}")
    if not isinstance(profile["id"], str) or profile["id"] == "":
        raise ValueError("profile id must be a non-empty string")
    if not isinstance(profile["name"], str) or profile["name"] == "":
        raise ValueError("profile name must be a non-empty string")
    if profile["segment"] not in SEGMENT_LABELS:
        raise ValueError("profile segment must be a known segment code")
    if not isinstance(profile["location"], str) or profile["location"] == "":
        raise ValueError("profile location must be a non-empty string")
    if not isinstance(profile["voice_profile"], str) or profile["voice_profile"] == "":
        raise ValueError("profile voice_profile must be a non-empty string")
    # Age is available to validate the profile record but is deliberately omitted from prompts.
    _finite_float(profile["age"], "profile age")
    _validate_moved_by(profile["moved_by"], str(profile["id"]))
    return profile


def _validate_moved_by(value: Any, persona_id: str) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValueError(f"{persona_id}.moved_by must be a sequence")
    moved_by = tuple(value)
    if not moved_by or not all(isinstance(item, str) and item for item in moved_by):
        raise ValueError(f"{persona_id}.moved_by must contain non-empty strings")
    return moved_by


def build_pool_by_id(persona_pool: Sequence[Mapping[str, Any]]) -> Mapping[str, Mapping[str, Any]]:
    if isinstance(persona_pool, (str, bytes)) or not isinstance(persona_pool, Sequence):
        raise ValueError("persona_pool must be a sequence")
    if not persona_pool:
        raise ValueError("persona_pool must be nonempty")
    pool: dict[str, Mapping[str, Any]] = {}
    for raw_profile in persona_pool:
        profile = _validate_profile(raw_profile)
        persona_id = str(profile["id"])
        if persona_id in pool:
            raise ValueError(f"duplicate persona id: {persona_id}")
        frozen_profile = dict(profile)
        frozen_profile["moved_by"] = tuple(profile["moved_by"])
        pool[persona_id] = MappingProxyType(frozen_profile)
    return MappingProxyType(pool)


POOL_BY_ID = build_pool_by_id(personas.POOL)


def live_persona_generator(
    model_client,
    pool_by_id=POOL_BY_ID,
    fallback=None,
):
    if not callable(model_client):
        raise ValueError("model_client must be callable")
    pool = _validate_pool_by_id(pool_by_id)
    fallback_generator = fallback or mock_generator
    if not callable(fallback_generator):
        raise ValueError("fallback must be callable")
    leak_events: list[dict[str, object]] = []
    last_attempts: list[dict[str, object]] = []

    def generator(response: GroundedResponse) -> str:
        grounded = _validate_grounded_response(response)
        persona_id = str(grounded["persona_id"])
        if persona_id not in pool:
            raise ValueError(f"unknown persona_id: {persona_id}")
        profile = pool[persona_id]
        if grounded["persona_name"] != profile["name"]:
            raise ValueError("grounded persona_name must match the persona profile")
        hidden_segment = str(profile["segment"])
        system, user = build_persona_prompt(
            profile,
            grounded["stated_warmth"],
            grounded["relationship"],
        )
        last_attempts.clear()
        for attempt in (1, 2):
            text = model_client(system, user, 0.7)
            if not isinstance(text, str):
                raise ValueError("model_client must return a string")
            findings = tuple(leakage_scan(text, hidden_segment))
            attempt_record = {
                "persona_id": persona_id,
                "attempt": attempt,
                "source": "model",
                "findings": findings,
                "clean": not findings,
                "fallback_used": False,
                "fixed_safe_response_used": False,
            }
            last_attempts.append(attempt_record)
            if not findings:
                return text
            event = {
                "persona_id": persona_id,
                "attempt": attempt,
                "findings": findings,
                "fallback_used": False,
            }
            leak_events.append(event)

        fallback_text = fallback_generator(dict(grounded))
        if not isinstance(fallback_text, str):
            raise ValueError("fallback must return a string")
        fallback_findings = tuple(leakage_scan(fallback_text, hidden_segment))
        leak_events[-1]["fallback_used"] = True
        if not fallback_findings:
            last_attempts.append({
                "persona_id": persona_id,
                "attempt": "fallback",
                "source": "fallback",
                "findings": (),
                "clean": True,
                "fallback_used": True,
                "fixed_safe_response_used": False,
            })
            return fallback_text
        safe_name = _sanitize_prompt_text(str(profile["name"]))
        fixed_text = f"{safe_name} is still sorting out how the brand feels to them."
        fixed_findings = tuple(leakage_scan(fixed_text, hidden_segment))
        if fixed_findings:
            fixed_text = "I am still sorting out how the brand feels to me."
            final_findings = tuple(leakage_scan(fixed_text, hidden_segment))
            if final_findings:
                raise ValueError("internal fixed-safe response unexpectedly failed leakage scan")
        last_attempts.append({
            "persona_id": persona_id,
            "attempt": "fixed_safe",
            "source": "fixed_safe",
            "findings": fallback_findings,
            "clean": True,
            "fallback_used": True,
            "fixed_safe_response_used": True,
        })
        return fixed_text

    generator.leak_events = leak_events  # type: ignore[attr-defined]
    generator.last_attempts = last_attempts  # type: ignore[attr-defined]
    generator.pool_by_id = pool  # type: ignore[attr-defined]
    return generator


def run_rubric(
    generator: Callable[[GroundedResponse], str],
    cases: Sequence[Mapping[str, Any]],
    judge_client: ModelClient | None = None,
) -> dict[str, object]:
    if not callable(generator):
        raise ValueError("generator must be callable")
    if isinstance(cases, (str, bytes)) or not isinstance(cases, Sequence):
        raise ValueError("cases must be a non-string sequence")
    if not cases:
        raise ValueError("cases must be nonempty")
    if judge_client is not None and not callable(judge_client):
        raise ValueError("judge_client must be callable")

    seen_case_ids: set[object] = set()
    results: list[dict[str, object]] = []
    regressions: list[tuple[object, str]] = []
    judge_scores: list[dict[str, float]] = []

    for index, case in enumerate(cases, start=1):
        if not isinstance(case, Mapping):
            raise ValueError(f"case {index} must be a mapping")
        if "case_id" not in case:
            raise ValueError(f"case {index} is missing case_id")
        case_id = case["case_id"]
        if case_id in seen_case_ids:
            raise ValueError(f"duplicate case_id: {case_id}")
        seen_case_ids.add(case_id)

        pool = getattr(generator, "pool_by_id", POOL_BY_ID)
        grounded = _case_grounded_response(case, index, pool)
        profile = _profile_for_grounded(grounded, pool)
        hidden_segment = str(case.get("hidden_segment", profile["segment"]))
        if hidden_segment not in SEGMENT_LABELS:
            raise ValueError(f"case {case_id} has invalid hidden_segment")
        if hidden_segment != profile["segment"]:
            raise ValueError(f"case {case_id} hidden_segment does not match persona profile")
        previous_event_count = len(getattr(generator, "leak_events", ()))
        text = generator(dict(grounded))
        if not isinstance(text, str):
            raise ValueError("generator must return a string")
        case_events = tuple(getattr(generator, "leak_events", ())[previous_event_count:])
        final_leakage = leakage_scan(text, hidden_segment)
        in_character = str(grounded["persona_name"]).lower() in text.lower()
        leak_guard_triggered = bool(case_events)
        fallback_used = any(bool(event.get("fallback_used")) for event in case_events)
        deterministic = {
            "leakage": final_leakage,
            "no_leakage": not final_leakage,
            "in_character": in_character,
            "leak_guard_triggered": leak_guard_triggered,
            "fallback_used": fallback_used,
        }
        judge = None
        if judge_client is not None:
            judge = _judge_response(judge_client, text, grounded)
            judge_scores.append({
                "direction_fidelity": judge["direction_fidelity"],
                "character": judge["character"],
            })
        if final_leakage:
            regressions.append((case_id, "final_leakage"))
        if not in_character:
            regressions.append((case_id, "persona_name_absent"))
        if leak_guard_triggered:
            regressions.append((case_id, "leak_guard_triggered"))
        if fallback_used:
            regressions.append((case_id, "fallback_used"))

        results.append({
            "case_id": case_id,
            "persona_id": grounded["persona_id"],
            "text": text,
            "deterministic": deterministic,
            "judge": judge,
        })

    deterministic_passes = sum(
        bool(case["deterministic"]["no_leakage"] and case["deterministic"]["in_character"])
        for case in results
    )
    return {
        "cases": results,
        "n_cases": len(results),
        "deterministic_passes": deterministic_passes,
        "deterministic_failures": len(results) - deterministic_passes,
        "leak_guard_activations": sum(bool(case["deterministic"]["leak_guard_triggered"]) for case in results),
        "fallback_uses": sum(bool(case["deterministic"]["fallback_used"]) for case in results),
        "judge_averages": _judge_averages(judge_scores) if judge_scores else None,
        "regressions": tuple(regressions),
    }


def _validate_pool_by_id(pool_by_id: Mapping[str, Mapping[str, Any]]) -> Mapping[str, Mapping[str, Any]]:
    if not isinstance(pool_by_id, Mapping) or not pool_by_id:
        raise ValueError("pool_by_id must be a nonempty mapping")
    for persona_id, profile in pool_by_id.items():
        if not isinstance(persona_id, str) or persona_id == "":
            raise ValueError("pool_by_id keys must be non-empty strings")
        validated = _validate_profile(profile)
        if validated["id"] != persona_id:
            raise ValueError(f"pool_by_id key mismatch for {persona_id}")
    return pool_by_id


def _validate_grounded_response(response: Mapping[str, Any]) -> GroundedResponse:
    if not isinstance(response, Mapping):
        raise ValueError("grounded response must be a mapping")
    for field in REQUIRED_GROUNDED_FIELDS:
        if field not in response:
            raise ValueError(f"grounded response is missing {field}")
    if not isinstance(response["persona_id"], str) or response["persona_id"] == "":
        raise ValueError("persona_id must be a non-empty string")
    if not isinstance(response["persona_name"], str) or response["persona_name"] == "":
        raise ValueError("persona_name must be a non-empty string")
    _bounded_float(response["stated_warmth"], "stated_warmth")
    _bounded_float(response["relationship"], "relationship")
    return dict(response)


def _case_grounded_response(
    case: Mapping[str, Any],
    index: int,
    pool_by_id: Mapping[str, Mapping[str, Any]],
) -> GroundedResponse:
    if "grounded_response" in case:
        return _validate_grounded_response(case["grounded_response"])
    persona_id = case.get("persona_id")
    if not isinstance(persona_id, str) or persona_id == "":
        raise ValueError(f"case {index} is missing persona_id")
    profile = pool_by_id.get(persona_id)
    if profile is None:
        raise ValueError(f"case {index} has unknown persona_id")
    return _validate_grounded_response({
        "mode": case.get("mode", "focus_group"),
        "persona_id": persona_id,
        "persona_name": profile["name"],
        "stated_warmth": case.get("stated_warmth", 0.5),
        "relationship": case.get("relationship", 0.5),
    })


def _profile_for_grounded(
    grounded: GroundedResponse,
    pool_by_id: Mapping[str, Mapping[str, Any]],
) -> Mapping[str, Any]:
    profile = pool_by_id.get(str(grounded["persona_id"]))
    if profile is None:
        raise ValueError(f"unknown persona_id: {grounded['persona_id']}")
    if grounded["persona_name"] != profile["name"]:
        raise ValueError("grounded persona_name must match the persona profile")
    return profile


def _judge_response(
    judge_client: ModelClient,
    text: str,
    grounded: GroundedResponse,
) -> dict[str, object]:
    system = "Return only a JSON object assessing customer-voice quality."
    user = (
        f"Expected qualitative band: {band(grounded['stated_warmth'])}. "
        f"Expected relationship direction: {lean(grounded['relationship'])}. "
        f"Persona name: {_sanitize_prompt_text(str(grounded['persona_name']))}. "
        f"Text: {_sanitize_prompt_text(text)}. "
        "Return JSON with direction_fidelity and character, each from zero to one."
    )
    judged = judge_client(system, user, 0.0)
    if not isinstance(judged, str):
        raise ValueError("judge_client must return a string")
    try:
        parsed = json.loads(judged)
    except json.JSONDecodeError as exc:
        raise ValueError("judge_client must return valid JSON") from exc
    if not isinstance(parsed, dict):
        raise ValueError("judge_client JSON must be an object")
    scores = {
        "direction_fidelity": _bounded_float(parsed.get("direction_fidelity"), "direction_fidelity"),
        "character": _bounded_float(parsed.get("character"), "character"),
    }
    return {
        "direction_fidelity": scores["direction_fidelity"],
        "character": scores["character"],
        "raw": judged,
    }


def _judge_averages(scores: Sequence[Mapping[str, float]]) -> dict[str, float]:
    return {
        "direction_fidelity": sum(score["direction_fidelity"] for score in scores) / len(scores),
        "character": sum(score["character"] for score in scores) / len(scores),
    }


def _whole_phrase_search(phrase: str, text: str) -> bool:
    return search(_phrase_pattern(phrase), text, IGNORECASE) is not None


def _sanitize_prompt_text(value: str) -> str:
    sanitized = value
    for labels in SEGMENT_LABELS.values():
        for label in labels:
            sanitized = _remove_phrase(sanitized, label)
    for code in SEGMENT_LABELS:
        sanitized = _remove_phrase(sanitized, code)
    for competitor in PROMPT_COMPETITORS:
        sanitized = _remove_phrase(sanitized, competitor)
    for term in SCOREBOARD_TERMS:
        sanitized = _remove_phrase(sanitized, term)
    sanitized = "".join(" " if char.isdigit() else char for char in sanitized)
    return " ".join(sanitized.split()) or "customer"


def _remove_phrase(text: str, phrase: str) -> str:
    return sub(_phrase_pattern(phrase), " ", text, flags=IGNORECASE)


def _phrase_pattern(phrase: str) -> str:
    return r"\b" + "".join(
        r"\s+" if char.isspace() else "\\" + char if not char.isalnum() else char
        for char in phrase
    ) + r"\b"


def _bounded_float(value: object, label: str) -> float:
    try:
        converted = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be a finite number") from exc
    if not isfinite(converted):
        raise ValueError(f"{label} must be a finite number")
    if converted < 0.0 or converted > 1.0:
        raise ValueError(f"{label} must be within [0, 1]")
    return converted

