"""
Flexee Marketing Management (Torque) — Group B, Decision 6: CHANNEL MIX.
REFERENCE + golden source. Self-contained; the matrices and the exact share-math
insertion are the specification. Priyanshuu ports this into flexee_engine/vendor/channel.py
and wires the one-line insertion into engine._perception (documented below).

WHAT IT MODELS
  The team splits go-to-market across three channels — RETAIL, DTC, SOCIAL — each with
  different per-segment affinity. Channel mix changes how much of your TARGETING EFFORT
  actually reaches each segment. It is a marketing-reach lever, not distribution logistics:
  it multiplies the targeting term of perception and nothing else.

THE EXACT INSERTION (engine.py, _perception, the targeting line)
  BEFORE:  targeting_fit = params.targeting_bonus_coef * firm_config.emphasis[segment_index]
  AFTER :  targeting_fit = params.targeting_bonus_coef * firm_config.emphasis[segment_index] * reach[segment.name]
  where reach = channel_reach(team_channel_weights).  Everything else in _perception is untouched.

GOLDEN-LOCK GUARANTEE (why this can't break the engine)
  The affinity matrix is normalised so that each segment column AVERAGES 1.0 across the three
  channels. Therefore the neutral/uniform mix (1/3, 1/3, 1/3) gives reach[s] == 1.0 for every
  segment, so targeting_fit is unchanged and the enriched engine reproduces the base engine to
  machine precision. Channel mix REDISTRIBUTES reach across segments; a balanced mix is a no-op.
  All magnitudes are calibrate-later.
"""
from math import exp, isclose
import engine
from engine import DEFAULT_PARAMS

CHANNELS = ("RETAIL", "DTC", "SOCIAL")
SEGMENTS = ("LEG", "FIT", "FUN", "GENZ", "MAIN")

# Affinity matrix A[channel][segment]. Each segment column averages EXACTLY 1.0 across the
# three channels (sum 3.0), so a uniform mix is neutral. Skew toward a channel raises reach
# for that channel's affine segments and lowers the others. Calibrate-later.
CHANNEL_AFFINITY = {
    "RETAIL": {"LEG": 1.60, "FIT": 1.00, "FUN": 0.85, "GENZ": 0.55, "MAIN": 1.45},
    "DTC":    {"LEG": 0.85, "FIT": 1.30, "FUN": 1.35, "GENZ": 0.95, "MAIN": 0.85},
    "SOCIAL": {"LEG": 0.55, "FIT": 0.70, "FUN": 0.80, "GENZ": 1.50, "MAIN": 0.70},
}
NEUTRAL_MIX = {"RETAIL": 1.0/3.0, "DTC": 1.0/3.0, "SOCIAL": 1.0/3.0}


def _validate_mix(weights):
    if set(weights) != set(CHANNELS):
        raise ValueError(f"channel weights must be exactly {CHANNELS}")
    w = {c: float(weights[c]) for c in CHANNELS}
    if any(v < 0.0 for v in w.values()):
        raise ValueError("channel weights cannot be negative")
    if abs(sum(w.values()) - 1.0) > 1e-9:
        raise ValueError("channel weights must sum to 1.0")
    return w


def channel_reach(weights):
    """weights: {channel: fraction} summing to 1 -> {segment: reach multiplier}."""
    w = _validate_mix(weights)
    return {s: sum(w[c] * CHANNEL_AFFINITY[c][s] for c in CHANNELS) for s in SEGMENTS}


# ---- reproduction of engine._perception (bound to the real engine below) + the insertion ----
def _perception_components(firm_config, segment, seg_index, params, price,
                           promo_depth=0.0, entering_dc=0.0, reach=None):
    """Reproduces engine._perception EXACTLY; when reach is given, multiplies ONLY targeting_fit."""
    attribute_fit = sum(wt * a for wt, a in zip(segment.weights, firm_config.attributes))
    positioning_fit = params.positioning_bonus_max * (
        1.0 - 2.0 * abs(firm_config.positioning - segment.positioning_ideal) / 100.0)
    targeting_fit = params.targeting_bonus_coef * firm_config.emphasis[seg_index]
    if reach is not None:
        targeting_fit *= reach[segment.name]                     # <-- THE INSERTION
    # value_fit reuses the engine's own helpers so there is no arithmetic to drift
    value_fit = params.value_fit_vp * engine._incremental_value_fit(segment, price, params)
    base = attribute_fit + positioning_fit + targeting_fit + value_fit
    if promo_depth == 0.0 and entering_dc == 0.0:
        return base
    lift = params.promotion_lift * promo_depth * segment.deal_sensitivity
    pen = params.deal_conditioning_penalty * entering_dc * segment.deal_sensitivity
    return base + lift - pen


if __name__ == "__main__":
    P = DEFAULT_PARAMS
    fc = engine.FirmConfig(tuple(P.team_baseline_attributes), 30.0, (.45, .15, .05, .05, .30))

    # (1) BIND to the real engine: reproduction == engine._perception over a grid -> zero drift
    maxdiff = 0.0
    for price in (1.10, 1.30, 1.50, 1.75, 1.90):
        for promo, dc in ((0.0, 0.0), (0.5, 0.0), (0.0, 0.3), (0.8, 0.4)):
            for i, seg in enumerate(P.segments):
                mine = _perception_components(fc, seg, i, P, price, promo, dc, reach=None)
                real = engine._perception(fc, seg, i, P, price, promo, dc)
                maxdiff = max(maxdiff, abs(mine - real))
    assert maxdiff < 1e-12, f"reproduction drifted from engine: {maxdiff}"
    print(f"BIND to engine._perception: max diff over grid = {maxdiff:.2e}  (zero drift)")

    # (2) NEUTRALITY: uniform mix -> reach all 1.0 -> enriched perception == base, machine precision
    reach_u = channel_reach(NEUTRAL_MIX)
    print("\nreach (uniform mix):", {s: round(reach_u[s], 12) for s in SEGMENTS})
    nd = 0.0
    for i, seg in enumerate(P.segments):
        b = _perception_components(fc, seg, i, P, 1.50, reach=None)
        e = _perception_components(fc, seg, i, P, 1.50, reach=reach_u)
        nd = max(nd, abs(b - e))
    assert nd < 1e-12, f"uniform mix not neutral: {nd}"
    print(f"NEUTRALITY: uniform-mix perception vs base, max diff = {nd:.2e}")

    # (3) GOLDEN reach vectors for three representative mixes
    RETAIL_HEAVY = {"RETAIL": 0.70, "DTC": 0.20, "SOCIAL": 0.10}
    SOCIAL_HEAVY = {"RETAIL": 0.10, "DTC": 0.20, "SOCIAL": 0.70}
    DTC_HEAVY    = {"RETAIL": 0.15, "DTC": 0.70, "SOCIAL": 0.15}
    print("\n=== GOLDEN: reach vectors ===")
    for name, mix in (("retail_heavy", RETAIL_HEAVY), ("social_heavy", SOCIAL_HEAVY), ("dtc_heavy", DTC_HEAVY)):
        r = channel_reach(mix)
        print(f"  {name:12s}", {s: round(r[s], 6) for s in SEGMENTS})

    # (4) DIRECTION: retail-heavy lifts LEG reach above 1 and drops GENZ below 1; social-heavy the reverse
    r_ret = channel_reach(RETAIL_HEAVY); r_soc = channel_reach(SOCIAL_HEAVY)
    assert r_ret["LEG"] > 1.0 > r_ret["GENZ"], "retail mix should favour LEG over GENZ"
    assert r_soc["GENZ"] > 1.0 > r_soc["LEG"], "social mix should favour GENZ over LEG"
    assert r_ret["LEG"] > r_soc["LEG"] and r_soc["GENZ"] > r_ret["GENZ"]

    # (5) SHARE-LEVEL golden: a segment's share moves the intended way when reach shifts.
    #     Softmax over firms in one segment; only the team's perception carries channel reach.
    def seg_share(reach, seg_name="GENZ"):
        i = SEGMENTS.index(seg_name); seg = P.segments[i]
        firms = {"A": fc, **P.backdrop_firms}
        att = {}
        for fn, cfg in firms.items():
            rr = reach if fn == "A" else None
            att[fn] = exp(seg.beta * _perception_components(cfg, seg, i, P, P.price, reach=rr) / 100.0)
        Z = sum(att.values())
        return att["A"] / Z
    s_base = seg_share(channel_reach(NEUTRAL_MIX)); s_soc = seg_share(channel_reach(SOCIAL_HEAVY))
    print(f"\nGENZ share for team A:  uniform={s_base:.6f}  social_heavy={s_soc:.6f}  (+{s_soc-s_base:.6f})")
    assert s_soc > s_base, "social-heavy mix must raise the team's GenZ share"

    print("\nALL CHANNEL-MIX CHECKS PASSED: engine-bound reproduction, uniform-mix neutrality, "
          "reach goldens, direction, and share shift.  (magnitudes calibrate-later)")
