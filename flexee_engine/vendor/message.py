"""
Flexee Marketing Management (Torque) — Group B, Decision 7: MESSAGE / CREATIVE DIRECTION.
REFERENCE + golden source. Self-contained; the fit table and the exact share-math insertion
are the specification. Priyanshuu ports this into flexee_engine/vendor/message.py.

WHAT IT MODELS
  The team chooses WHAT the brand says — a single creative theme: PERFORMANCE, HERITAGE,
  CLEAN_ENERGY, or COMMUNITY (or NEUTRAL / none). Message fit varies by segment and carries
  the say-do twist: the heritage claim the loud Legacy core loves is exactly the claim the
  quiet growth segments (FUN, GENZ) tune out.

THE EXACT INSERTION (engine.py, _perception, one added additive term)
  base_perception = attribute_fit + positioning_fit + targeting_fit + value_fit + message_fit
  where message_fit = params.message_bonus_max * MESSAGE_FIT[message][segment.name].
  message_fit is ADDITIVE, parallel to positioning_fit. Nothing else changes.

GOLDEN-LOCK GUARANTEE
  The NEUTRAL message row is all zeros, so message_fit == 0 and the enriched engine reproduces
  the base engine exactly. A team that makes no creative choice is a no-op. message_bonus_max
  and the fit table are calibrate-later.
"""
from math import exp
import engine
from engine import DEFAULT_PARAMS

SEGMENTS = ("LEG", "FIT", "FUN", "GENZ", "MAIN")
MESSAGES = ("NEUTRAL", "PERFORMANCE", "HERITAGE", "CLEAN_ENERGY", "COMMUNITY")

MESSAGE_BONUS_MAX = 12.0   # perception points at fit == 1.0; calibrate-later

# Fit scores in roughly [-1, +1]. NEUTRAL is the zero row (back-compat anchor).
# HERITAGE is the say-do trap: strongly endorsed by the loud LEG, tuned out (negative) by the
# quiet growth segments FUN and GENZ — the same asymmetry the customer-agent layer models.
MESSAGE_FIT = {
    "NEUTRAL":      {"LEG": 0.0,  "FIT": 0.0,  "FUN": 0.0,  "GENZ": 0.0,  "MAIN": 0.0},
    "PERFORMANCE":  {"LEG": 0.10, "FIT": 0.90, "FUN": 0.40, "GENZ": 0.20, "MAIN": 0.00},
    "HERITAGE":     {"LEG": 0.90, "FIT": -0.10, "FUN": -0.50, "GENZ": -0.60, "MAIN": 0.30},
    "CLEAN_ENERGY": {"LEG": -0.20, "FIT": 0.40, "FUN": 0.90, "GENZ": 0.30, "MAIN": 0.10},
    "COMMUNITY":    {"LEG": -0.10, "FIT": 0.20, "FUN": 0.30, "GENZ": 0.90, "MAIN": 0.10},
}


def message_fit(message):
    if message not in MESSAGE_FIT:
        raise ValueError(f"unknown message {message!r}; one of {MESSAGES}")
    return {s: MESSAGE_BONUS_MAX * MESSAGE_FIT[message][s] for s in SEGMENTS}


def _perception_components(firm_config, segment, seg_index, params, price,
                           promo_depth=0.0, entering_dc=0.0, msg_bonus=None):
    """Reproduces engine._perception EXACTLY; when msg_bonus given, ADDS it (per segment)."""
    attribute_fit = sum(wt * a for wt, a in zip(segment.weights, firm_config.attributes))
    positioning_fit = params.positioning_bonus_max * (
        1.0 - 2.0 * abs(firm_config.positioning - segment.positioning_ideal) / 100.0)
    targeting_fit = params.targeting_bonus_coef * firm_config.emphasis[seg_index]
    value_fit = params.value_fit_vp * engine._incremental_value_fit(segment, price, params)
    base = attribute_fit + positioning_fit + targeting_fit + value_fit
    if msg_bonus is not None:
        base += msg_bonus[segment.name]                          # <-- THE INSERTION
    if promo_depth == 0.0 and entering_dc == 0.0:
        return base
    lift = params.promotion_lift * promo_depth * segment.deal_sensitivity
    pen = params.deal_conditioning_penalty * entering_dc * segment.deal_sensitivity
    return base + lift - pen


if __name__ == "__main__":
    P = DEFAULT_PARAMS
    fc = engine.FirmConfig(tuple(P.team_baseline_attributes), 30.0, (.45, .15, .05, .05, .30))

    # (1) BIND to the real engine (msg_bonus=None reproduces _perception exactly)
    maxdiff = 0.0
    for price in (1.10, 1.30, 1.50, 1.75, 1.90):
        for promo, dc in ((0.0, 0.0), (0.5, 0.0), (0.0, 0.3), (0.8, 0.4)):
            for i, seg in enumerate(P.segments):
                mine = _perception_components(fc, seg, i, P, price, promo, dc, msg_bonus=None)
                real = engine._perception(fc, seg, i, P, price, promo, dc)
                maxdiff = max(maxdiff, abs(mine - real))
    assert maxdiff < 1e-12, f"reproduction drifted: {maxdiff}"
    print(f"BIND to engine._perception: max diff over grid = {maxdiff:.2e}  (zero drift)")

    # (2) NEUTRALITY: NEUTRAL message adds 0 -> enriched == base
    nb = message_fit("NEUTRAL")
    nd = 0.0
    for i, seg in enumerate(P.segments):
        b = _perception_components(fc, seg, i, P, 1.50, msg_bonus=None)
        e = _perception_components(fc, seg, i, P, 1.50, msg_bonus=nb)
        nd = max(nd, abs(b - e))
    assert nd < 1e-12, f"NEUTRAL message not neutral: {nd}"
    print(f"NEUTRALITY: NEUTRAL-message perception vs base, max diff = {nd:.2e}")

    # (3) GOLDEN message-fit point tables (perception points added per segment)
    print("\n=== GOLDEN: message_fit (perception points) ===")
    for m in MESSAGES:
        mf = message_fit(m)
        print(f"  {m:12s}", {s: round(mf[s], 4) for s in SEGMENTS})

    # (4) THE SAY-DO TRAP (structural): HERITAGE is the loud-vs-quiet asymmetry.
    h = message_fit("HERITAGE")
    assert h["LEG"] > 0 and h["FUN"] < 0 and h["GENZ"] < 0, "heritage must please LEG, alienate growth"
    # PERFORMANCE peaks on FIT, CLEAN_ENERGY on FUN, COMMUNITY on GENZ
    assert max(MESSAGE_FIT["PERFORMANCE"], key=MESSAGE_FIT["PERFORMANCE"].get) == "FIT"
    assert max(MESSAGE_FIT["CLEAN_ENERGY"], key=MESSAGE_FIT["CLEAN_ENERGY"].get) == "FUN"
    assert max(MESSAGE_FIT["COMMUNITY"], key=MESSAGE_FIT["COMMUNITY"].get) == "GENZ"

    # (5) SHARE-LEVEL golden: HERITAGE lifts the team's LEG share and lowers its GENZ share.
    def seg_share(msg_bonus, seg_name):
        i = SEGMENTS.index(seg_name); seg = P.segments[i]
        firms = {"A": fc, **P.backdrop_firms}
        att = {}
        for fn, cfg in firms.items():
            mb = msg_bonus if fn == "A" else None
            att[fn] = exp(seg.beta * _perception_components(cfg, seg, i, P, P.price, msg_bonus=mb) / 100.0)
        return att["A"] / sum(att.values())
    base_leg = seg_share(nb, "LEG"); herit_leg = seg_share(h, "LEG")
    base_genz = seg_share(nb, "GENZ"); herit_genz = seg_share(h, "GENZ")
    print(f"\nLEG  share: neutral={base_leg:.6f}  heritage={herit_leg:.6f}  ({herit_leg-base_leg:+.6f})")
    print(f"GENZ share: neutral={base_genz:.6f}  heritage={herit_genz:.6f}  ({herit_genz-base_genz:+.6f})")
    assert herit_leg > base_leg and herit_genz < base_genz, "heritage must trade GenZ for Legacy"

    print("\nALL MESSAGE CHECKS PASSED: engine-bound reproduction, NEUTRAL neutrality, fit goldens, "
          "say-do trap, and share trade-off.  (magnitudes calibrate-later)")
