"""Adapter to the REAL verified engine (active when vendor/ holds the shipping modules).

Signatures below were confirmed against the shipping modules in Implementation/. Several
differ from the reference flexee_service.py — those differences are handled here so the
Django app (which drives this adapter, not the reference spine) is correct:

  * customer_agents.focus_group needs Persona INSTANCES, not dicts.
  * customer_agents.stated_warmth(revealed, persona, gap_gain) takes a Persona; for the
    advisor context we need a SEGMENT-level signal, computed directly from SAYDO_GAP.
  * advisors.build_usage_record(team, round, advisor_IDS, decision) — ids, not stances.
  * advisors context must be EXACTLY {stated_warmth, eq, trust} (all 5 segments, [0,1]).
  * defense.assemble_defense_packet(team, history, decisions_by_round, fg, advisor) —
    team is the FIRST arg.
  * run_game_with_faculty returns a list of records, each embedding effective_decisions.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Optional

# All must import for the real engine to be selected (else api.py falls back to the stub).
from game_faculty import run_game_with_faculty  # noqa: F401
from crisis import CrisisParams
import faculty
from faculty import DEFAULT_FACULTY_CONFIG
import personas
import customer_agents
from customer_agents import Persona, SAYDO_GAP, GAP_GAIN, clip01
import advisors
import report as _report_mod
import defense as _defense_mod
import defense_grader as _grader_mod
import live_voice
import advisor_voice
from flexee_calibrated_config import (
    CALIBRATED_PARAMS, CALIBRATED_EQUITY_PARAMS, CALIBRATED_PROMOTION_PARAMS,
    CALIBRATED_CRISIS_DAMAGE_BASE,
)

SEGS = ("LEG", "FIT", "FUN", "GENZ", "MAIN")

POOL = [dict(p) for p in personas.POOL]
POOL_BY_ID = {p["id"]: p for p in POOL}
# advisors.ADVISORS is a tuple of (id, name) pairs — normalize to dicts for the app.
ADVISORS = [{"id": aid, "name": aname} for (aid, aname) in advisors.ADVISORS]


# --- Config translation: JSON dict <-> FacultyConfig ------------------------------
def _faculty_config(config: dict):
    fields = {}
    if "num_rounds" in config:
        fields["num_rounds"] = int(config["num_rounds"])
    if "takeover_round" in config:
        fields["takeover_round"] = int(config["takeover_round"])
    sched = config.get("lever_open_round") or config.get("lever_schedule")
    if sched:
        fields["lever_open_round"] = dict(sched)
    if config.get("baseline_decision"):
        fields["baseline_decision"] = dict(config["baseline_decision"])
    return replace(DEFAULT_FACULTY_CONFIG, **fields) if fields else DEFAULT_FACULTY_CONFIG


def _crisis_params(config: dict):
    return CrisisParams(
        crisis_round=int(config.get("crisis_round", 9)),
        damage_base=float(config.get("crisis_damage_base", CALIBRATED_CRISIS_DAMAGE_BASE)),
        trust_fraction=0.5, prod_fraction=0.3, eq_fraction=0.2,
        lower_bound=0.0, upper_bound=100.0,
    )


def default_config() -> dict:
    # Start from the stub's config so the app-layer keys (round_budget, price_band,
    # engagement_cap_per_round, engagement_saturation) are always present, then override
    # the engine-derived fields from the real DEFAULT_FACULTY_CONFIG.
    from . import _stub
    cfg = _stub.default_config()
    fc = DEFAULT_FACULTY_CONFIG
    lever_open = dict(getattr(fc, "lever_open_round", {}))
    baseline = dict(getattr(fc, "baseline_decision", {}))
    budget = sum(float(baseline.get(k, 0)) for k in ("brand", "perf", "research")) or 10.0
    cfg.update({
        "num_rounds": getattr(fc, "num_rounds", 14),
        "takeover_round": getattr(fc, "takeover_round", 4),
        "lever_open_round": lever_open,      # ENGINE levers only (faculty validates these keys)
        # App-layer schedule incl. Group B enrichment levers (engine never sees these keys).
        "lever_schedule": {**lever_open, "channel": 5, "message": 6, "line_extension": 7},
        "extra_lever_open_round": {"channel": 5, "message": 6, "line_extension": 7},
        "baseline_decision": baseline,
        "round_budget": budget,
        "crisis_round": 9,
        "crisis_damage_base": float(CALIBRATED_CRISIS_DAMAGE_BASE),
        "engine": "real",
    })
    return cfg


def open_levers_for_round(rnd: int, config: dict) -> list[str]:
    # third arg (params) defaults; lever opening derives from faculty_config.
    return list(faculty.open_levers_for_round(rnd, _faculty_config(config)))


def run_history(team_names, submissions_by_round, config,
                team_channel_by_round=None, team_message_by_round=None) -> list[dict]:
    subs = [{t: (row.get(t) or {}) for t in team_names} for row in submissions_by_round]
    group_b = {}
    if team_channel_by_round:
        group_b["team_channel_by_round"] = team_channel_by_round
    if team_message_by_round:
        group_b["team_message_by_round"] = team_message_by_round
    result = run_game_with_faculty(
        list(team_names), subs, _faculty_config(config),
        params=CALIBRATED_PARAMS, equity_params=CALIBRATED_EQUITY_PARAMS,
        promotion_params=CALIBRATED_PROMOTION_PARAMS,
        crisis_params=_crisis_params(config), crisis_inputs=config.get("crisis_inputs"),
        **group_b,
    )
    # Shipping runner returns a list of records that already embed effective_decisions.
    # (Tuple form handled defensively in case a future build returns (history, built).)
    if isinstance(result, tuple):
        history, built = result[0], result[1]
        for i, rec in enumerate(history):
            if isinstance(rec, dict) and "effective_decisions" not in rec and i < len(built):
                rec["effective_decisions"] = built[i]
    else:
        history = result
    return [_jsonable(rec) for rec in history]


def _jsonable(value):
    """Normalize engine records for JSON storage: sets/frozensets -> sorted lists,
    tuples -> lists, MappingProxy/dict -> dict (recursively)."""
    from types import MappingProxyType
    if isinstance(value, (set, frozenset)):
        return sorted(value)
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, (dict, MappingProxyType)):
        return {k: _jsonable(v) for k, v in value.items()}
    return value


# --- Customer agents --------------------------------------------------------------
def revealed_warmth(perception_by_firm, team, seg):
    return customer_agents.revealed_warmth(perception_by_firm, team, seg)


def stated_warmth(revealed, seg, idiosyncrasy: float = 0.0):
    """SEGMENT-level stated warmth (the room's signal). The module's stated_warmth needs a
    Persona; here we compute the segment signal directly from the locked constants."""
    return clip01(float(revealed) + GAP_GAIN * SAYDO_GAP[seg] + float(idiosyncrasy))


def segment_stated_warmth(perception_by_firm, team) -> dict:
    return {s: stated_warmth(revealed_warmth(perception_by_firm, team, s), s, 0.0)
            for s in SEGS}


def update_relationship(prev, revealed):
    return customer_agents.update_relationship(prev, revealed)


def focus_group(perception_by_firm, team, chosen_personas, relationships,
                prompt: Optional[str] = None, model_client=None):
    persona_objs = [Persona(p["id"], p["name"], p["segment"], float(p.get("idiosyncrasy", 0.0)))
                    for p in chosen_personas]
    # relationships must contain exactly the chosen persona ids
    rels = {p["id"]: float(relationships.get(p["id"], 0.5)) for p in chosen_personas}
    kwargs = {}
    if model_client is not None:
        kwargs["generator"] = live_voice.live_persona_generator(model_client, POOL_BY_ID)
    return customer_agents.focus_group(perception_by_firm, team, persona_objs, rels, **kwargs)


# --- Advisors ---------------------------------------------------------------------
def advise(decision_under_review, context, model_client=None):
    kwargs = {}
    if model_client is not None:
        kwargs["generator"] = advisor_voice.live_advisor_generator(model_client)
    return advisors.advise(decision_under_review, context, **kwargs)


def build_usage_record(team, rnd, stances, decision_under_review):
    fn = getattr(advisors, "build_usage_record", None)
    if fn is not None:
        try:
            ids = [s.get("advisor_id") for s in stances]
            return fn(team, rnd, ids, decision_under_review)
        except Exception:
            pass
    return {"team_id": team, "round": rnd,
            "consulted_advisors": [s.get("advisor_id") for s in stances],
            "consultation_count": len(stances)}


# --- Scorecard --------------------------------------------------------------------
def build_report(history):
    return _report_mod.build_report(history)


# --- Board defense ----------------------------------------------------------------
def assemble_defense_packet(history, decisions_by_round, team, focus_groups, advisor_consults):
    # real signature: (team, history, decisions_by_round, focus_group_evidence, advisor_usage)
    return _defense_mod.assemble_defense_packet(
        team, history, decisions_by_round, focus_groups, advisor_consults)


def build_defense(history, decisions_by_round, engagement):
    return _defense_mod.build_defense(history, decisions_by_round, engagement)


def grade_written_defense(defense_text, packet, model_client=None, cache=None):
    return _grader_mod.grade_written_defense(defense_text, packet, model_client, cache)


def combine_report(deterministic_team, prose):
    return _grader_mod.combine_report(deterministic_team or {}, prose or {})
