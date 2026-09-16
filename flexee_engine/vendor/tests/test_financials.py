"""Golden for the financial reporting layer (financials.py).

Locks the three articulation identities on every round for two opposite scenarios
(brand-builder vs performance-renter), plus the opening-balance peg and the
build-vs-rent thesis. Ported from torque_financials_reference.py's assertions.
"""
from decimal import Decimal as D

from financials import CFG, round_financials, build_opening_state


def _make_rounds(brand, perf, rsch, rev_path, promo_path):
    return [
        {"gross_revenue": rev_path[i], "promo_allow": promo_path[i],
         "brand_spend": brand, "perf_spend": perf, "research_spend": rsch}
        for i in range(len(rev_path))
    ]


REV = [D("400"), D("420"), D("440"), D("460")]
PROMO = [D("20"), D("22"), D("24"), D("26")]
BUILDER = _make_rounds(D("60"), D("30"), D("10"), REV, PROMO)   # brand-heavy
RENTER = _make_rounds(D("10"), D("80"), D("10"), REV, PROMO)    # performance-heavy


def _run(rounds, cfg=CFG):
    prev = build_opening_state(rounds[0], cfg)
    results = []
    for mkt in rounds:
        income, cf, bs, state = round_financials(prev, mkt, cfg)
        results.append((income, cf, bs, prev))
        prev = state
    return results


def test_opening_balance_pegs_to_zero():
    prev = build_opening_state(BUILDER[0])
    assets = prev["cash"] + prev["ar"] + prev["inv"] + prev["brand"] + prev["ppe"]
    le = prev["ap"] + prev["debt"] + prev["paid_in"] + prev["retained_earnings"]
    assert assets - le == 0


def test_articulation_holds_every_round():
    for name, rounds in (("builder", BUILDER), ("renter", RENTER)):
        for t, (income, cf, bs, prev) in enumerate(_run(rounds), start=1):
            # 1. Assets == Liabilities + Equity
            assert bs["total_assets"] - (bs["total_liabilities"] + bs["total_equity"]) == 0, \
                f"{name} R{t}: balance sheet off"
            # 2. ending cash on cash-flow == balance-sheet cash
            assert bs["cash"] == cf["ending_cash"], f"{name} R{t}: cash tie broken"
            # 3. retained earnings rolls by net income less dividends
            assert bs["retained_earnings"] == prev["retained_earnings"] + income["net_income"] - CFG["dividend"], \
                f"{name} R{t}: RE roll broken"


def test_build_vs_rent_surfaces_in_the_accounts():
    b = _run(BUILDER)
    r = _run(RENTER)
    b_brand_first, b_brand_last = b[0][2]["brand"], b[-1][2]["brand"]
    r_brand_first, r_brand_last = r[0][2]["brand"], r[-1][2]["brand"]
    # brand-builder grows the intangible asset; renter lets it decay
    assert b_brand_last > b_brand_first
    assert r_brand_last < r_brand_first
    # and ends with healthier equity
    assert b[-1][2]["total_equity"] > r[-1][2]["total_equity"]
