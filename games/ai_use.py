"""The house rules every AI feature runs under.

- Every call to Claude is logged (AiCall): which feature, which game, tokens, cost, outcome.
- One monthly ceiling for the whole site: once this calendar month's spend (UTC) reaches it,
  every AI feature stops until the month turns over.
- Each game has a switch (Game.ai_enabled). Off means no AI for that game.

A feature asks `state(game)` before it calls Claude and records what it spent. The voice layers
(focus groups, advisors, the written-defense grade) go through `session()`, which does both: when
AI is off for the game it hands them no model client at all, which is the engine's own
deterministic path, and when it is on it measures the tokens the calls used.
"""
from __future__ import annotations

import logging
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone

from django.conf import settings
from django.db.models import Sum

from flexee_engine import api, model_client as mc
from . import models

log = logging.getLogger("flexee.ai")

# List prices in US dollars per million tokens, matched by model-name prefix. A model that is not
# listed is priced at the highest rate, so a wrong price stops spending early rather than late.
# Override with FLEXEE_LLM_INPUT_USD_PER_MTOK / FLEXEE_LLM_OUTPUT_USD_PER_MTOK.
MODEL_PRICES = (
    ("claude-haiku", 1.0, 5.0),
    ("claude-sonnet", 3.0, 15.0),
    ("claude-opus", 15.0, 75.0),
)
FALLBACK_PRICE = (15.0, 75.0)

# on, or why not
ON, OFF_SWITCH, OFF_CEILING, UNAVAILABLE = "on", "off_switch", "off_ceiling", "unavailable"

# What the game's switch is called in the app, in words.
FEATURE_NAMES = {
    "ask_claude": "Ask Claude", "focus_group": "focus group",
    "advisors": "advisor panel", "defense_grade": "written-defense grade",
}


def _setting(name, default=None):
    return getattr(settings, name, default)


def ceiling_usd() -> float:
    return float(_setting("FLEXEE_AI_MONTHLY_CEILING_USD", 50))


def price_for(model: str, input_override=None, output_override=None) -> tuple[float, float]:
    listed = next(((i, o) for prefix, i, o in MODEL_PRICES if (model or "").startswith(prefix)), FALLBACK_PRICE)
    return (float(input_override) if input_override is not None else listed[0],
            float(output_override) if output_override is not None else listed[1])


def _env_price(name):
    import os
    raw = os.environ.get(name, "").strip()
    try:
        value = float(raw)
    except ValueError:
        return None
    return value if value >= 0 else None


def call_cost(input_tokens: int, output_tokens: int, model: str, input_usd=None, output_usd=None) -> float:
    """Cost of one call in US dollars, to a millionth of a dollar."""
    pin, pout = price_for(model,
                          input_usd if input_usd is not None else _env_price("FLEXEE_LLM_INPUT_USD_PER_MTOK"),
                          output_usd if output_usd is not None else _env_price("FLEXEE_LLM_OUTPUT_USD_PER_MTOK"))
    return round((input_tokens * pin + output_tokens * pout) / 1e6, 6)


def month_start(now: datetime | None = None) -> datetime:
    now = now or datetime.now(timezone.utc)
    return datetime(now.year, now.month, 1, tzinfo=timezone.utc)


def site_spend(now: datetime | None = None) -> float:
    """This calendar month's spend on the whole site, in US dollars."""
    total = models.AiCall.objects.filter(created_at__gte=month_start(now)).aggregate(t=Sum("cost_usd"))["t"]
    return round(float(total or 0), 2)


def state(game, now: datetime | None = None, *, needs_key=True) -> str:
    """Whether this game may call Claude now. Ask before every call."""
    if needs_key and not api.live_model_available():
        return UNAVAILABLE
    if game is not None and not game.ai_enabled:
        return OFF_SWITCH
    if site_spend(now) >= ceiling_usd():
        return OFF_CEILING
    return ON


def record_call(game, feature: str, *, outcome="ok", input_tokens=0, output_tokens=0,
                cost_usd=0.0, sentences_dropped=0) -> None:
    """Log one call. Never raises: a logging failure must not lose the answer."""
    try:
        models.AiCall.objects.create(
            game_id_ref=getattr(game, "pk", None), course=getattr(game, "name", None),
            feature=feature, outcome=outcome, sentences_dropped=sentences_dropped,
            input_tokens=int(input_tokens), output_tokens=int(output_tokens),
            cost_usd=float(cost_usd), practice=bool(getattr(game, "is_practice", False)))
    except Exception:  # pragma: no cover
        log.exception("could not record an AI call for %s", feature)


@dataclass
class Session:
    """What a voice feature gets: the client to pass to the engine (None = deterministic path)."""
    client: object
    state: str


@contextmanager
def session(game, feature: str):
    """Wrap a voice-layer feature. Gives it the right client for the game's state, and logs the
    tokens and cost of the live calls it made (one AiCall row per feature use)."""
    if game is not None and not game.ai_enabled:     # the game's switch wins over everything
        yield Session(None, OFF_SWITCH)
        return
    st = state(game)
    if st in (OFF_SWITCH, OFF_CEILING):
        yield Session(None, st)                      # the engine's deterministic voice; no spend
        return
    if st == UNAVAILABLE:
        yield Session(api.get_model_client(), st)    # the offline stand-in, as before; nothing to log
        return
    failed = False
    with mc.collect_usage() as sink:
        try:
            yield Session(api.get_model_client(), st)
        except mc.ModelCallFailed:
            failed = True
            raise
        finally:
            if sink or failed:
                tin = sum(u["input_tokens"] for u in sink)
                tout = sum(u["output_tokens"] for u in sink)
                cost = sum(call_cost(u["input_tokens"], u["output_tokens"], u["model"]) for u in sink)
                record_call(game, feature, outcome="error" if failed else "ok",
                            input_tokens=tin, output_tokens=tout, cost_usd=round(cost, 6))
