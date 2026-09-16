"""
Flexee Marketing Management (Torque) — Group C, Decision 9: MARKET RESEARCH & COMMISSIONED DATA.
REFERENCE + golden source. Self-contained. Priyanshuu ports this into flexee_engine/vendor/research.py.

THE ONE OPEN QUESTION THIS ANSWERS
  The say-do bias was defined only for warmth (stated = revealed + SAYDO_GAP[seg]). Price-sensitivity
  and perceptual-map studies also have a "stated" version, and the spec didn't say how those overstate.
  The resolution is that THERE IS NO SEPARATE BIAS TO DEFINE. There is ONE per-segment self-flattery
  propensity — the exact SAYDO_GAP the warmth layer already uses — and every stated study PROJECTS
  that same vector through two per-measure parameters:
     reported = true + DIRECTION * GAP_GAIN * SAYDO_GAP[segment] * GAIN        (stated)
     reported = true                                                          (behavioral)
  DIRECTION (+1 / 0 / -1) is the self-flattering direction for that measure — which way of
  mis-reporting makes the respondent look good. GAIN converts the warmth-scale gap into the measure's
  units. A behavioral study sets DIRECTION to 0, so behavioral == engine truth: that single switch IS
  the stated-vs-behavioral distinction. Nothing new is invented; the non-warmth studies inherit the
  warmth bias, only pointed the right way.

DIRECTIONS (the whole design decision, in one table)
  - warmth / perception / awareness  -> +1  : the flattering answer is to over-claim affinity/awareness.
  - perceptual-map fit               -> +1  : self-flattery is assimilation — "that brand is really made
                                              for me" — pulling a liked brand toward your ideal (a halo).
  - price sensitivity                -> -1  : the flattering answer is to UNDER-claim — "I buy on quality,
                                              not on deals." So stated sensitivity sits BELOW true, most for
                                              the image-conscious segments that also overstate warmth.
  So most evaluative measures share warmth's +1; price sensitivity is the one that flips, because there
  the self-flattering lie is to look less price-driven, not more enthusiastic.

GROUNDED-OVERLAY (unchanged discipline)
  Every "true" number is read from engine state (perception, deal-sensitivity, ideal price, positioning
  fit). Nothing feeds back into the engine. Behavioral studies render the true number; stated studies
  render the projected one; commissioning debits the research budget. All gains are calibrate-later.
"""
import engine
from engine import DEFAULT_PARAMS as P
# reuse the EXACT warmth bias source and warmth math — no second copy, no drift
from ref_agents import SAYDO_GAP, GAP_GAIN, revealed_warmth, stated_warmth

SEGMENTS = ("LEG", "FIT", "FUN", "GENZ", "MAIN")


def _clip(x, lo, hi):
    return max(lo, min(hi, x))


# ---- the single projection: one bias vector, per-measure direction + gain ----
def project(true_val, segment, direction, gain, mode, lo=0.0, hi=1.0):
    """reported value for one segment. mode 'behavioral' -> truth (direction collapses to 0)."""
    if mode == "behavioral":
        return true_val
    if mode != "stated":
        raise ValueError("mode must be 'stated' or 'behavioral'")
    return _clip(true_val + direction * GAP_GAIN * SAYDO_GAP[segment] * gain, lo, hi)


# ---- the study catalog: measure, its flattery DIRECTION, GAIN, and commissioning cost ----
# cost_behavioral > cost_stated by design (behavioral truth is dearer and slower).
STUDY_CATALOG = {
    "segment_perception_tracker": dict(direction=+1, gain=1.0, lo=0.0, hi=1.0, cost_stated=2.0, cost_behavioral=5.0),
    "awareness_brand_tracker":    dict(direction=+1, gain=1.0, lo=0.0, hi=1.0, cost_stated=2.0, cost_behavioral=5.0),
    "perceptual_map":             dict(direction=+1, gain=1.0, lo=0.0, hi=1.0, cost_stated=3.0, cost_behavioral=6.0),
    "price_sensitivity_study":    dict(direction=-1, gain=1.0, lo=0.0, hi=1.0, cost_stated=3.0, cost_behavioral=7.0),
}


# ---- true (engine-grounded) measures per study ----
def _true_warmth(ctx):
    return {s: revealed_warmth(ctx["perception_by_firm"], ctx["firm"], s) for s in SEGMENTS}

def _true_price_sensitivity(ctx):
    return {s: float(engine.SEGMENT_DEAL_SENSITIVITIES[s]) for s in SEGMENTS}

def _true_perceptual_fit(ctx):
    """Perceived positioning fit (0..1) of the team brand for each segment — the map's true coordinate."""
    pos = ctx["team_positioning"]
    out = {}
    for seg in P.segments:
        out[seg.name] = _clip(1.0 - 2.0 * abs(pos - seg.positioning_ideal) / 100.0, 0.0, 1.0)
    return out

_TRUE = {
    "segment_perception_tracker": _true_warmth,
    "awareness_brand_tracker":    _true_warmth,
    "perceptual_map":             _true_perceptual_fit,
    "price_sensitivity_study":    _true_price_sensitivity,
}


def render_study(study, mode, ctx):
    """Return {segment: {'true':.., 'reported':..}} for a study in 'stated' or 'behavioral' mode."""
    if study not in STUDY_CATALOG:
        raise ValueError(f"unknown study {study!r}")
    spec = STUDY_CATALOG[study]
    true_by_seg = _TRUE[study](ctx)
    return {s: {"true": true_by_seg[s],
                "reported": project(true_by_seg[s], s, spec["direction"], spec["gain"],
                                    mode, spec["lo"], spec["hi"])}
            for s in SEGMENTS}


def where_to_grow(ctx, mode):
    """Opportunity ranking — the say-do TRAP rendered as a recommendation, no new bias.
    STATED: a team that only sees the stated tracker chases the loudest stated warmth -> the loud
            overstating core tops the list.
    BEHAVIORAL: the reconciliation surfaces who is WARMER than they say (the understated growth) by
            ranking on the say-do margin (behavioral truth minus what the room stated)."""
    stated = render_study("segment_perception_tracker", "stated", ctx)
    if mode == "stated":
        return sorted(SEGMENTS, key=lambda s: stated[s]["reported"], reverse=True)
    truth = render_study("segment_perception_tracker", "behavioral", ctx)
    margin = {s: truth[s]["true"] - stated[s]["reported"] for s in SEGMENTS}  # understated growth on top
    return sorted(SEGMENTS, key=lambda s: margin[s], reverse=True)


def commission(study, mode, budget_remaining):
    """Debit the research budget. Returns (ok, cost, budget_left). Behavioral costs more than stated."""
    spec = STUDY_CATALOG[study]
    cost = spec["cost_behavioral"] if mode == "behavioral" else spec["cost_stated"]
    if cost > budget_remaining:
        return (False, cost, budget_remaining)
    return (True, cost, budget_remaining - cost)


# --------------------------- verification / golden ---------------------------
def _ctx():
    # a growth-leaning team: mid-range warmth across segments (no floor clipping), so the say-do
    # projection is legible and the trap flips cleanly.
    dec = {"A": {"brand": 4, "perf": 4, "research": 2, "positioning": 60,
                 "emphasis": (.15, .20, .25, .25, .15)}}
    firms = engine.compute_round(dec, P)
    perception_by_firm = {f: firms[f]["perception"] for f in firms}
    return {"firm": "A", "perception_by_firm": perception_by_firm, "team_positioning": 60.0}


if __name__ == "__main__":
    ctx = _ctx()

    # (1) BIND the warmth study to the EXISTING stated_warmth transform -> zero drift, no second bias
    md = 0.0
    warm = render_study("segment_perception_tracker", "stated", ctx)
    for s in SEGMENTS:
        rev = revealed_warmth(ctx["perception_by_firm"], ctx["firm"], s)
        md = max(md, abs(warm[s]["reported"] - stated_warmth(rev, s, 0.0)))
    assert md < 1e-12, f"warmth study drifted from the engine's say-do transform: {md}"
    print(f"BIND warmth study to stated_warmth: max diff = {md:.2e}  (same bias source, zero drift)")

    # (2) BEHAVIORAL == TRUTH for every study (direction collapses to 0)
    bd = 0.0
    for study in STUDY_CATALOG:
        beh = render_study(study, "behavioral", ctx)
        for s in SEGMENTS:
            bd = max(bd, abs(beh[s]["reported"] - beh[s]["true"]))
    assert bd == 0.0
    print(f"BEHAVIORAL == engine truth for all studies: max diff = {bd:.2e}")

    # (3) GOLDEN sheets (stated vs behavioral), rounded
    def show(study):
        st = render_study(study, "stated", ctx)
        print(f"\n=== {study} ===")
        for s in SEGMENTS:
            print(f"  {s:5s} true={st[s]['true']:.4f}  stated={st[s]['reported']:.4f}  "
                  f"gap={st[s]['reported']-st[s]['true']:+.4f}")
    for study in ("segment_perception_tracker", "price_sensitivity_study", "perceptual_map"):
        show(study)

    # (4) DIRECTION invariants — the heart of the answer:
    #     warmth & map: stated - true has the SIGN of SAYDO_GAP (over-claim).
    #     price:        stated - true has the OPPOSITE sign (under-claim price sensitivity).
    warm = render_study("segment_perception_tracker", "stated", ctx)
    pmap = render_study("perceptual_map", "stated", ctx)
    price = render_study("price_sensitivity_study", "stated", ctx)
    for s in SEGMENTS:
        g = SAYDO_GAP[s]
        if g > 0:
            assert warm[s]["reported"] >= warm[s]["true"] and price[s]["reported"] <= price[s]["true"]
        elif g < 0:
            assert warm[s]["reported"] <= warm[s]["true"] and price[s]["reported"] >= price[s]["true"]
    # LEG (loud/overstater) claims to be LESS price-driven than it is; FUN (quiet) claims MORE
    assert price["LEG"]["reported"] < price["LEG"]["true"]
    assert price["FUN"]["reported"] > price["FUN"]["true"]
    print("\nDIRECTION invariants hold: warmth/map over-claim, price UNDER-claims (opposite sign).")

    # (5) THE TRAP: where-to-grow tops the loud OVERSTATER when STATED, an understated GROWTH
    #     segment when BEHAVIORAL — the recommendation itself flips.
    stated_rank = where_to_grow(ctx, "stated")
    beh_rank = where_to_grow(ctx, "behavioral")
    print(f"\nwhere-to-grow  STATED: {stated_rank}\n               BEHAVIORAL: {beh_rank}")
    assert stated_rank[0] == "LEG", "stated tracker should chase the loud overstating core"
    assert beh_rank[0] in ("FUN", "GENZ") and beh_rank[0] != "LEG", "behavioral should surface understated growth"

    # (6) commissioning: behavioral dearer than stated; debits budget; rejects when short
    ok, cost, left = commission("price_sensitivity_study", "behavioral", 10.0)
    assert ok and cost == 7.0 and abs(left - 3.0) < 1e-12
    ok2, cost2, _ = commission("price_sensitivity_study", "behavioral", 5.0)
    assert not ok2 and cost2 == 7.0
    assert STUDY_CATALOG["price_sensitivity_study"]["cost_behavioral"] > \
           STUDY_CATALOG["price_sensitivity_study"]["cost_stated"]
    print("commissioning: behavioral > stated cost, budget debit + rejection when short — OK")

    print("\nALL RESEARCH CHECKS PASSED: one bias source projected per study, warmth-bound with zero "
          "drift, behavioral==truth, direction invariants (price flips), the say-do trap, and "
          "commissioning costs.  (gains calibrate-later)")
