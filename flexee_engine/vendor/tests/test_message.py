"""Golden test — Group B message/creative. Binds to the real engine, proves NEUTRAL neutrality,
pins the fit goldens, checks the say-do trap and a share trade-off. Run: python -m pytest -q test_message.py"""
from math import exp
import engine
from engine import DEFAULT_PARAMS as P
import message as MSG

FC = engine.FirmConfig(tuple(P.team_baseline_attributes), 30.0, (.45, .15, .05, .05, .30))
TOL = 1e-9


def test_reproduction_binds_to_engine():
    md = 0.0
    for price in (1.10, 1.30, 1.50, 1.75, 1.90):
        for promo, dc in ((0.0, 0.0), (0.5, 0.0), (0.0, 0.3), (0.8, 0.4)):
            for i, seg in enumerate(P.segments):
                md = max(md, abs(MSG._perception_components(FC, seg, i, P, price, promo, dc)
                                 - engine._perception(FC, seg, i, P, price, promo, dc)))
    assert md < 1e-12


def test_neutral_message_is_neutral():
    nb = MSG.message_fit("NEUTRAL")
    assert all(nb[s] == 0.0 for s in MSG.SEGMENTS)
    for i, seg in enumerate(P.segments):
        b = MSG._perception_components(FC, seg, i, P, 1.50, msg_bonus=None)
        e = MSG._perception_components(FC, seg, i, P, 1.50, msg_bonus=nb)
        assert abs(b - e) < 1e-12


def test_message_fit_goldens():
    want = {
        "PERFORMANCE":  {"LEG": 1.2, "FIT": 10.8, "FUN": 4.8, "GENZ": 2.4, "MAIN": 0.0},
        "HERITAGE":     {"LEG": 10.8, "FIT": -1.2, "FUN": -6.0, "GENZ": -7.2, "MAIN": 3.6},
        "CLEAN_ENERGY": {"LEG": -2.4, "FIT": 4.8, "FUN": 10.8, "GENZ": 3.6, "MAIN": 1.2},
        "COMMUNITY":    {"LEG": -1.2, "FIT": 2.4, "FUN": 3.6, "GENZ": 10.8, "MAIN": 1.2},
    }
    for m, w in want.items():
        got = MSG.message_fit(m)
        for s in MSG.SEGMENTS:
            assert abs(got[s] - w[s]) < TOL


def test_saydo_trap_and_share_tradeoff():
    h = MSG.message_fit("HERITAGE")
    assert h["LEG"] > 0 and h["FUN"] < 0 and h["GENZ"] < 0
    assert max(MSG.MESSAGE_FIT["PERFORMANCE"], key=MSG.MESSAGE_FIT["PERFORMANCE"].get) == "FIT"
    assert max(MSG.MESSAGE_FIT["CLEAN_ENERGY"], key=MSG.MESSAGE_FIT["CLEAN_ENERGY"].get) == "FUN"
    assert max(MSG.MESSAGE_FIT["COMMUNITY"], key=MSG.MESSAGE_FIT["COMMUNITY"].get) == "GENZ"

    def share(msg_bonus, seg_name):
        i = MSG.SEGMENTS.index(seg_name); seg = P.segments[i]
        firms = {"A": FC, **P.backdrop_firms}
        att = {fn: exp(seg.beta * MSG._perception_components(
            cfg, seg, i, P, P.price, msg_bonus=(msg_bonus if fn == "A" else None)) / 100.0)
            for fn, cfg in firms.items()}
        return att["A"] / sum(att.values())
    nb = MSG.message_fit("NEUTRAL")
    assert share(h, "LEG") > share(nb, "LEG")
    assert share(h, "GENZ") < share(nb, "GENZ")
