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

# Group B (enrichment) — convert app-layer channel/message choices into the per-segment
# reach multiplier / perception bonus the engine consumes. Neutral inputs => base engine.
from channel import channel_reach
from message import message_fit as _message_fit


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
    team_channel_by_round: Sequence[Mapping[str, Mapping[str, float]]] | None = None,
    team_message_by_round: Sequence[Mapping[str, str]] | None = None,
) -> GameFacultyHistory:
    """Run faculty-gated decisions through the integrated game-state runner.

    Group B (optional, neutral by default): `team_channel_by_round` gives each team's
    channel weights per round and `team_message_by_round` its creative choice; these are
    converted to per-segment reach/perception inputs and threaded into the runner. Omit
    them (or pass neutral) to reproduce the base engine exactly.
    """
    effective_decisions_by_round = build_effective_decisions(
        team_names,
        submissions_by_round,
        faculty_config,
        params,
    )
    # Only add the Group B kwargs when actually present, so the neutral call to run_game
    # is byte-identical to the base engine (keeps the wiring golden green).
    group_b: dict = {}
    if team_channel_by_round:
        group_b["team_reach_by_round"] = [
            {team: channel_reach(weights) for team, weights in (per_round or {}).items()}
            for per_round in team_channel_by_round
        ]
    if team_message_by_round:
        group_b["team_message_fit_by_round"] = [
            {team: _message_fit(msg) for team, msg in (per_round or {}).items()}
            for per_round in team_message_by_round
        ]
    game_history = run_game(
        effective_decisions_by_round,
        params,
        growth_rates,
        equity_params,
        crisis_params,
        promotion_params,
        crisis_inputs,
        **group_b,
    )

    return [
        {
            **record,
            "open_levers": open_levers_for_round(record["round"], faculty_config, params),
            "effective_decisions": deepcopy(effective_decisions_by_round[record["round"] - 1]),
        }
        for record in game_history
    ]
