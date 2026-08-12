"""Deterministic DEV STUB of the Flexee engine.

NOT the real engine and NOT calibrated. Its only job is to return outputs in the exact
shapes the UI data contracts specify, deterministically, so the Django API runs end to
end before the verified engine is dropped into ``flexee_engine/vendor/`` (see README).

Everything here is pure: same inputs -> same outputs, no randomness, no I/O. That mirrors
the real runner's contract, so the "re-derive history from submissions each round-close"
orchestration behaves identically against the stub.
"""

from __future__ import annotations

import math
from typing import Any, Optional

SEGS = ("LEG", "FIT", "FUN", "GENZ", "MAIN")

# --- Market backdrop (arbitrary but plausible; NOT calibrated) --------------------
SEGMENT_BASE = {"LEG": 300.0, "FIT": 200.0, "FUN": 250.0, "GENZ": 150.0, "MAIN": 400.0}
SEGMENT_GROWTH = {"LEG": -0.03, "FIT": 0.02, "FUN": 0.08, "GENZ": 0.06, "MAIN": 0.01}
# Deal sensitivity by segment (from the promotion task chunk).
DEAL_SENSITIVITY = {"LEG": 0.70, "FIT": 0.20, "FUN": 0.05, "GENZ": 0.80, "MAIN": 1.00}

# Fixed competitors the teams contend with (Red Bull / Monster / functional challenger).
BACKDROP = {
    "IncumbentA": {"LEG": 62, "FIT": 55, "FUN": 40, "GENZ": 58, "MAIN": 66},
    "IncumbentB": {"LEG": 58, "FIT": 52, "FUN": 45, "GENZ": 62, "MAIN": 64},
    "Challenger": {"LEG": 40, "FIT": 60, "FUN": 70, "GENZ": 66, "MAIN": 50},
}

REFERENCE_PRICE = 1.50
VARIABLE_COST = 0.60
PROMO_COST_RATE = 0.20
B_PROMO = 2.5           # equity debit per unit depth
LP = 8.0                # promotion perception lift coefficient
VDC = 4.0               # deal-conditioning perception penalty
RHO = 0.30              # DC decay
ETA = 0.50              # DC gain

# --- Personas: the real 28-persona pool -------------------------------------------
# Prefer the authoritative personas.py (vendored, pure data). Its `idiosyncrasy` is a
# FLOAT say-do offset (not text). Fall back to a tiny synthetic pool only if absent.
try:
    from personas import POOL as _REAL_POOL  # authoritative, hash-verified
    POOL = [dict(p) for p in _REAL_POOL]
except Exception:  # pragma: no cover - fallback keeps the stub runnable in isolation
    POOL = [
        {"id": f"{seg.lower()}_{i}", "name": f"{seg}{i}", "segment": seg,
         "idiosyncrasy": 0.0}
        for seg in SEGS for i in range(1, 6)
    ]

POOL_BY_ID = {p["id"]: p for p in POOL}

# --- Advisors: the real six (ids/names per the verified advisors module) -----------
ADVISORS = [
    {"id": "elena", "name": "Elena Vasquez", "role": "Brand Strategist",
     "bias": "protect the base and heritage equity"},
    {"id": "ray", "name": "Ray Sullivan", "role": "Performance Marketing Chief",
     "bias": "chase measurable growth and margin"},
    {"id": "jax", "name": "Jax Turner", "role": "Creative Director",
     "bias": "push bold, distinctive repositioning"},
    {"id": "anita", "name": "Anita Bose", "role": "Consumer Insights Lead",
     "bias": "faithfully reports the room's stated warmth (inherits the say-do gap)"},
    {"id": "bud", "name": "Bud Hollis", "role": "Head of Sales",
     "bias": "volume, deal-sensitive segments, and promotion now"},
    {"id": "tom", "name": "Tom Delgado", "role": "Risk & Compliance",
     "bias": "flag exposure and downside no one else is watching"},
]

# --- Default faculty config (JSON-serializable contract) --------------------------
DEFAULT_LEVER_SCHEDULE = {
    "allocation": 4,     # brand/perf/research split opens at takeover
    "emphasis": 4,       # segment targeting emphasis
    "positioning": 5,
    "promotion": 10,
    "price": 12,         # the most abusable lever opens last
}
DEFAULT_SCORECARD_WEIGHTS = {
    "brand_health": 0.25,
    "customer_value": 0.25,
    "market_position": 0.25,
    "financial": 0.25,
}


def default_config() -> dict:
    """A JSON-serializable configuration snapshot (the contract Django persists)."""
    return {
        "num_rounds": 14,
        "takeover_round": 4,
        "round_budget": 10.0,
        "price_band": [1.10, 1.90],
        "engagement_cap_per_round": 3,     # faculty-set cap on engagement actions per round
        "engagement_half_saturation": 6.0,  # hyperbolic diminishing-returns knee for the grade
        "lever_schedule": dict(DEFAULT_LEVER_SCHEDULE),
        "scorecard_weights": dict(DEFAULT_SCORECARD_WEIGHTS),
        "crisis_round": 9,
        "crisis_damage_base": 25.0,
        "reveal_policy": {"mode": "anonymous", "reveal_round": None},
        "param_set": "calibrated",
        "engine": "stub",
    }


LEVER_ORDER = ["allocation", "emphasis", "positioning", "promotion", "price"]


def open_levers_for_round(rnd: int, config: dict) -> list[str]:
    """Which levers the student round screen should show this round."""
    takeover = int(config.get("takeover_round", 4))
    if rnd < takeover:
        return []  # listening period: no market-moving decisions
    schedule = config.get("lever_schedule", DEFAULT_LEVER_SCHEDULE)
    return [lever for lever in LEVER_ORDER if rnd >= int(schedule.get(lever, takeover))]


# --- Decision baseline / normalization --------------------------------------------
def _baseline_decision() -> dict:
    # Matches the verified faculty BASELINE (brand 5, perf 2, research 3; pos 35).
    return {
        "brand": 5.0, "perf": 2.0, "research": 3.0,   # sum = budget (10)
        "positioning": 35.0,                           # 0=heritage .. 100=functional
        "emphasis": [0.20, 0.20, 0.20, 0.20, 0.20],
        "price": REFERENCE_PRICE,
        "promotion": 0.0,
    }


def _merge_decision(submitted: Optional[dict]) -> dict:
    base = _baseline_decision()
    if submitted:
        for k, v in submitted.items():
            if v is not None:
                base[k] = v
    # normalize emphasis to a 5-list summing to 1
    emph = base.get("emphasis") or [0.2] * 5
    emph = list(emph)[:5] + [0.0] * (5 - len(list(emph)))
    total = sum(emph) or 1.0
    base["emphasis"] = [e / total for e in emph]
    return base


# --- Core deterministic simulation ------------------------------------------------
def _perception(team_dec: dict, eq: float, seg: str, seg_index: int,
                entering_dc: float) -> float:
    """A plausible, deterministic per-segment perception (NOT calibrated)."""
    budget = team_dec["brand"] + team_dec["perf"] + team_dec["research"]
    brand_share = team_dec["brand"] / budget if budget else 0.0
    perf_share = team_dec["perf"] / budget if budget else 0.0
    positioning = team_dec["positioning"]
    emphasis = team_dec["emphasis"][seg_index]
    promo = team_dec["promotion"]

    # Positioning fit: LEG likes low positioning (heritage), FUN/GENZ like high.
    seg_ideal = {"LEG": 10, "FIT": 45, "FUN": 85, "GENZ": 75, "MAIN": 50}[seg]
    positioning_fit = 32.0 * (1 - abs(positioning - seg_ideal) / 100.0)

    base = 45.0
    equity_term = 0.15 * eq
    brand_term = 10.0 * brand_share
    perf_term = 8.0 * perf_share
    targeting_term = 20.0 * emphasis
    promo_lift = LP * promo * DEAL_SENSITIVITY[seg]
    dc_penalty = VDC * entering_dc * DEAL_SENSITIVITY[seg]

    val = (base + equity_term + brand_term + perf_term + positioning_fit
           + targeting_term + promo_lift - dc_penalty)
    return max(1.0, val)


def _next_equity(prev_eq: float, brand: float, perf: float, promo_depth: float) -> float:
    credit = 0.6 * brand
    perfskew_debit = 0.25 * perf
    reversion = 0.05 * (prev_eq - 50.0)
    nxt = prev_eq + credit - perfskew_debit - B_PROMO * promo_depth - reversion
    return max(0.0, min(100.0, nxt))


def run_history(team_names: list[str], submissions_by_round: list[dict],
                config: dict) -> list[dict]:
    """Re-derive the full history from submissions for rounds 1..t. Pure & deterministic.

    ``submissions_by_round`` is 0-indexed: element i holds round (i+1)'s submissions as
    ``{team: {open-lever fields}}`` (empty during the listening period).
    """
    teams = list(team_names)
    n = len(submissions_by_round)
    crisis_round = int(config.get("crisis_round", 9))
    crisis_base = float(config.get("crisis_damage_base", 25.0))
    weights = config.get("scorecard_weights", DEFAULT_SCORECARD_WEIGHTS)

    eq = {t: 60.0 for t in teams}
    dc = {t: 0.0 for t in teams}
    trust = {t: 70.0 for t in teams}
    prod = {t: 70.0 for t in teams}

    history: list[dict] = []
    for i in range(n):
        rnd = i + 1
        subs = submissions_by_round[i] or {}
        decisions = {t: _merge_decision(subs.get(t)) for t in teams}

        # 1. grow segments
        sizes = {s: SEGMENT_BASE[s] * ((1 + SEGMENT_GROWTH[s]) ** (rnd - 1)) for s in SEGS}

        # 2. equity stock update (promo debit lands before resolution)
        for t in teams:
            eq[t] = _next_equity(eq[t], decisions[t]["brand"], decisions[t]["perf"],
                                 decisions[t]["promotion"])

        # crisis: at/after crisis round, high-caffeine (low positioning) teams take damage
        damage = {t: 0.0 for t in teams}
        if rnd >= crisis_round:
            for t in teams:
                exposure = max(0.0, (60.0 - decisions[t]["positioning"]) / 60.0)
                preparedness = min(1.0, trust[t] / 100.0)
                dmg = crisis_base * exposure * (1.0 - 0.5 * preparedness)
                damage[t] = round(dmg, 4)
                trust[t] = max(0.0, trust[t] - dmg)
                prod[t] = max(0.0, prod[t] - 0.4 * dmg)

        # 3. resolve market per segment
        firms: dict[str, dict] = {t: {"perception": {}, "share": {}, "volume": {}} for t in teams}
        for si, seg in enumerate(SEGS):
            perc = {}
            for t in teams:
                p = _perception(decisions[t], eq[t], seg, si, dc[t])
                p -= 0.5 * damage[t] * DEAL_SENSITIVITY.get(seg, 0.5)
                perc[t] = max(1.0, p)
                firms[t]["perception"][seg] = round(perc[t], 4)
            denom = sum(perc.values()) + sum(BACKDROP[b][seg] for b in BACKDROP)
            for t in teams:
                share = perc[t] / denom if denom else 0.0
                firms[t]["share"][seg] = round(share, 6)
                firms[t]["volume"][seg] = round(sizes[seg] * share, 4)

        total_size = sum(sizes.values())
        scorecard_firms = {}
        for t in teams:
            total_vol = sum(firms[t]["volume"].values())
            price = decisions[t]["price"]
            promo = decisions[t]["promotion"]
            budget = decisions[t]["brand"] + decisions[t]["perf"] + decisions[t]["research"]
            revenue = total_vol * price
            contribution = total_vol * (price - VARIABLE_COST - PROMO_COST_RATE * promo) - budget
            tms = total_vol / total_size if total_size else 0.0

            firms[t].update({
                "firm_type": "team",
                "total_market_share": round(tms, 6),
                "revenue": round(revenue, 4),
                "contribution": round(contribution, 4),
                "price": round(price, 4),
                "promotion": round(promo, 4),
                "eq": round(eq[t], 4),
                "attributes": {"EQ": round(eq[t], 4), "TRUST": round(trust[t], 4),
                               "PROD": round(prod[t], 4)},
            })

            # 4. scorecard: four normalized 0-100 dimensions (NOT calibrated)
            brand_health = 0.6 * eq[t] + 0.4 * trust[t]
            avg_perc = sum(firms[t]["perception"].values()) / len(SEGS)
            customer_value = min(100.0, avg_perc)
            market_position = min(100.0, tms * 100.0 * len(teams))
            financial = max(0.0, min(100.0, 50.0 + contribution))
            normalized = {
                "brand_health": round(brand_health, 3),
                "customer_value": round(customer_value, 3),
                "market_position": round(market_position, 3),
                "financial": round(financial, 3),
            }
            overall = round(
                sum(normalized[k] * float(weights.get(k, 0.25)) for k in normalized), 3
            )
            scorecard_firms[t] = {"overall": overall, "normalized": normalized}

        record = {
            "round": rnd,
            "firms": firms,
            "equity": {t: round(eq[t], 4) for t in teams},
            "attribute_state": {t: {"EQ": round(eq[t], 4), "TRUST": round(trust[t], 4),
                                    "PROD": round(prod[t], 4)} for t in teams},
            "deal_conditioning": {t: round(dc[t], 4) for t in teams},
            "promotion_depth": {t: round(decisions[t]["promotion"], 4) for t in teams},
            "damage": damage,
            "segment_sizes": {s: round(sizes[s], 4) for s in SEGS},
            "scorecard": {"firms": scorecard_firms, "weights": dict(weights)},
            "open_levers": open_levers_for_round(rnd, config),
            "effective_decisions": {t: decisions[t] for t in teams},
        }
        history.append(record)

        # 5. update deal-conditioning for next round
        for t in teams:
            dc[t] = dc[t] * (1 - RHO) + ETA * decisions[t]["promotion"]

    return history


# --- Customer agents (say-do gap) — scaled to [0,1], centered at 0.5 ---------------
K_PERCEP = 0.02      # perception points -> warmth slope
SAYDO_GAP = {"LEG": +0.20, "FIT": 0.00, "FUN": -0.20, "GENZ": -0.15, "MAIN": +0.05}
GAP_GAIN = 1.0
REL_ALPHA = 0.30
REL_START = 0.5


def _clip01(x: float) -> float:
    return max(0.0, min(1.0, x))


def revealed_warmth(perception_by_firm: dict, team: str, seg: str) -> float:
    """Grounded 'do': segment perception relative to the field mean, squashed to [0,1]."""
    vs = [perception_by_firm[f][seg] for f in perception_by_firm]
    vbar = sum(vs) / len(vs) if vs else 0.0
    return _clip01(0.5 + K_PERCEP * (perception_by_firm.get(team, {}).get(seg, vbar) - vbar))


def stated_warmth(revealed: float, seg: str, idiosyncrasy: float = 0.0) -> float:
    """Grounded 'say' = revealed + per-segment gap + persona idiosyncrasy, in [0,1]."""
    return _clip01(revealed + GAP_GAIN * SAYDO_GAP[seg] + idiosyncrasy)


def update_relationship(prev: float, revealed: float) -> float:
    return prev + REL_ALPHA * (revealed - prev)


def segment_stated_warmth(perception_by_firm: dict, team: str) -> dict:
    """Per-segment stated ('say') signal for the advisor context — segment-level, no
    persona idiosyncrasy (matches how anita reads the skewed room)."""
    return {s: stated_warmth(revealed_warmth(perception_by_firm, team, s), s, 0.0)
            for s in SEGS}


def warmth_band(stated: float) -> str:
    """[0,1] stated warmth -> display band (never expose the raw number to students)."""
    if stated >= 0.6:
        return "warm"
    if stated <= 0.4:
        return "cool"
    return "mixed"


def focus_group(perception_by_firm: dict, team: str, chosen_personas: list[dict],
                relationships: dict, prompt: Optional[str] = None,
                model_client=None) -> list[dict]:
    """Interview chosen personas. Returns grounded records (segment/revealed HIDDEN).

    `stated_warmth` is a raw [0,1] float here (matching the engine); the app layer
    converts it to a display band. `revealed_warmth` and segment never leave this function.
    """
    out = []
    for p in chosen_personas:
        seg = p["segment"]
        rel = float(relationships.get(p["id"], REL_START))
        rev = revealed_warmth(perception_by_firm, team, seg)
        stt = stated_warmth(rev, seg, float(p.get("idiosyncrasy", 0.0)))
        resp = {"persona_id": p["id"], "persona_name": p["name"],
                "stated_warmth": stt, "relationship": rel}
        resp["text"] = _persona_text(p, stt, rel, prompt, model_client)
        out.append(resp)
    return out


def _persona_text(persona: dict, stated: float, rel: float, prompt: Optional[str],
                  model_client) -> str:
    band = warmth_band(stated)
    if model_client is not None:
        system = (f"You are {persona['name']}, a customer. Stay in character. "
                  f"You feel {band} about the brand right now. "
                  f"Do NOT mention market segments or any numbers.")
        user = (prompt or "React to the brand and whether you'd buy it.")
        try:
            return model_client(system, user, 0.7)
        except Exception:
            pass
    lean = ("warming to" if rel >= 0.55 else "cooling on" if rel <= 0.45 else "neutral on")
    return f"{persona['name']}: [{band}] I'm {lean} this brand right now."


# --- Advisors ---------------------------------------------------------------------
def advise(decision_under_review: dict, context: dict, model_client=None) -> list[dict]:
    """Six public stances. The AI renders; it never moves a number."""
    proposed = decision_under_review.get("levers", {})
    positioning = proposed.get("positioning", 30)
    promo = proposed.get("promotion", 0.0)
    stances = []
    for adv in ADVISORS:
        valence, intensity = _advisor_valence(adv["id"], positioning, promo, context)
        text = _advisor_text(adv, valence, decision_under_review, model_client)
        stances.append({
            "advisor_id": adv["id"],
            "advisor_name": adv["name"],
            "role": adv["role"],
            "valence": valence,          # support | oppose | mixed
            "intensity": intensity,      # 0..1
            "text": text,
        })
    return stances


def _advisor_valence(advisor_id: str, positioning: float, promo: float, context: dict):
    high_pos = positioning >= 55
    if advisor_id == "elena":       # brand: defends the base
        return ("oppose" if high_pos else "support", 0.8 if high_pos else 0.6)
    if advisor_id == "ray":         # performance: likes growth
        return ("support" if high_pos else "mixed", 0.7)
    if advisor_id == "jax":         # creative: rewards boldness (distance from center)
        return ("support" if abs(positioning - 50) >= 25 else "mixed", 0.75)
    if advisor_id == "anita":       # insights: reads STATED warmth (the skewed room)
        stated = context.get("stated_warmth", {})
        avg = (sum(stated.values()) / len(stated)) if stated else 0.5
        return ("support" if avg >= 0.5 else "oppose", round(min(1.0, abs(avg - 0.5) * 2), 2))
    if advisor_id == "bud":         # sales: promotion & volume
        return ("support" if promo > 0.3 else "mixed", 0.6 + min(0.3, promo))
    if advisor_id == "tom":         # risk: exposure from aggressive promo
        return ("oppose" if promo > 0.4 else "mixed", 0.5 + min(0.4, promo))
    return ("mixed", 0.5)


def _advisor_text(advisor: dict, valence: str, decision: dict, model_client) -> str:
    system = f"You are {advisor['name']}, {advisor['role']}. Bias: {advisor['bias']}."
    stance_word = {"support": "argue FOR", "oppose": "argue AGAINST", "mixed": "weigh both sides of"}[valence]
    user = f"On the decision under review, {stance_word} it. Be brief and in character."
    if model_client is not None:
        try:
            return model_client(system, user, 0.7)
        except Exception:
            pass
    line = {
        "support": "I'd push for this — it plays to where we need to go.",
        "oppose": "I'd hold off — this exposes us more than it looks.",
        "mixed": "There's a case both ways; know what you're trading.",
    }[valence]
    return f"{advisor['name']}: {line}"


def build_usage_record(team: str, rnd: int, stances: list[dict],
                       decision_under_review: dict) -> dict:
    return {
        "team": team,
        "round": rnd,
        "advisors_opened": [s["advisor_id"] for s in stances],
        "count": len(stances),
        "decision_ref": decision_under_review.get("levers", {}),
    }


# --- Scorecard report (shape matches the verified report.build_report) -------------
DIMS = ("brand_health", "customer_value", "market_position", "financial")


def build_report(history: list[dict]) -> list[dict]:
    """List, one entry per round: {round, teams: {team: {own, cohort_overall,
    cohort_dimension_average, rank, n_teams}}}. Cohort blocks are anonymized."""
    report = []
    for record in history:
        firms = record["scorecard"]["firms"]
        teams = list(firms)
        overall = {t: firms[t]["overall"] for t in teams}
        dim = {d: {t: firms[t]["normalized"][d] for t in teams} for d in DIMS}
        ovals = list(overall.values())
        cohort_overall = {"best": max(ovals), "average": sum(ovals) / len(ovals),
                          "worst": min(ovals)} if ovals else {"best": 0, "average": 0, "worst": 0}
        dim_average = {d: sum(dim[d].values()) / len(teams) for d in DIMS} if teams else {}
        team_reports = {}
        for t in teams:
            rank = 1 + sum(1 for u in teams if overall[u] > overall[t])
            team_reports[t] = {
                "own": {"overall": overall[t], "normalized": {d: dim[d][t] for d in DIMS}},
                "cohort_overall": cohort_overall,
                "cohort_dimension_average": dim_average,
                "rank": rank, "n_teams": len(teams),
            }
        report.append({"round": record["round"], "teams": team_reports})
    return report


# --- Board defense (shapes match the verified defense / defense_grader) ------------
DEFENSE_WEIGHTS = {"market_read": 0.35, "outcome": 0.35, "engagement": 0.15, "coherence": 0.15}


def assemble_defense_packet(history, decisions_by_round, team, focus_groups,
                            advisor_consults) -> dict:
    """Facts the written-defense grader reads: focus_group_evidence, decisions, final_scorecard."""
    last = history[-1] if history else {}
    fg = [f for f in focus_groups if f.get("team") == team]
    decisions = [row.get(team, {}) for row in decisions_by_round]
    return {
        "team": team,
        "focus_group_evidence": fg,
        "decisions": decisions,
        "final_scorecard": last.get("scorecard", {}).get("firms", {}).get(team, {}),
    }


def _norm(d: dict) -> dict:
    lo, hi = min(d.values()), max(d.values())
    if hi - lo < 1e-12:
        return {k: 50.0 for k in d}
    return {k: 100 * (v - lo) / (hi - lo) for k, v in d.items()}


def _stats(d: dict) -> dict:
    v = list(d.values())
    return {"best": max(v), "average": sum(v) / len(v), "worst": min(v)}


def build_defense(history, decisions_by_round, engagement, live_from: int = 1) -> dict:
    """Grades on tracking the REVEALED truth vs the skewed stated room. Shape per team:
    {dimensions:{market_read, outcome, engagement, coherence}, defense_score, raw, cohort, rank}."""
    if not history:
        return {}
    teams = [t for t in history[0]["firms"]
             if history[0]["firms"][t].get("firm_type", "team") == "team"]
    raw = {}
    for t in teams:
        mr = []
        for r, rec in enumerate(history, start=1):
            if r < live_from:
                continue
            perc = {f: rec["firms"][f]["perception"] for f in rec["firms"]}
            emph = (decisions_by_round[r - 1].get(t, {}) or {}).get("emphasis", [0.2] * 5)
            gap = 0.0
            for i, s in enumerate(SEGS):
                rev = revealed_warmth(perc, t, s)
                stt = stated_warmth(rev, s, 0.0)
                gap += emph[i] * (rev - stt)
            mr.append(gap)
        market_read = sum(mr) / len(mr) if mr else 0.0
        outcome = history[-1]["scorecard"]["firms"][t]["overall"]
        eng = float(engagement.get(t, 0))
        drift = 0.0
        n = 0
        prev = None
        for r in range(live_from - 1, len(history)):
            pos = (decisions_by_round[r].get(t, {}) or {}).get("positioning", 35.0)
            if prev is not None:
                drift += abs(pos - prev)
                n += 1
            prev = pos
        coherence = -(drift / n) if n > 0 else 0.0
        raw[t] = {"market_read": market_read, "outcome": outcome,
                  "engagement": eng, "coherence": coherence}
    norm = {dim: _norm({t: raw[t][dim] for t in teams}) for dim in DEFENSE_WEIGHTS}
    out = {}
    for t in teams:
        dims = {dim: norm[dim][t] for dim in DEFENSE_WEIGHTS}
        defense_score = sum(DEFENSE_WEIGHTS[dim] * dims[dim] for dim in DEFENSE_WEIGHTS)
        out[t] = {"dimensions": dims, "defense_score": defense_score, "raw": raw[t]}
    cohort = {"defense_score": _stats({t: out[t]["defense_score"] for t in teams}),
              **{dim: _stats({t: out[t]["dimensions"][dim] for t in teams})
                 for dim in DEFENSE_WEIGHTS}}
    for t in teams:
        out[t]["cohort"] = cohort
        out[t]["rank"] = 1 + sum(1 for u in teams if out[u]["defense_score"] > out[t]["defense_score"])
    return out


def grade_written_defense(defense_text: str, packet: dict, model_client=None,
                          cache: Optional[dict] = None) -> dict:
    """Prose grade (three dimensions + overall + justification). Cache by input hash."""
    key = str(hash((defense_text, str(packet.get("team")))))
    if cache is not None and key in cache:
        return cache[key]

    if model_client is not None:
        system = ("Return STRICT JSON with argument_coherence, honest_evidence_citation, "
                  "ownership_of_tradeoffs (0-100) and justification.")
        user = f"STUDENT DEFENSE: {defense_text}\nCONTEXT: {packet}"
        try:
            import json
            raw = model_client(system, user, 0.0)
            data = json.loads(raw)
            result = {
                "prose_dimensions": {
                    "argument_coherence": data.get("argument_coherence", 60),
                    "honest_evidence_citation": data.get("honest_evidence_citation", 60),
                    "ownership_of_tradeoffs": data.get("ownership_of_tradeoffs", 60),
                },
                "prose_overall": round(
                    (data.get("argument_coherence", 60)
                     + data.get("honest_evidence_citation", 60)
                     + data.get("ownership_of_tradeoffs", 60)) / 3, 2),
                "justification": data.get("justification", ""),
            }
            if cache is not None:
                cache[key] = result
            return result
        except Exception:
            pass

    # deterministic fallback grade based on length/evidence heuristics
    words = len((defense_text or "").split())
    base = max(30, min(85, 40 + words // 5))
    result = {
        "prose_dimensions": {
            "argument_coherence": base,
            "honest_evidence_citation": max(30, base - 5),
            "ownership_of_tradeoffs": max(30, base - 8),
        },
        "prose_overall": round((base + (base - 5) + (base - 8)) / 3, 2),
        "justification": ("Stub grade: no live grader configured. Score scales with the "
                          "length and specificity of the written defense."),
    }
    if cache is not None:
        cache[key] = result
    return result


def combine_report(deterministic_team: dict, prose: Optional[dict]) -> dict:
    """Two blocks side by side; never blended into one number."""
    return {
        "deterministic": deterministic_team,
        "prose": prose,
        "note": ("deterministic decision grade and prose grade are reported separately; "
                 "faculty weight them"),
    }
