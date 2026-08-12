"""Spine factory — ILLUSTRATIVE ONLY (retired 2026-08-07, Vikram's call).

DECISION: `games.services.GameService` is the CANONICAL orchestration layer. The reference
`flexee_service.py` spine is kept as an illustrative pattern only — its AI/defense wiring was
never exercised against the shipping modules and has several signature bugs (persona dicts vs
Persona instances, `stated_warmth(rev, seg, 0.0)`, `build_usage_record(..stances..)`,
`assemble_defense_packet` arg order, `get_submissions_through` length). Do NOT rely on
`make_flexee_game`; build on `GameService`. Left here so a corrected reference can be produced
later if wanted. `DjangoStore` remains tested and reusable.

--- original docstring ---
Spine factory — build the FlexeeGame wired to the database.

This is the payoff of the store swap: once the verified engine (and the vendored
`flexee_service.py`) are importable, `make_flexee_game(game_row)` returns a fully-wired
`FlexeeGame` whose persistence is the Django DB via `DjangoStore`, with the shared model
client injected. Building the seven screens onto this spine means calling its verbs
(`submit`, `close_round`, `focus_group`, `consult_advisors`, `scorecard`, `board_defense`)
rather than reassembling the plumbing.

Until the engine is vendored, `engine_available()` is False and `make_flexee_game` raises
a clear, actionable error — the Django app runs on the seam's dev stub in the meantime.
"""

from __future__ import annotations


def engine_available() -> bool:
    """True once flexee_service.py and its engine modules are importable from vendor/."""
    try:
        import flexee_service  # noqa: F401
        return True
    except Exception:
        return False


def make_flexee_game(game, model_client=None):
    """Return a FlexeeGame backed by DjangoStore for this Game row.

    team_names come from the game's teams; config is derived from the persisted snapshot;
    the model client defaults to the shared one (offline unless a provider is configured).
    """
    if not engine_available():
        raise RuntimeError(
            "The verified engine is not vendored yet. Drop the modules into "
            "flexee_engine/vendor/ (see flexee_engine/vendor/README.md); until then use "
            "the dev stub via flexee_engine.api / games.services.GameService."
        )

    from flexee_service import FlexeeGame, GameConfig  # vendored
    from . import _real  # config translation lives here (engine dataclasses)
    from .api import get_model_client
    from .django_store import DjangoStore

    snapshot = game.config_snapshot or {}
    config = GameConfig(
        faculty_config=_real._faculty_config(snapshot),
        crisis_round=int(snapshot.get("crisis_round", 9)),
        crisis_damage_base=float(snapshot.get(
            "crisis_damage_base", _real.CALIBRATED_CRISIS_DAMAGE_BASE)),
        crisis_inputs=snapshot.get("crisis_inputs"),
    )
    team_names = list(game.teams.order_by("name").values_list("name", flat=True))
    return FlexeeGame(team_names, config=config,
                      model_client=model_client or get_model_client(),
                      store=DjangoStore(game))
