from __future__ import annotations

from collections.abc import Mapping, Sequence
from math import isfinite
from statistics import mean
from types import MappingProxyType
from typing import Any

from crisis import CrisisParams
from engine import DEFAULT_PARAMS, SimulationParams
from equity import DEFAULT_EQUITY_PARAMS, EquityParams
from game import run_game
from promotion import DEFAULT_PROMOTION_PARAMS, PromotionParams
from simulation import DEFAULT_GROWTH_RATES


ARCHETYPE_ORDER = ("HERITAGE", "PIVOT", "PROMO", "BALANCED", "PERF")

ARCHETYPES = MappingProxyType({
    "HERITAGE": MappingProxyType({
        "brand": 6.0,
        "perf": 2.0,
        "research": 2.0,
        "positioning": 18.0,
        "emphasis": (0.45, 0.15, 0.05, 0.05, 0.30),
        "price": 1.50,
        "promotion": 0.0,
    }),
    "PIVOT": MappingProxyType({
        "brand": 4.0,
        "perf": 3.0,
        "research": 3.0,
        "positioning": 82.0,
        "emphasis": (0.05, 0.15, 0.40, 0.30, 0.10),
        "price": 1.55,
        "promotion": 0.1,
    }),
    "PROMO": MappingProxyType({
        "brand": 3.0,
        "perf": 5.0,
        "research": 2.0,
        "positioning": 45.0,
        "emphasis": (0.25, 0.10, 0.10, 0.15, 0.40),
        "price": 1.20,
        "promotion": 0.9,
    }),
    "BALANCED": MappingProxyType({
        "brand": 4.0,
        "perf": 3.0,
        "research": 3.0,
        "positioning": 50.0,
        "emphasis": (0.20, 0.20, 0.20, 0.20, 0.20),
        "price": 1.50,
        "promotion": 0.0,
    }),
    "PERF": MappingProxyType({
        "brand": 2.0,
        "perf": 6.0,
        "research": 2.0,
        "positioning": 70.0,
        "emphasis": (0.10, 0.15, 0.30, 0.35, 0.10),
        "price": 1.55,
        "promotion": 0.3,
    }),
})

CRISIS_INPUTS = MappingProxyType({
    "HERITAGE": MappingProxyType({"exposure": 0.8, "preempt": 0.1, "response": 0.3}),
    "PIVOT": MappingProxyType({"exposure": 0.4, "preempt": 0.6, "response": 0.7}),
    "PROMO": MappingProxyType({"exposure": 0.6, "preempt": 0.2, "response": 0.3}),
    "BALANCED": MappingProxyType({"exposure": 0.5, "preempt": 0.4, "response": 0.5}),
    "PERF": MappingProxyType({"exposure": 0.6, "preempt": 0.3, "response": 0.4}),
})


def run_battery(
    num_rounds: int = 14,
    crisis_round: int = 9,
    params: SimulationParams = DEFAULT_PARAMS,
    equity_params: EquityParams = DEFAULT_EQUITY_PARAMS,
    promotion_params: PromotionParams = DEFAULT_PROMOTION_PARAMS,
) -> list[dict[str, Any]]:
    """Run the locked calibration archetype battery without tuning parameters."""
    if not isinstance(num_rounds, int) or num_rounds <= 0:
        raise ValueError("num_rounds must be a positive integer")
    if isinstance(num_rounds, bool):
        raise ValueError("num_rounds must be a positive integer")
    if not isinstance(crisis_round, int) or crisis_round <= 0:
        raise ValueError("crisis_round must be a positive integer")
    if isinstance(crisis_round, bool):
        raise ValueError("crisis_round must be a positive integer")

    decisions_by_round = [_decisions_for_round() for _ in range(num_rounds)]
    crisis_params = CrisisParams(
        crisis_round=crisis_round,
        damage_base=25.0,
        trust_fraction=0.5,
        prod_fraction=0.3,
        eq_fraction=0.2,
        lower_bound=0.0,
        upper_bound=100.0,
    )
    return run_game(
        decisions_by_round,
        params=params,
        growth_rates=DEFAULT_GROWTH_RATES,
        equity_params=equity_params,
        crisis_params=crisis_params,
        promotion_params=promotion_params,
        crisis_inputs=CRISIS_INPUTS,
    )


def telemetry(history: Sequence[Mapping[str, Any]]) -> dict[str, object]:
    """Summarize finished calibration history into deterministic diagnostics."""
    records = _validated_history(history)
    final_record = records[-1]
    final_overall = {
        team: _finite(_required(_team_scorecard(final_record, team), "overall", f"{team} scorecard"), f"{team}.overall")
        for team in ARCHETYPE_ORDER
    }
    final_share_pct = {
        team: _finite(
            _required(_team_firm(final_record, team), "total_market_share", f"{team} firm record"),
            f"{team}.total_market_share",
        )
        * 100.0
        for team in ARCHETYPE_ORDER
    }
    final_contribution = {
        team: _finite(
            _required(_team_firm(final_record, team), "contribution", f"{team} firm record"),
            f"{team}.contribution",
        )
        for team in ARCHETYPE_ORDER
    }
    final_equity_mapping = _required_mapping(final_record, "equity", "final history record")
    final_equity = {
        team: _finite(_required(final_equity_mapping, team, "final equity"), f"{team}.equity")
        for team in ARCHETYPE_ORDER
    }
    _validate_attribute_state_equity(final_record, final_equity)
    final_overall_spread = max(final_overall.values()) - min(final_overall.values())

    crisis_index = _crisis_index(records)
    mean_crisis_share_drop = 0.0
    if crisis_index is not None and crisis_index > 0:
        previous_record = records[crisis_index - 1]
        crisis_record = records[crisis_index]
        mean_crisis_share_drop = mean(
            (
                _finite(
                    _required(_team_firm(previous_record, team), "total_market_share", f"{team} pre-crisis firm"),
                    f"{team}.pre_crisis_share",
                )
                - _finite(
                    _required(_team_firm(crisis_record, team), "total_market_share", f"{team} crisis firm"),
                    f"{team}.crisis_share",
                )
            )
            * 100.0
            for team in ARCHETYPE_ORDER
        )

    return {
        "final_overall": final_overall,
        "final_overall_spread": final_overall_spread,
        "final_share_pct": final_share_pct,
        "final_contribution": final_contribution,
        "final_equity": final_equity,
        "heritage_trap_present": final_overall["PIVOT"] > final_overall["HERITAGE"],
        "promo_punished": (
            final_overall["PROMO"] < final_overall["PIVOT"]
            and final_contribution["PROMO"] < final_contribution["PIVOT"]
        ),
        "stuck_in_middle_present": (
            final_overall["BALANCED"] < max(final_overall["HERITAGE"], final_overall["PIVOT"])
        ),
        "promo_equity_erosion": final_equity["PROMO"] < final_equity["PIVOT"],
        "mean_crisis_share_drop": mean_crisis_share_drop,
    }


def _decisions_for_round() -> dict[str, dict[str, object]]:
    return {
        team: {
            key: value
            for key, value in decision.items()
        }
        for team, decision in ARCHETYPES.items()
    }


def _validated_history(history: object) -> list[Mapping[str, Any]]:
    if isinstance(history, (str, bytes)) or not isinstance(history, Sequence):
        raise ValueError("history must be a nonempty sequence")
    if len(history) == 0:
        raise ValueError("history must be a nonempty sequence")

    records: list[Mapping[str, Any]] = []
    seen_rounds: set[int] = set()
    for index, raw_record in enumerate(history):
        record = _record(raw_record, f"history record {index + 1}")
        if "round" not in record:
            raise ValueError(f"history record {index + 1} is missing round")
        round_number = record["round"]
        if isinstance(round_number, bool) or not isinstance(round_number, int):
            raise ValueError(f"history record {index + 1} round must be an integer")
        if round_number in seen_rounds:
            raise ValueError(f"duplicate history round: {round_number}")
        seen_rounds.add(round_number)

        _required_mapping(record, "firms", f"history record {index + 1}")
        scorecard = _required_mapping(record, "scorecard", f"history record {index + 1}")
        _required_mapping(scorecard, "firms", f"history record {index + 1} scorecard")
        _required_mapping(record, "damage", f"history record {index + 1}")
        records.append(record)
    return records


def _crisis_index(history: Sequence[Mapping[str, Any]]) -> int | None:
    crisis_index: int | None = None
    for index, record in enumerate(history):
        damage = _required_mapping(record, "damage", f"history record {index + 1}")
        _validate_damage(damage, f"history record {index + 1} damage")
        if len(damage) > 0:
            if crisis_index is not None:
                raise ValueError("history must contain at most one nonempty damage round")
            crisis_index = index
    return crisis_index


def _team_firm(record: Mapping[str, Any], team: str) -> Mapping[str, Any]:
    firms = _required_mapping(record, "firms", "record")
    return _mapping(_required(firms, team, "record firms"), f"{team} firm record")


def _team_scorecard(record: Mapping[str, Any], team: str) -> Mapping[str, Any]:
    scorecard = _required_mapping(record, "scorecard", "record")
    firms = _required_mapping(scorecard, "firms", "record scorecard")
    return _mapping(_required(firms, team, "record scorecard firms"), f"{team} scorecard")


def _validate_attribute_state_equity(record: Mapping[str, Any], final_equity: Mapping[str, float]) -> None:
    if "attribute_state" not in record:
        return
    attribute_state = _mapping(record["attribute_state"], "final attribute_state")
    for team, equity in final_equity.items():
        team_state = _required_mapping(attribute_state, team, "final attribute_state")
        attribute_eq = _finite(_required(team_state, "EQ", f"{team} attribute_state"), f"{team}.attribute_state.EQ")
        if attribute_eq != equity:
            raise ValueError(f"{team}.attribute_state.EQ must match final equity")


def _validate_damage(damage: Mapping[str, Any], label: str) -> None:
    for team, value in damage.items():
        if not isinstance(team, str) or team == "":
            raise ValueError(f"{label} team names must be nonempty strings")
        _finite(value, f"{label}.{team}")


def _record(value: object, label: str) -> Mapping[str, Any]:
    return _mapping(value, label)


def _required_mapping(container: Mapping[str, Any], key: str, label: str) -> Mapping[str, Any]:
    return _mapping(_required(container, key, label), f"{label} {key}")


def _required(container: Mapping[str, Any], key: str, label: str) -> Any:
    if key not in container:
        raise ValueError(f"{label} is missing {key}")
    return container[key]


def _mapping(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be a mapping")
    return value


def _finite(value: object, label: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{label} must be a finite number")
    try:
        converted = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be a finite number") from exc
    if not isfinite(converted):
        raise ValueError(f"{label} must be a finite number")
    return converted
