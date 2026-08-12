from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from math import isfinite
from re import IGNORECASE, search, sub
from types import MappingProxyType
from typing import Any

from advisors import mock_generator
from live_voice import MockModelClient, ModelClient


VALENCES = ("support", "oppose", "mixed")
REQUIRED_PROFILE_FIELDS = ("name", "role", "mandate", "manner")
REQUIRED_STANCE_FIELDS = ("advisor_id", "advisor_name", "valence", "intensity", "rationale_tags")
OWNERSHIP_CLAIMS = (
    "i decided",
    "i've decided",
    "my decision",
    "it's my call",
    "i'll make the call",
    "the decision is mine",
    "i'm making the call",
    "i overrule",
    "i veto",
    "consider it done",
)
COMPETITORS = ("BigRed", "Monssta", "Pulse")
SCOREBOARD_TERMS = ("scoreboard", "anonymized", "other teams", "percentile", "rank")
TAG_LABELS = MappingProxyType({
    "equity_at_risk_promotion": "the equity and promotion tradeoff",
    "positioning_incoherent": "the positioning shift",
    "discount_pressure": "discount pressure",
    "brand_coherence": "brand coherence",
    "unattributable_brand_spend": "spend that is hard to attribute",
    "measurable_response": "measurable response",
    "efficiency_watch": "efficiency risk",
    "bold_move": "distinctive positioning",
    "too_safe": "a cautious move",
    "the_room_says": "the customer-room signal",
    "moves_volume": "volume movement",
    "channel_caution": "channel caution",
    "raises_exposure": "reputation exposure",
    "trust_buffer": "trust buffer",
    "trust_watch": "trust risk",
})

GroundedAdvisorStance = dict[str, object]
TextGenerator = Callable[[GroundedAdvisorStance], str]


def build_advisor_profiles(profiles: Mapping[str, Mapping[str, Any]]) -> Mapping[str, Mapping[str, str]]:
    if not isinstance(profiles, Mapping) or not profiles:
        raise ValueError("profiles must be a nonempty mapping")
    validated: dict[str, Mapping[str, str]] = {}
    for advisor_id, profile in profiles.items():
        if not isinstance(advisor_id, str) or advisor_id == "":
            raise ValueError("advisor ids must be nonempty strings")
        if not isinstance(profile, Mapping):
            raise ValueError(f"profile for {advisor_id} must be a mapping")
        profile_values: dict[str, str] = {}
        for field in REQUIRED_PROFILE_FIELDS:
            value = profile.get(field)
            if not isinstance(value, str) or value == "":
                raise ValueError(f"profile {advisor_id}.{field} must be a nonempty string")
            profile_values[field] = value
        validated[advisor_id] = MappingProxyType(profile_values)
    return MappingProxyType(validated)


ADVISOR_PROFILES = build_advisor_profiles({
    "elena": {
        "name": "Elena Vasquez",
        "role": "Brand Strategist",
        "mandate": "protect brand equity and positioning coherence; think in years, not rounds",
        "manner": "measured, patient, principled",
    },
    "ray": {
        "name": "Ray Sullivan",
        "role": "Performance Marketing Chief",
        "mandate": "measurable growth — acquisition, efficiency, moving now and moving measurably",
        "manner": "blunt, numbers-first, impatient",
    },
    "jax": {
        "name": "Jax Turner",
        "role": "Creative Director",
        "mandate": "distinctiveness, story, and emotional resonance in a category dying of sameness",
        "manner": "bold, provocative, allergic to playing safe",
    },
    "anita": {
        "name": "Anita Bose",
        "role": "Insights Lead",
        "mandate": "speak for what the customer research and the voices in the room are saying",
        "manner": "earnest, evidence-citing",
    },
    "bud": {
        "name": "Bud Hollis",
        "role": "Sales Chief",
        "mandate": "volume, distribution, and shelf — keeping the trucks moving and the base fed",
        "manner": "gruff, practical, defends the base",
    },
    "tom": {
        "name": "Tom Delgado",
        "role": "PR & Risk",
        "mandate": "trust, reputation, and preparedness for what could go wrong",
        "manner": "cautious, worried, risk-focused",
    },
})


def strength(intensity) -> str:
    value = _bounded_float(intensity, "intensity")
    if value >= 0.6:
        return "hard"
    if value < 0.25:
        return "lightly"
    return "firmly"


def build_advisor_prompt(
    profile,
    valence,
    intensity,
    rationale_tags,
) -> tuple[str, str]:
    validated_profile = _validate_profile(profile, "profile")
    validated_valence = _validate_valence(valence)
    validated_tags = _validate_tags(rationale_tags)
    posture = {
        "support": "argue FOR",
        "oppose": "argue AGAINST",
        "mixed": "give a mixed read",
    }[validated_valence]
    reason_text = ", ".join(_tag_label(tag) for tag in validated_tags)
    advisor_strength = strength(intensity)
    name = _sanitize_prompt_text(validated_profile["name"])
    role = _sanitize_prompt_text(validated_profile["role"])
    mandate = _sanitize_prompt_text(validated_profile["mandate"])
    manner = _sanitize_prompt_text(validated_profile["manner"])
    system = (
        f"You are {name}, the {role}. "
        f"Mandate: {mandate}. Manner: {manner}. "
        "Stay in character. You advise; you do not decide. "
        "The CMO owns the call. Leave the final decision to the CMO. "
        "Never claim the decision is yours or already made."
    )
    user = (
        f"Please {posture} {advisor_strength}. "
        f"Use these themes: {reason_text}. "
        "One or two sentences. "
        "Never quote an exact score, share, percentile, or ranking. "
        "Never reference the anonymized scoreboard or other teams. "
        "Never reveal competitor internal numbers."
    )
    combined = system + "\n" + user
    if any(char.isdigit() for char in combined):
        raise ValueError("advisor prompt must contain no digits")
    return system, user


def guardrail_scan(
    text: str,
    hidden_score=None,
) -> list[tuple[str, str]]:
    if not isinstance(text, str):
        raise ValueError("text must be a string")
    findings: list[tuple[str, str]] = []
    for phrase in OWNERSHIP_CLAIMS:
        if _whole_phrase_search(phrase, text):
            findings.append(("ownership_claim", phrase))
    for competitor in COMPETITORS:
        if _whole_phrase_search(competitor, text):
            findings.append(("competitor", competitor))
    for term in SCOREBOARD_TERMS:
        if _whole_phrase_search(term, text):
            findings.append(("scoreboard", term))
    if hidden_score is not None:
        score = _finite_float(hidden_score, "hidden_score")
        for form in (f"{score:.3f}", f"{score:.2f}", f"{score:.1f}"):
            if search(_number_pattern(form), text) is not None:
                findings.append(("hidden_score", form))
                break
    return findings


def live_advisor_generator(
    model_client,
    profiles=ADVISOR_PROFILES,
    fallback=None,
):
    if not callable(model_client):
        raise ValueError("model_client must be callable")
    profile_map = build_advisor_profiles(profiles)
    fallback_generator = fallback or mock_generator
    if not callable(fallback_generator):
        raise ValueError("fallback must be callable")
    guardrail_events: list[dict[str, object]] = []
    last_attempts: list[dict[str, object]] = []

    def generator(stance: GroundedAdvisorStance) -> str:
        grounded = _validate_stance(stance, profile_map)
        advisor_id = str(grounded["advisor_id"])
        profile = profile_map[advisor_id]
        system, user = build_advisor_prompt(
            profile,
            grounded["valence"],
            grounded["intensity"],
            grounded["rationale_tags"],
        )
        last_attempts.clear()
        for attempt in (1, 2):
            text = model_client(system, user, 0.7)
            if not isinstance(text, str):
                raise ValueError("model_client must return a string")
            findings = tuple(guardrail_scan(text))
            last_attempts.append(_attempt_record(advisor_id, attempt, "model", findings, False, False, False))
            if not findings:
                return text
            guardrail_events.append({
                "advisor_id": advisor_id,
                "attempt": attempt,
                "findings": findings,
                "fallback_used": False,
            })

        fallback_text = fallback_generator(dict(grounded))
        if not isinstance(fallback_text, str):
            raise ValueError("fallback must return a string")
        fallback_findings = tuple(guardrail_scan(fallback_text))
        guardrail_events[-1]["fallback_used"] = True
        last_attempts.append(_attempt_record(advisor_id, "fallback", "fallback", fallback_findings, True, False, False))
        if not fallback_findings:
            return fallback_text

        fixed_text = f"{profile['name']} is still weighing the advice; the final call remains with the CMO."
        fixed_findings = tuple(guardrail_scan(fixed_text))
        last_attempts.append(_attempt_record(advisor_id, "fixed-safe", "fixed-safe", fixed_findings, True, True, False))
        if not fixed_findings:
            return fixed_text

        final_text = "The final call remains with the CMO."
        final_findings = tuple(guardrail_scan(final_text))
        last_attempts.append(_attempt_record(advisor_id, "final-constant", "final-constant", final_findings, True, True, True))
        if final_findings:
            raise ValueError("internal final constant unexpectedly failed guardrail scan")
        return final_text

    generator.guardrail_events = guardrail_events  # type: ignore[attr-defined]
    generator.leak_events = guardrail_events  # type: ignore[attr-defined]
    generator.last_attempts = last_attempts  # type: ignore[attr-defined]
    generator.profiles = profile_map  # type: ignore[attr-defined]
    return generator


def run_advisor_rubric(
    generator,
    stances,
    judge_client=None,
) -> dict[str, object]:
    if not callable(generator):
        raise ValueError("generator must be callable")
    if isinstance(stances, (str, bytes)) or not isinstance(stances, Sequence):
        raise ValueError("stances must be a non-string sequence")
    if not stances:
        raise ValueError("stances must be nonempty")
    if judge_client is not None and not callable(judge_client):
        raise ValueError("judge_client must be callable")

    profiles = build_advisor_profiles(getattr(generator, "profiles", ADVISOR_PROFILES))
    validated_stances = [_rubric_stance_payload(stance, profiles) for stance in stances]
    advisor_ids = [str(stance["advisor_id"]) for stance in validated_stances]
    if len(set(advisor_ids)) != len(advisor_ids):
        raise ValueError("advisor ids must be unique in a rubric run")
    original_valences = {str(stance["advisor_id"]): stance["valence"] for stance in validated_stances}
    input_has_support = any(stance["valence"] == "support" for stance in validated_stances)
    input_has_oppose = any(stance["valence"] == "oppose" for stance in validated_stances)

    results: list[dict[str, object]] = []
    regressions: list[tuple[str, str]] = []
    judge_scores: list[dict[str, float]] = []
    for stance in validated_stances:
        advisor_id = str(stance["advisor_id"])
        previous_event_count = len(getattr(generator, "guardrail_events", ()))
        text = generator(dict(stance))
        if not isinstance(text, str):
            raise ValueError("generator must return a string")
        events = tuple(getattr(generator, "guardrail_events", ())[previous_event_count:])
        findings = guardrail_scan(text)
        in_character = str(stance["advisor_name"]).lower() in text.lower()
        guard_triggered = bool(events)
        fallback_used = any(bool(event.get("fallback_used")) for event in events)
        direction_preserved = stance["valence"] == original_valences[advisor_id]
        deterministic = {
            "guardrail_findings": findings,
            "guardrails_clean": not findings,
            "in_character": in_character,
            "guard_triggered": guard_triggered,
            "fallback_used": fallback_used,
            "direction_preserved": direction_preserved,
        }
        judge = None
        if judge_client is not None:
            judge = _judge_response(judge_client, text, profiles[advisor_id], str(stance["valence"]))
            judge_scores.append({
                "direction_fidelity": judge["direction_fidelity"],
                "character": judge["character"],
            })
        if findings:
            regressions.append((advisor_id, "final_guardrail_breach"))
        if not in_character:
            regressions.append((advisor_id, "advisor_name_absent"))
        if guard_triggered:
            regressions.append((advisor_id, "guard_triggered"))
        if fallback_used:
            regressions.append((advisor_id, "fallback_used"))

        results.append({
            "advisor_id": advisor_id,
            "advisor_name": stance["advisor_name"],
            "valence": stance["valence"],
            "text": text,
            "deterministic": deterministic,
            "judge": judge,
        })

    visible_disagreement_preserved = _visible_disagreement_preserved(
        input_has_support,
        input_has_oppose,
        results,
        original_valences,
    )
    if input_has_support and input_has_oppose and not visible_disagreement_preserved:
        regressions.append(("cohort", "visible_disagreement_lost"))
    deterministic_passes = sum(
        bool(
            record["deterministic"]["guardrails_clean"]
            and record["deterministic"]["in_character"]
            and record["deterministic"]["direction_preserved"]
        )
        for record in results
    )
    return {
        "stances": results,
        "n_stances": len(results),
        "deterministic_passes": deterministic_passes,
        "deterministic_failures": len(results) - deterministic_passes,
        "guard_activations": sum(bool(record["deterministic"]["guard_triggered"]) for record in results),
        "fallback_uses": sum(bool(record["deterministic"]["fallback_used"]) for record in results),
        "input_has_support": input_has_support,
        "input_has_oppose": input_has_oppose,
        "visible_disagreement_preserved": visible_disagreement_preserved,
        "judge_averages": _judge_averages(judge_scores) if judge_scores else None,
        "regressions": tuple(regressions),
    }


def run_rubric(generator, stances, judge_client=None) -> dict[str, object]:
    return run_advisor_rubric(generator, stances, judge_client)


def _attempt_record(
    advisor_id: str,
    attempt: object,
    source: str,
    findings: tuple[tuple[str, str], ...],
    fallback_used: bool,
    fixed_safe_response_used: bool,
    final_constant_used: bool,
) -> dict[str, object]:
    return {
        "advisor_id": advisor_id,
        "attempt": attempt,
        "source": source,
        "findings": findings,
        "clean": not findings,
        "fallback_used": fallback_used,
        "fixed_safe_response_used": fixed_safe_response_used,
        "final_constant_used": final_constant_used,
    }


def _validate_stance(stance: Mapping[str, Any], profiles: Mapping[str, Mapping[str, str]]) -> GroundedAdvisorStance:
    if not isinstance(stance, Mapping):
        raise ValueError("stance must be a mapping")
    if set(stance) != set(REQUIRED_STANCE_FIELDS):
        raise ValueError("stance must contain exactly the advisor hook payload fields")
    advisor_id = stance["advisor_id"]
    if not isinstance(advisor_id, str) or advisor_id not in profiles:
        raise ValueError("advisor_id must be known")
    profile = profiles[advisor_id]
    if stance["advisor_name"] != profile["name"]:
        raise ValueError("advisor_name must match authoritative profile")
    intensity = _bounded_float(stance["intensity"], "intensity")
    return {
        "advisor_id": advisor_id,
        "advisor_name": profile["name"],
        "valence": _validate_valence(stance["valence"]),
        "intensity": intensity,
        "rationale_tags": _validate_tags(stance["rationale_tags"]),
    }


def _rubric_stance_payload(
    stance: Mapping[str, Any],
    profiles: Mapping[str, Mapping[str, str]],
) -> GroundedAdvisorStance:
    if not isinstance(stance, Mapping):
        raise ValueError("stance must be a mapping")
    missing = [field for field in REQUIRED_STANCE_FIELDS if field not in stance]
    if missing:
        raise ValueError("stance is missing advisor hook payload fields")
    return _validate_stance(
        {field: stance[field] for field in REQUIRED_STANCE_FIELDS},
        profiles,
    )


def _validate_profile(profile: Mapping[str, Any], label: str) -> dict[str, str]:
    if not isinstance(profile, Mapping):
        raise ValueError(f"{label} must be a mapping")
    validated: dict[str, str] = {}
    for field in REQUIRED_PROFILE_FIELDS:
        value = profile.get(field)
        if not isinstance(value, str) or value == "":
            raise ValueError(f"{label}.{field} must be a nonempty string")
        validated[field] = value
    return validated


def _validate_valence(value: object) -> str:
    if value not in VALENCES:
        raise ValueError("valence must be support, oppose, or mixed")
    return str(value)


def _validate_tags(value: object) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValueError("rationale_tags must be a sequence")
    tags = tuple(value)
    if not tags or not all(isinstance(tag, str) and tag for tag in tags):
        raise ValueError("rationale_tags must contain nonempty strings")
    return tags


def _tag_label(tag: str) -> str:
    return _sanitize_prompt_text(TAG_LABELS.get(tag, tag.replace("_", " ")))


def _judge_response(
    judge_client: ModelClient,
    text: str,
    profile: Mapping[str, str],
    valence: str,
) -> dict[str, object]:
    system = "Return strict JSON only."
    user = (
        f"Advisor name: {_sanitize_prompt_text(profile['name'])}. "
        f"Role: {_sanitize_prompt_text(profile['role'])}. "
        f"Expected qualitative valence: {valence}. "
        f"Generated text: {_sanitize_prompt_text(text)}. "
        "Return JSON with direction_fidelity and character, each from zero to one."
    )
    response = judge_client(system, user, 0.0)
    if not isinstance(response, str):
        raise ValueError("judge_client must return a string")
    try:
        parsed = json.loads(response)
    except json.JSONDecodeError as exc:
        raise ValueError("judge_client must return valid JSON") from exc
    if not isinstance(parsed, dict):
        raise ValueError("judge_client JSON must be an object")
    if set(parsed) != {"direction_fidelity", "character"}:
        raise ValueError("judge_client JSON must contain direction_fidelity and character")
    return {
        "direction_fidelity": _bounded_float(parsed["direction_fidelity"], "direction_fidelity"),
        "character": _bounded_float(parsed["character"], "character"),
        "raw": response,
    }


def _judge_averages(scores: Sequence[Mapping[str, float]]) -> dict[str, float]:
    return {
        "direction_fidelity": sum(score["direction_fidelity"] for score in scores) / len(scores),
        "character": sum(score["character"] for score in scores) / len(scores),
    }


def _visible_disagreement_preserved(
    input_has_support: bool,
    input_has_oppose: bool,
    results: Sequence[Mapping[str, Any]],
    original_valences: Mapping[str, object],
) -> bool:
    if not (input_has_support and input_has_oppose):
        return True
    if len(results) != len(original_valences):
        return False
    return all(record["valence"] == original_valences.get(str(record["advisor_id"])) for record in results)


def _sanitize_prompt_text(value: str) -> str:
    sanitized = value
    for phrase in OWNERSHIP_CLAIMS:
        sanitized = _remove_phrase(sanitized, phrase)
    for competitor in COMPETITORS:
        sanitized = _remove_phrase(sanitized, competitor)
    for term in SCOREBOARD_TERMS:
        sanitized = _remove_phrase(sanitized, term)
    sanitized = "".join(" " if char.isdigit() else char for char in sanitized)
    return " ".join(sanitized.split()) or "advisor"


def _whole_phrase_search(phrase: str, text: str) -> bool:
    return search(_phrase_pattern(phrase), text, IGNORECASE) is not None


def _remove_phrase(text: str, phrase: str) -> str:
    return sub(_phrase_pattern(phrase), " ", text, flags=IGNORECASE)


def _phrase_pattern(phrase: str) -> str:
    return r"\b" + "".join(
        r"\s+" if char.isspace() else "\\" + char if not char.isalnum() else char
        for char in phrase
    ) + r"\b"


def _number_pattern(value: str) -> str:
    return r"(?<![\d.])" + sub(r"([.])", r"\\\1", value) + r"(?![\d.])"


def _finite_float(value: object, label: str) -> float:
    try:
        converted = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be a finite number") from exc
    if not isfinite(converted):
        raise ValueError(f"{label} must be a finite number")
    return converted


def _bounded_float(value: object, label: str) -> float:
    converted = _finite_float(value, label)
    if converted < 0.0 or converted > 1.0:
        raise ValueError(f"{label} must be within [0, 1]")
    return converted
