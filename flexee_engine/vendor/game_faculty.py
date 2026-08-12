from __future__ import annotations

from copy import deepcopy
from typing import Mapping, Sequence, TypedDict

from crisis import DEFAULT_CRISIS_PARAMS, CrisisInputs, CrisisParams
from engine import DEFAULT_PARAMS, SimulationParams
from equity import DEFAULT_EQUITY_PARAMS, EquityParams
from faculty import (
    DEFAULT_FACULTY_CONFIG,
    EffectiveRound,
    FacultyConfig,
    RoundSubmissions,
    build_effective_decisions,
    open_levers_for_round,
)
from game import GameRoundRecord, run_game
from promotion import DEFAULT_PROMOTION_PARAMS, PromotionParams
from simulation import DEFAULT_GROWTH_RATES


class GameFacultyRoundRecord(GameRoundRecord):
    open_levers: frozenset[str]
    effective_decisions: EffectiveRound


GameFacultyHistory = list[GameFacultyRoundRecord]


def run_game_with_faculty(
    team_names: Sequence[str],
    submissions_by_round: Sequence[RoundSubmissions],
    faculty_config: FacultyConfig = DEFAULT_FACULTY_CONFIG,
    params: SimulationParams = DEFAULT_PARAMS,
    growth_rates: Mapping[str, float] = DEFAULT_GROWTH_RATES,
    equity_params: EquityParams = DEFAULT_EQUITY_PARAMS,
    promotion_params: PromotionParams = DEFAULT_PROMOTION_PARAMS,
    crisis_params: CrisisParams = DEFAULT_CRISIS_PARAMS,
    crisis_inputs: CrisisInputs | None = None,
) -> GameFacultyHistory:
    """Run faculty-gated decisions through the integrated game-state runner."""
    effective_decisions_by_round = build_effective_decisions(
        team_names,
        submissions_by_round,
        faculty_config,
        params,
    )
    game_history = run_game(
        effective_decisions_by_round,
        params,
        growth_rates,
        equity_params,
        crisis_params,
        promotion_params,
        crisis_inputs,
    )

    return [
        {
            **record,
            "open_levers": open_levers_for_round(record["round"], faculty_config, params),
            "effective_decisions": deepcopy(effective_decisions_by_round[record["round"] - 1]),
        }
        for record in game_history
    ]
