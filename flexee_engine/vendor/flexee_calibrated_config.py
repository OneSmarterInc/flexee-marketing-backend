"""Flexee Marketing Management — calibrated default configuration (v1).

Every simulation coefficient is a faculty-tunable parameter. The verified engine modules
carry REFERENCE defaults that the golden tests are computed from; those never change.
This module builds the SHIPPING default configuration — the values a course runs with out of
the box, which faculty can still override — by applying the calibration decisions below to the
reference defaults. Because it is a config layer (not an edit to the locked modules), all
727 goldens remain intact.

Calibration decisions (Aug 2026, from the telemetry-harness pass on the five archetypes):
  1. Crisis magnitude — UNCHANGED (damage_base 25). The round-9 scandal is fundamentally a
     brand-equity and trust event, not a market-share event: doubling its magnitude barely moved
     total share (mean swing 0.048 -> 0.074) while it visibly hits the equity stock and TRUST.
     Left as an equity/scorecard event, which is the intended lesson.
  2. Positioning penalty SHARPENED — positioning_bonus_max 20 -> 32. The centrist "stuck in the
     middle" strategy finished a too-comfortable 2nd (74). Sharpening the off-ideal penalty pulls
     it down to a clearly-can't-win ~62 while the focused pivoter still dominates at ~94.
  3. Promotion penalty EASED — promo_cost_rate 0.30 -> 0.20, deal_conditioning_penalty 6.0 -> 4.0,
     b_promo 4.0 -> 2.5. The promo abuser was annihilated (overall ~2). Easing the three promo
     levers leaves it unmistakably worst (~7, negative contribution, eroded equity) without being
     instant death — the promo trap should look tempting and then hurt, not obviously suicidal.

Resulting archetype standings (telemetry): PIVOT ~93.8, BALANCED ~62.5, PERF ~51.9,
HERITAGE ~50.0, PROMO ~7.4; all four pedagogy diagnostics True.
"""

from dataclasses import replace

from engine import DEFAULT_PARAMS as _REFERENCE_PARAMS
from equity import DEFAULT_EQUITY_PARAMS as _REFERENCE_EQUITY_PARAMS
from promotion import DEFAULT_PROMOTION_PARAMS as _REFERENCE_PROMOTION_PARAMS

# Crisis magnitude is unchanged from the reference (damage_base 25.0); promotion dc_decay/dc_gain
# are unchanged. Only the three levers below move.

CALIBRATED_PARAMS = replace(
    _REFERENCE_PARAMS,
    positioning_bonus_max=32.0,        # was 20.0 — sharpen the stuck-in-the-middle penalty
    promo_cost_rate=0.20,              # was 0.30 — ease the promotion margin bite
    deal_conditioning_penalty=4.0,    # was 6.0  — ease the deal-conditioning drag
)

CALIBRATED_EQUITY_PARAMS = replace(
    _REFERENCE_EQUITY_PARAMS,
    b_promo=2.5,                      # was 4.0 — ease the promotion equity debit
)

CALIBRATED_PROMOTION_PARAMS = _REFERENCE_PROMOTION_PARAMS   # unchanged

# Crisis: use the reference magnitude (damage_base 25.0) when constructing CrisisParams at setup.
CALIBRATED_CRISIS_DAMAGE_BASE = 25.0
