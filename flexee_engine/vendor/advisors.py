from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from math import isfinite
from types import MappingProxyType
from typing import Any


SEGS = ("LEG", "FIT", "FUN", "GENZ", "MAIN")
REF_PRICE = 1.50
POS_CENTER = 50.0
TRUST_BASE = 0.32
DEADBAND = 0.05

ADVISORS = (
    ("elena", "Elena Vasquez"),
    ("ray", "Ray Sullivan"),
    ("jax", "Jax Turner"),
    ("anita", "Anita Bose"),
    ("bud", "Bud Hollis"),
    ("tom", "Tom Delgado"),
)
ADVISOR_NAMES = MappingProxyType(dict(ADVISORS))
REQUIRED_LEVERS = ("brand", "perf", "research", "positioning", "emphasis", "price", "promotion")

GroundedStance = dict[str, object]
TextGenerator = Callable[[GroundedStance], str]


def advise(
    decision_under_review: Mapping[str, Any],
    context: Mapping[str, Any],
    generator: TextGenerator | None = None,
) -> list[dict[str, object]]:
    """Return deterministic advisor stances without modifying decisions or engine state."""
    evaluated_stances = evaluate_stances(decision_under_review, context)
    text_generator = generator or mock_generator
    if not callable(text_generator):
        raise ValueError("generator must be callable")

    stances: list[dict[str, object]] = []
    for internal in evaluated_stances:
        public_stance = {
            "advisor_id": internal["advisor_id"],
            "advisor_name": internal["advisor_name"],
            "valence": internal["valence"],
            "intensity": internal["intensity"],
        }
        text = text_generator({
            **public_stance,
            "rationale_tags": tuple(internal["rationale_tags"]),
        })
        if not isinstance(text, str):
            raise ValueError("generator must return text")
        stances.append({**public_stance, "text": text})

    return stances


def evaluate_stances(
    decision_under_review: Mapping[str, Any],
    context: Mapping[str, Any],
) -> list[dict[str, object]]:
    """Evaluate the six deterministic advisor stances without generating public text."""
    decision = _validate_decision_under_review(decision_under_review)
    validated_context = _validate_context(context)
    scorecards = _internal_stances(decision, validated_context)
    return [
        {
            "advisor_id": advisor_id,
            "advisor_name": advisor_name,
            "valence": scorecards[advisor_id]["valence"],
            "intensity": scorecards[advisor_id]["intensity"],
            "rationale_tags": tuple(scorecards[advisor_id]["rationale_tags"]),
            "score": scorecards[advisor_id]["score"],
        }
        for advisor_id, advisor_name in ADVISORS
    ]


def advisor_usage_record(stances: Sequence[Mapping[str, Any]]) -> dict[str, object]:
    if isinstance(stances, (str, bytes)) or not isinstance(stances, Sequence):
        raise ValueError("stances must be a sequence")
    consulted: list[str] = []
    for index, stance in enumerate(stances, start=1):
        if not isinstance(stance, Mapping):
            raise ValueError(f"stance {index} must be a mapping")
        advisor_id = stance.get("advisor_id")
        if not isinstance(advisor_id, str) or advisor_id not in ADVISOR_NAMES:
            raise ValueError(f"stance {index} has an unknown advisor_id")
        consulted.append(advisor_id)
    return {
        "consulted_advisors": tuple(consulted),
        "points_awarded": 0,
    }


def build_usage_record(
    team_id: str,
    round_number: int,
    advisor_ids: Sequence[str],
    decision_under_review: Mapping[str, Any],
) -> dict[str, object]:
    if not isinstance(team_id, str) or team_id == "":
        raise ValueError("team_id must be a non-empty string")
    if not isinstance(round_number, int) or isinstance(round_number, bool):
        raise ValueError("round_number must be an integer")
    if isinstance(advisor_ids, (str, bytes)) or not isinstance(advisor_ids, Sequence):
        raise ValueError("advisor_ids must be a sequence")
    consulted: list[str] = []
    for index, advisor_id in enumerate(advisor_ids, start=1):
        if not isinstance(advisor_id, str) or advisor_id not in ADVISOR_NAMES:
            raise ValueError(f"advisor_ids[{index}] must be a known advisor id")
        consulted.append(advisor_id)
    if len(set(consulted)) != len(consulted):
        raise ValueError("advisor_ids must be unique")
    decision = _validate_decision_under_review(decision_under_review)
    if decision["round"] != round_number:
        raise ValueError("round_number must match decision_under_review round")
    return {
        "team_id": team_id,
        "round": round_number,
        "consulted_advisors": tuple(consulted),
        "lever_changes": _lever_changes(decision["levers"], decision["baseline"]),
        "consultation_count": len(consulted),
    }


def mock_generator(stance: GroundedStance) -> str:
    advisor_name = str(stance["advisor_name"])
    valence = str(stance["valence"])
    tag = _tag_label(_first_tag(stance.get("rationale_tags", ())))
    if valence == "support":
        return f"{advisor_name} supports the direction because {tag}; the call remains yours."
    if valence == "oppose":
        return f"{advisor_name} pushes back because {tag}; the call remains yours."
    return f"{advisor_name} is mixed because {tag}; the call remains yours."


def advisor_scores(decision_under_review: Mapping[str, Any], context: Mapping[str, Any]) -> dict[str, float]:
    decision = _validate_decision_under_review(decision_under_review)
    validated_context = _validate_context(context)
    return {
        advisor_id: stance["score"]
        for advisor_id, stance in _internal_stances(decision, validated_context).items()
    }


def elena_score(levers: Mapping[str, Any], baseline: Mapping[str, Any]) -> float:
    dec = _validate_levers(levers, "levers")
    base = _validate_levers(baseline, "baseline")
    change_mag = _change_mag(dec, base)
    return (
        0.15 * (dec["brand"] - base["brand"])
        - 0.60 * dec["promotion"]
        - 1.20 * abs(dec["positioning"] - base["positioning"]) / 100.0
        - 1.00 * max(0.0, base["price"] - dec["price"])
        - 0.80 * change_mag
    )


def ray_score(levers: Mapping[str, Any], baseline: Mapping[str, Any]) -> float:
    dec = _validate_levers(levers, "levers")
    base = _validate_levers(baseline, "baseline")
    return (
        0.15 * (dec["perf"] - base["perf"])
        + 0.50 * dec["promotion"]
        + 1.00 * (dec["price"] - base["price"])
        - 0.12 * (dec["brand"] - base["brand"])
    )


def jax_score(levers: Mapping[str, Any], baseline: Mapping[str, Any] | None = None) -> float:
    dec = _validate_levers(levers, "levers")
    extremity = abs(dec["positioning"] - POS_CENTER) / 50.0
    return 1.20 * extremity - 0.80 * (1.0 - extremity)


def anita_score(levers: Mapping[str, Any], context: Mapping[str, Any]) -> float:
    dec = _validate_levers(levers, "levers")
    stated_warmth = _validate_stated_warmth(context.get("stated_warmth"))
    return 2.0 * sum(
        dec["emphasis"][index] * (stated_warmth[segment] - 0.5)
        for index, segment in enumerate(SEGS)
    )


def bud_score(levers: Mapping[str, Any], baseline: Mapping[str, Any]) -> float:
    dec = _validate_levers(levers, "levers")
    base = _validate_levers(baseline, "baseline")
    volseg = dec["emphasis"][0] + dec["emphasis"][4]
    return 0.70 * dec["promotion"] + 0.60 * volseg - 1.00 * max(0.0, dec["price"] - base["price"])


def tom_score(levers: Mapping[str, Any], baseline: Mapping[str, Any], context: Mapping[str, Any]) -> float:
    dec = _validate_levers(levers, "levers")
    base = _validate_levers(baseline, "baseline")
    validated_context = _validate_context(context)
    aggr = dec["promotion"] + max(0.0, base["price"] - dec["price"])
    return (
        1.00 * (validated_context["trust"] / 100.0 - TRUST_BASE)
        - 0.70 * dec["promotion"]
        - 0.80 * max(0.0, base["price"] - dec["price"])
        - 0.50 * aggr
        + 0.40 * (validated_context["eq"] / 100.0)
    )


def revealed_truth_signal(levers: Mapping[str, Any], revealed_warmth: Mapping[str, Any]) -> float:
    dec = _validate_levers(levers, "levers")
    truth = _validate_segment_signal(revealed_warmth, "revealed_warmth")
    return 2.0 * sum(
        dec["emphasis"][index] * (truth[segment] - 0.5)
        for index, segment in enumerate(SEGS)
    )


def valence_for_score(score: float) -> str:
    score_value = _finite_number(score, "score")
    if score_value > DEADBAND:
        return "support"
    if score_value < -DEADBAND:
        return "oppose"
    return "mixed"


def intensity_for_score(score: float) -> float:
    return min(1.0, abs(_finite_number(score, "score")))


def _internal_stances(decision: Mapping[str, Any], context: Mapping[str, Any]) -> dict[str, dict[str, object]]:
    levers = decision["levers"]
    baseline = decision["baseline"]
    scores = {
        "elena": elena_score(levers, baseline),
        "ray": ray_score(levers, baseline),
        "jax": jax_score(levers, baseline),
        "anita": anita_score(levers, context),
        "bud": bud_score(levers, baseline),
        "tom": tom_score(levers, baseline, context),
    }
    return {
        advisor_id: {
            "score": score,
            "valence": valence_for_score(score),
            "intensity": intensity_for_score(score),
            "rationale_tags": _rationale_tags(advisor_id, levers, baseline, context),
        }
        for advisor_id, score in scores.items()
    }


def _rationale_tags(
    advisor_id: str,
    levers: Mapping[str, Any],
    baseline: Mapping[str, Any],
    context: Mapping[str, Any],
) -> tuple[str, ...]:
    dec = _validate_levers(levers, "levers")
    base = _validate_levers(baseline, "baseline")
    if advisor_id == "elena":
        tags = []
        if dec["promotion"] > 0.0:
            tags.append("equity_at_risk_promotion")
        if abs(dec["positioning"] - base["positioning"]) > 20.0:
            tags.append("positioning_incoherent")
        if dec["price"] < base["price"]:
            tags.append("discount_pressure")
        return tuple(tags or ["brand_coherence"])
    if advisor_id == "ray":
        if dec["brand"] > base["brand"]:
            return ("unattributable_brand_spend",)
        if dec["promotion"] > 0.0:
            return ("measurable_response",)
        return ("efficiency_watch",)
    if advisor_id == "jax":
        extremity = abs(dec["positioning"] - POS_CENTER) / 50.0
        return ("bold_move",) if extremity >= 0.5 else ("too_safe",)
    if advisor_id == "anita":
        return ("the_room_says",)
    if advisor_id == "bud":
        if dec["promotion"] > 0.0 or dec["emphasis"][0] + dec["emphasis"][4] >= 0.5:
            return ("moves_volume",)
        return ("channel_caution",)
    if advisor_id == "tom":
        if dec["promotion"] > 0.0 or dec["price"] < base["price"]:
            return ("raises_exposure",)
        if _validate_context(context)["trust"] >= TRUST_BASE * 100.0:
            return ("trust_buffer",)
        return ("trust_watch",)
    raise ValueError(f"unknown advisor_id: {advisor_id}")


def _validate_decision_under_review(decision_under_review: Mapping[str, Any]) -> dict[str, object]:
    if not isinstance(decision_under_review, Mapping):
        raise ValueError("decision_under_review must be a mapping")
    if set(decision_under_review) != {"round", "levers", "baseline"}:
        raise ValueError("decision_under_review must contain round, levers, and baseline")
    round_number = decision_under_review["round"]
    if not isinstance(round_number, int) or isinstance(round_number, bool):
        raise ValueError("round must be an integer")
    return {
        "round": round_number,
        "levers": _validate_levers(decision_under_review["levers"], "levers"),
        "baseline": _validate_levers(decision_under_review["baseline"], "baseline"),
    }


def _validate_levers(levers: Mapping[str, Any], label: str) -> dict[str, Any]:
    if not isinstance(levers, Mapping):
        raise ValueError(f"{label} must be a mapping")
    if set(levers) != set(REQUIRED_LEVERS):
        raise ValueError(f"{label} must contain exactly the required lever keys")
    emphasis = _validate_emphasis(levers["emphasis"], f"{label}.emphasis")
    price = _finite_number(levers["price"], f"{label}.price")
    if price <= 0.0:
        raise ValueError(f"{label}.price must be positive")
    promotion = _finite_number(levers["promotion"], f"{label}.promotion")
    if promotion < 0.0:
        raise ValueError(f"{label}.promotion cannot be negative")
    return {
        "brand": _finite_number(levers["brand"], f"{label}.brand"),
        "perf": _finite_number(levers["perf"], f"{label}.perf"),
        "research": _finite_number(levers["research"], f"{label}.research"),
        "positioning": _finite_number(levers["positioning"], f"{label}.positioning"),
        "emphasis": emphasis,
        "price": price,
        "promotion": promotion,
    }


def _validate_emphasis(value: object, label: str) -> tuple[float, ...]:
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


def _validate_context(context: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(context, Mapping):
        raise ValueError("context must be a mapping")
    if set(context) != {"stated_warmth", "eq", "trust"}:
        raise ValueError("context must contain stated_warmth, eq, and trust")
    eq = _finite_number(context["eq"], "context.eq")
    trust = _finite_number(context["trust"], "context.trust")
    return {
        "stated_warmth": _validate_stated_warmth(context["stated_warmth"]),
        "eq": eq,
        "trust": trust,
    }


def _validate_stated_warmth(value: object) -> dict[str, float]:
    return _validate_segment_signal(value, "stated_warmth")


def _validate_segment_signal(value: object, label: str) -> dict[str, float]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be a mapping")
    if set(value) != set(SEGS):
        raise ValueError(f"{label} must contain exactly the supported segments")
    validated = {
        segment: _finite_number(value[segment], f"{label}.{segment}")
        for segment in SEGS
    }
    for segment, signal in validated.items():
        if signal < 0.0 or signal > 1.0:
            raise ValueError(f"{label}.{segment} must be within [0, 1]")
    return validated


def _change_mag(dec: Mapping[str, Any], base: Mapping[str, Any]) -> float:
    return (
        abs(dec["positioning"] - base["positioning"]) / 100.0
        + abs(dec["price"] - base["price"]) / base["price"]
        + dec["promotion"]
    )


def _lever_changes(levers: Mapping[str, Any], baseline: Mapping[str, Any]) -> tuple[str, ...]:
    return tuple(
        lever_name
        for lever_name in REQUIRED_LEVERS
        if levers[lever_name] != baseline[lever_name]
    )


def _first_tag(tags: object) -> str:
    if isinstance(tags, (str, bytes)) or not isinstance(tags, Sequence) or not tags:
        return "balanced_judgment"
    first = tags[0]
    if not isinstance(first, str):
        return "balanced_judgment"
    return first


def _tag_label(tag: str) -> str:
    labels = {
        "equity_at_risk_promotion": "brand equity may be strained",
        "positioning_incoherent": "the move changes the story sharply",
        "discount_pressure": "discounting can weaken pricing power",
        "brand_coherence": "the brand story stays coherent",
        "unattributable_brand_spend": "the spend is hard to attribute",
        "measurable_response": "the action can move quickly",
        "efficiency_watch": "the efficiency case is not obvious",
        "bold_move": "the positioning stands apart",
        "too_safe": "the idea may be too cautious",
        "the_room_says": "the customer room is sending a signal",
        "moves_volume": "the channel can move volume",
        "channel_caution": "the channel case is thin",
        "raises_exposure": "the move raises exposure",
        "trust_buffer": "trust gives you some room",
        "trust_watch": "trust deserves attention",
    }
    return labels.get(tag, "the judgment needs balance")


def _finite_number(value: object, label: str) -> float:
    try:
        converted = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be a finite number") from exc
    if not isfinite(converted):
        raise ValueError(f"{label} must be a finite number")
    return converted
