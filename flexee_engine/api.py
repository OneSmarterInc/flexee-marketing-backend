"""Single import point between Django and the Flexee engine.

Selects the real verified engine when available, else the deterministic dev stub, and
injects the shared LLM model client into the AI voice layers so callers never handle it.
Import ONLY from here in Django code (never from _real / _stub / engine internals).
"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any, Optional

log = logging.getLogger("flexee.engine")

# --- Select implementation --------------------------------------------------------
try:
    from . import _real as _impl  # noqa: F401  (raises ImportError if vendor absent)
    ENGINE_MODE = "real"
except Exception as exc:  # engine not vendored yet -> deterministic stub
    from . import _stub as _impl
    ENGINE_MODE = "stub"
    log.info("flexee_engine: using dev STUB (real engine not found: %s)", exc)
else:
    log.info("flexee_engine: using REAL verified engine")

SEGS = _impl.SEGS


# --- Shared model client (LLM voice layer) ----------------------------------------
# Env var holding the API key for each live provider. Used to degrade to the offline
# client when the key is absent — the vendor SDKs construct fine without a key and only
# fail at request time, which would otherwise crash focus-group/advisor/defense calls.
_PROVIDER_KEY_ENV = {"anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY"}


@lru_cache(maxsize=1)
def get_model_client():
    """One shared model client per process, per the handoff.

    Defaults (settings) to Anthropic on the cheapest model (Claude Haiku 4.5). Falls
    back to the deterministic offline client whenever the chosen provider's SDK or API
    key is missing, so the app runs keyless in dev/CI without runtime crashes.
    """
    import os

    from django.conf import settings
    from .model_client import make_model_client

    provider = (getattr(settings, "FLEXEE_LLM_PROVIDER", "offline") or "offline").lower()
    model = getattr(settings, "FLEXEE_LLM_MODEL", "") or None

    key_env = _PROVIDER_KEY_ENV.get(provider)
    if key_env and not os.environ.get(key_env):
        log.warning("%s not set; LLM voice layer using offline client", key_env)
        return make_model_client(provider="offline")

    try:
        return make_model_client(provider=provider, model=model)
    except Exception as exc:  # missing SDK -> fall back to offline so the app runs
        log.warning("model client %r unavailable (%s); using offline", provider, exc)
        return make_model_client(provider="offline")


# --- Config -----------------------------------------------------------------------
def default_config() -> dict:
    return _impl.default_config()


def open_levers_for_round(rnd: int, config: dict) -> list[str]:
    return list(_impl.open_levers_for_round(rnd, config))


# --- Personas / advisors (reference data) -----------------------------------------
def persona_pool() -> list[dict]:
    """Full pool WITH segments — faculty/internal use only, never sent to students."""
    return list(_impl.POOL)


def persona_records() -> list[dict]:
    """Student-safe: id + name only (never segment)."""
    return [{"id": p["id"], "name": p["name"]} for p in _impl.POOL]


def persona_by_id() -> dict:
    return {p["id"]: p for p in _impl.POOL}


def advisor_roster() -> list[dict]:
    return [{"id": a["id"], "name": a["name"], "role": a.get("role", "")}
            for a in getattr(_impl, "ADVISORS", [])]


# --- Runner -----------------------------------------------------------------------
def run_history(team_names, submissions_by_round, config,
                team_channel_by_round=None, team_message_by_round=None) -> list[dict]:
    extra = {}
    if team_channel_by_round:
        extra["team_channel_by_round"] = team_channel_by_round
    if team_message_by_round:
        extra["team_message_by_round"] = team_message_by_round
    return _impl.run_history(team_names, submissions_by_round, config, **extra)


# --- Customer agents --------------------------------------------------------------
def revealed_warmth(perception_by_firm, team, seg):
    return _impl.revealed_warmth(perception_by_firm, team, seg)


def stated_warmth(revealed, seg, idiosyncrasy_skew: float = 0.0):
    return _impl.stated_warmth(revealed, seg, idiosyncrasy_skew)


def segment_stated_warmth(perception_by_firm, team) -> dict:
    """Per-segment stated-warmth signal for the advisor context (all 5 segments, [0,1])."""
    return _impl.segment_stated_warmth(perception_by_firm, team)


def update_relationship(prev, revealed):
    return _impl.update_relationship(prev, revealed)


_STUDENT_SAFE_FG = ("persona_id", "persona_name", "stated_warmth", "relationship", "text")


def _warmth_band(stated) -> str:
    """[0,1] stated warmth -> display band. The engine returns a raw float; students see
    only a band (never the raw number, revealed warmth, or segment)."""
    try:
        s = float(stated)
    except (TypeError, ValueError):
        return str(stated)  # already a band
    if s >= 0.6:
        return "warm"
    if s <= 0.4:
        return "cool"
    return "mixed"


# Passing model_client=None to a voice call is the engine's own no-LLM path (its deterministic
# templated voice); leaving it out uses the shared client. The app layer passes None when a game's
# AI is switched off.
_SHARED = object()


def focus_group(perception_by_firm, team, chosen_personas, relationships,
                prompt: Optional[str] = None, model_client=_SHARED) -> list[dict]:
    raw = _impl.focus_group(perception_by_firm, team, chosen_personas, relationships, prompt=prompt,
                            model_client=get_model_client() if model_client is _SHARED else model_client)
    # App-layer guardrail: band the warmth and strip anything not student-safe (defense in
    # depth on top of the engine's own omission of segment/revealed warmth).
    safe = []
    for r in raw:
        rec = {k: r[k] for k in _STUDENT_SAFE_FG if k in r}
        if "stated_warmth" in rec:
            rec["stated_warmth"] = _warmth_band(rec["stated_warmth"])
        if "relationship" in rec:
            rec["relationship"] = round(float(rec["relationship"]), 3)
        safe.append(rec)
    return safe


# --- Advisors ---------------------------------------------------------------------
def advise(decision_under_review, context, model_client=_SHARED) -> list[dict]:
    return _impl.advise(decision_under_review, context,
                        model_client=get_model_client() if model_client is _SHARED else model_client)


def build_usage_record(team, rnd, stances, decision_under_review):
    return _impl.build_usage_record(team, rnd, stances, decision_under_review)


# --- Scorecard --------------------------------------------------------------------
def build_report(history) -> dict:
    return _impl.build_report(history)


# --- Board defense ----------------------------------------------------------------
def assemble_defense_packet(history, decisions_by_round, team, focus_groups, advisor_consults):
    return _impl.assemble_defense_packet(history, decisions_by_round, team,
                                         focus_groups, advisor_consults)


def build_defense(history, decisions_by_round, engagement):
    return _impl.build_defense(history, decisions_by_round, engagement)


def grade_written_defense(defense_text, packet, cache: Optional[dict] = None, model_client=_SHARED):
    return _impl.grade_written_defense(defense_text, packet,
                                       model_client=get_model_client() if model_client is _SHARED else model_client,
                                       cache=cache)


def live_model_available() -> bool:
    """True when the shared client is a real vendor client (not the offline stand-in)."""
    from .model_client import OfflineModelClient
    return not isinstance(get_model_client(), OfflineModelClient)


def combine_report(deterministic_team, prose):
    return _impl.combine_report(deterministic_team, prose)


# --- Financials (reporting overlay over existing engine outputs) -------------------
def _num(d: dict) -> dict:
    """Coerce a statement's Decimal values to float for JSON, rounded to cents."""
    return {k: (round(float(v), 2) if hasattr(v, "__float__") else v) for k, v in d.items()}


# --- Market research (Group C — rendering over engine state, no feedback) --------
def research_catalog() -> dict:
    import research as r
    return {k: {"cost_stated": float(v["cost_stated"]), "cost_behavioral": float(v["cost_behavioral"])}
            for k, v in r.STUDY_CATALOG.items()}


def render_study(study, mode, ctx):
    import research as r
    return r.render_study(study, mode, ctx)


def where_to_grow(ctx, mode):
    import research as r
    return r.where_to_grow(ctx, mode)


def line_extension_effect(launch_state, core_volume, segment_sizes, core_positioning):
    """Group B line-extension overlay (incremental − cannibalization + crisis credit).
    App-layer overlay over a computed round; nothing feeds back into the engine."""
    import line_extension as le
    return le.line_extension_effect(launch_state, core_volume, segment_sizes, core_positioning)


def financials_statements(mkts: list[dict]) -> list[dict]:
    """Roll the three financial statements across a team's per-round marketing outputs.

    `mkts` is an ordered list, one per closed round, each a dict of $ values:
    {gross_revenue, promo_allow, brand_spend, perf_spend, research_spend}. Uses the
    vendored, golden-locked `financials` module (brand-building capitalized & amortized;
    perf/research expensed). Returns [{round, income, cash_flow, balance}] as floats.
    """
    import financials as fin  # vendored (on sys.path via flexee_engine/__init__)

    if not mkts:
        return []
    prev = fin.build_opening_state(mkts[0])
    out = []
    for i, mkt in enumerate(mkts, start=1):
        income, cash_flow, balance, state = fin.round_financials(prev, mkt)
        out.append({
            "round": i,
            "income": _num(income),
            "cash_flow": _num(cash_flow),
            "balance": _num(balance),
        })
        prev = state
    return out
