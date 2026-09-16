"""Golden test — Group B channel mix. Binds to the real engine, proves uniform-mix neutrality,
pins the reach goldens, and checks direction + a share shift. Run: python -m pytest -q test_channel.py"""
from math import exp
import engine
from engine import DEFAULT_PARAMS as P
import channel as CH

FC = engine.FirmConfig(tuple(P.team_baseline_attributes), 30.0, (.45, .15, .05, .05, .30))
TOL = 1e-9


def test_reproduction_binds_to_engine():
    md = 0.0
    for price in (1.10, 1.30, 1.50, 1.75, 1.90):
        for promo, dc in ((0.0, 0.0), (0.5, 0.0), (0.0, 0.3), (0.8, 0.4)):
            for i, seg in enumerate(P.segments):
                md = max(md, abs(CH._perception_components(FC, seg, i, P, price, promo, dc)
                                 - engine._perception(FC, seg, i, P, price, promo, dc)))
    assert md < 1e-12


def test_uniform_mix_is_neutral():
    reach = CH.channel_reach(CH.NEUTRAL_MIX)
    assert all(abs(reach[s] - 1.0) < 1e-12 for s in CH.SEGMENTS)
    for i, seg in enumerate(P.segments):
        b = CH._perception_components(FC, seg, i, P, 1.50, reach=None)
        e = CH._perception_components(FC, seg, i, P, 1.50, reach=reach)
        assert abs(b - e) < 1e-12


def test_reach_goldens():
    exp_reach = {
        "retail_heavy": ({"RETAIL": .70, "DTC": .20, "SOCIAL": .10},
                         {"LEG": 1.345, "FIT": 1.03, "FUN": 0.945, "GENZ": 0.725, "MAIN": 1.255}),
        "social_heavy": ({"RETAIL": .10, "DTC": .20, "SOCIAL": .70},
                         {"LEG": 0.715, "FIT": 0.85, "FUN": 0.915, "GENZ": 1.295, "MAIN": 0.805}),
        "dtc_heavy":    ({"RETAIL": .15, "DTC": .70, "SOCIAL": .15},
                         {"LEG": 0.9175, "FIT": 1.165, "FUN": 1.1925, "GENZ": 0.9725, "MAIN": 0.9175}),
    }
    for _, (mix, want) in exp_reach.items():
        got = CH.channel_reach(mix)
        for s in CH.SEGMENTS:
            assert abs(got[s] - want[s]) < TOL


def test_direction_and_share_shift():
    r_ret = CH.channel_reach({"RETAIL": .70, "DTC": .20, "SOCIAL": .10})
    r_soc = CH.channel_reach({"RETAIL": .10, "DTC": .20, "SOCIAL": .70})
    assert r_ret["LEG"] > 1.0 > r_ret["GENZ"]
    assert r_soc["GENZ"] > 1.0 > r_soc["LEG"]

    def genz_share(reach):
        i = CH.SEGMENTS.index("GENZ"); seg = P.segments[i]
        firms = {"A": FC, **P.backdrop_firms}
        att = {fn: exp(seg.beta * CH._perception_components(
            cfg, seg, i, P, P.price, reach=(reach if fn == "A" else None)) / 100.0)
            for fn, cfg in firms.items()}
        return att["A"] / sum(att.values())
    assert genz_share(r_soc) > genz_share(CH.channel_reach(CH.NEUTRAL_MIX))
