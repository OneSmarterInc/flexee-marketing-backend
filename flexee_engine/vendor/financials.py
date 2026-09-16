"""
Torque (Flexee Marketing Management) — three-statement financial reporting layer.

Ported VERBATIM from the verified reference (torque_financials_reference.py); the
articulation golden (test_financials.py) locks this math. Financials are a REPORTING
layer over the marketing engine's existing outputs (price, volume, the budget split,
promotion) — they add no new decision. The centerpiece is the sim's thesis in the
accounts: brand-building spend is CAPITALIZED as an intangible 'Brand Equity' asset and
amortized; performance marketing and research are EXPENSED as incurred. 'Build vs rent'
therefore surfaces in the accounts themselves.

Deliberately departs from strict GAAP (which expenses advertising); the manual flags it.

The three statements ARTICULATE by construction: ending cash on the cash-flow statement
equals the balance-sheet cash line, retained earnings rolls by net income less dividends,
and Assets == Liabilities + Equity every round.

This module is pure and imports nothing from the engine, so it cannot move any market
number. Config constants below are calibrate-later placeholders; the app layer overrides
them from flexee_calibrated_config.py.
"""

from decimal import Decimal, getcontext

getcontext().prec = 28
D = Decimal


# ----------------------------- configuration --------------------------------
# All money in $ (thousands, say). Constants are calibrate-later placeholders.
CFG = {
    "cogs_rate":        D("0.35"),   # COGS as % of GROSS revenue
    "fixed_overhead":   D("30"),     # SG&A / fixed operating cost per round
    "brand_amort_rate": D("0.25"),   # amortization of opening net brand asset (mirrors engine equity decay LAM=0.25)
    "tax_rate":         D("0.25"),
    "interest_rate":    D("0.06"),   # per round, on constant inherited debt
    # passive working-capital assumptions (NOT student decisions)
    "ar_frac":          D("0.15"),   # accounts receivable = ar_frac * gross revenue
    "inv_frac":         D("0.20"),   # inventory           = inv_frac * COGS
    "ap_frac":          D("0.25"),   # accounts payable    = ap_frac  * COGS
    "dividend":         D("0"),      # no dividend by default (retain all)
}

# Opening (inherited) balance sheet at t0. Torque is an established brand, so it
# opens with a meaningful Brand Equity asset and operating assets.
OPENING = {
    "cash":     D("120"),
    "ppe":      D("150"),   # inherited operating assets, held constant (no capex/depr in scope)
    "brand":    D("200"),   # opening capitalized brand asset (net)
    "debt":     D("100"),   # inherited long-term debt, constant
    "paid_in":  D("150"),   # paid-in capital, constant
    # AR, Inventory, AP at t0 are derived from round-1 activity for simplicity of
    # the opening peg; retained earnings is the balancing plug at t0.
}


def round_financials(prev, mkt, cfg=CFG):
    """
    Compute the three statements for one round.
      prev : dict, the prior period's ENDING balance sheet (or the opening peg)
      mkt  : dict of this round's marketing engine outputs:
             gross_revenue, promo_allow (contra-revenue $), brand_spend,
             perf_spend, research_spend
    Returns (income_stmt, cash_flow, balance_sheet, state) as dicts.
    """
    c = cfg
    gross_rev   = D(mkt["gross_revenue"])
    promo_allow = D(mkt["promo_allow"])
    brand_spend = D(mkt["brand_spend"])
    perf_spend  = D(mkt["perf_spend"])
    rsch_spend  = D(mkt["research_spend"])

    # ---- Income statement -------------------------------------------------
    net_rev   = gross_rev - promo_allow
    cogs      = c["cogs_rate"] * gross_rev
    gross_pf  = net_rev - cogs

    brand_amort = c["brand_amort_rate"] * prev["brand"]         # amortize OPENING net brand asset
    opex        = perf_spend + rsch_spend + brand_amort + c["fixed_overhead"]
    ebit        = gross_pf - opex

    interest = c["interest_rate"] * prev["debt"]
    pretax   = ebit - interest
    tax      = c["tax_rate"] * pretax if pretax > 0 else D("0")
    net_inc  = pretax - tax

    income = {
        "gross_revenue": gross_rev, "promo_allowances": promo_allow, "net_revenue": net_rev,
        "cogs": cogs, "gross_profit": gross_pf,
        "performance_mktg": perf_spend, "research": rsch_spend,
        "brand_amortization": brand_amort, "sga": c["fixed_overhead"],
        "ebit": ebit, "interest": interest, "pretax_income": pretax,
        "tax": tax, "net_income": net_inc,
    }

    # ---- Balance-sheet working-capital + brand roll-forward ----------------
    ar_end    = c["ar_frac"]  * gross_rev
    inv_end   = c["inv_frac"] * cogs
    ap_end    = c["ap_frac"]  * cogs
    brand_end = prev["brand"] + brand_spend - brand_amort       # capitalize spend, amortize
    ppe_end   = prev["ppe"]                                      # constant
    debt_end  = prev["debt"]                                     # constant
    paidin_end= prev["paid_in"]                                  # constant
    re_end    = prev["retained_earnings"] + net_inc - c["dividend"]

    d_ar  = ar_end  - prev["ar"]
    d_inv = inv_end - prev["inv"]
    d_ap  = ap_end  - prev["ap"]

    # ---- Cash-flow statement ----------------------------------------------
    # Operating: net income + non-cash amort - WC build. (perf+research+interest+tax
    # are already in net income as cash items; brand amort is the only non-cash addback.)
    cfo = net_inc + brand_amort - d_ar - d_inv + d_ap
    # Investing: brand-building is the capitalized (investing) marketing cash outflow.
    cfi = -brand_spend
    # Financing: dividends only (debt constant).
    cff = -c["dividend"]
    net_change_cash = cfo + cfi + cff
    cash_end = prev["cash"] + net_change_cash

    cash_flow = {
        "net_income": net_inc, "add_brand_amortization": brand_amort,
        "chg_ar": -d_ar, "chg_inventory": -d_inv, "chg_ap": d_ap,
        "cfo": cfo,
        "brand_investment": -brand_spend, "cfi": cfi,
        "dividends": -c["dividend"], "cff": cff,
        "net_change_in_cash": net_change_cash,
        "beginning_cash": prev["cash"], "ending_cash": cash_end,
    }

    # ---- Balance sheet -----------------------------------------------------
    total_assets = cash_end + ar_end + inv_end + brand_end + ppe_end
    total_liab   = ap_end + debt_end
    total_equity = paidin_end + re_end

    balance = {
        "cash": cash_end, "ar": ar_end, "inv": inv_end, "brand": brand_end, "ppe": ppe_end,
        "total_assets": total_assets,
        "ap": ap_end, "debt": debt_end, "total_liabilities": total_liab,
        "paid_in": paidin_end, "retained_earnings": re_end, "total_equity": total_equity,
    }
    # state needed as 'prev' next round (includes ap for the WC delta)
    state = {
        "cash": cash_end, "ar": ar_end, "inv": inv_end, "brand": brand_end,
        "ppe": ppe_end, "debt": debt_end, "paid_in": paidin_end,
        "retained_earnings": re_end, "ap": ap_end,
    }
    return income, cash_flow, balance, state


def build_opening_state(first_round_mkt, cfg=CFG, opening=OPENING):
    """Peg the opening balance sheet so it BALANCES at t0, deriving WC from round-1 activity."""
    c = cfg
    gr   = D(first_round_mkt["gross_revenue"])
    cogs = c["cogs_rate"] * gr
    ar   = c["ar_frac"]  * gr
    inv  = c["inv_frac"] * cogs
    ap   = c["ap_frac"]  * cogs
    assets_ex_re = opening["cash"] + ar + inv + opening["brand"] + opening["ppe"]
    liab         = ap + opening["debt"]
    # retained earnings is the plug so Assets == Liab + Equity at t0
    re0 = assets_ex_re - liab - opening["paid_in"]
    return {
        "cash": opening["cash"], "ar": ar, "inv": inv, "brand": opening["brand"],
        "ppe": opening["ppe"], "debt": opening["debt"], "paid_in": opening["paid_in"],
        "retained_earnings": re0, "ap": ap,
    }
