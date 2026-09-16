"""Golden test — Group C market research (say-do projection). Proves the one-bias-source design:
warmth binds to the existing transform, behavioral==truth, price sensitivity UNDER-claims (direction
flips) while warmth/map over-claim, the where-to-grow trap flips the recommendation, commissioning
costs debit. Run: python -m pytest -q test_research.py"""
import research as R
from research import SAYDO_GAP, GAP_GAIN
from ref_agents import revealed_warmth, stated_warmth
TOL = 1e-9


def _ctx():
    return R._ctx()


def test_warmth_binds_to_existing_transform_and_behavioral_is_truth():
    ctx = _ctx()
    stated = R.render_study("segment_perception_tracker", "stated", ctx)
    beh = R.render_study("segment_perception_tracker", "behavioral", ctx)
    for s in R.SEGMENTS:
        rev = revealed_warmth(ctx["perception_by_firm"], ctx["firm"], s)
        assert abs(stated[s]["reported"] - stated_warmth(rev, s, 0.0)) < 1e-12   # same bias source
        assert beh[s]["reported"] == beh[s]["true"]                              # behavioral == truth


def test_behavioral_is_truth_for_every_study():
    ctx = _ctx()
    for study in R.STUDY_CATALOG:
        beh = R.render_study(study, "behavioral", ctx)
        for s in R.SEGMENTS:
            assert beh[s]["reported"] == beh[s]["true"]


def test_price_sensitivity_golden_underclaims():
    ctx = _ctx()
    st = R.render_study("price_sensitivity_study", "stated", ctx)
    want = {"LEG": (0.70, 0.50), "FIT": (0.20, 0.20), "FUN": (0.05, 0.25),
            "GENZ": (0.80, 0.95), "MAIN": (1.00, 0.95)}   # (true, stated)
    for s, (t, rep) in want.items():
        assert abs(st[s]["true"] - t) < TOL and abs(st[s]["reported"] - rep) < TOL
    # the flip: overstating segments claim to be LESS price-driven; understating claim MORE
    assert st["LEG"]["reported"] < st["LEG"]["true"]     # LEG (+ warmth gap) under-claims price sensitivity
    assert st["FUN"]["reported"] > st["FUN"]["true"]     # FUN (- warmth gap) over-claims it


def test_perceptual_map_golden_overclaims():
    ctx = _ctx()
    st = R.render_study("perceptual_map", "stated", ctx)
    want = {"LEG": (0.00, 0.20), "FIT": (0.90, 0.90), "FUN": (0.40, 0.20),
            "GENZ": (0.70, 0.55), "MAIN": (0.70, 0.75)}   # (true, stated)
    for s, (t, rep) in want.items():
        assert abs(st[s]["true"] - t) < TOL and abs(st[s]["reported"] - rep) < TOL
    # map shares warmth's direction: stated-true has the sign of SAYDO_GAP
    for s in R.SEGMENTS:
        d = st[s]["reported"] - st[s]["true"]
        assert (d > 0) == (SAYDO_GAP[s] > 0) or abs(SAYDO_GAP[s]) < TOL


def test_where_to_grow_trap_flips():
    ctx = _ctx()
    assert R.where_to_grow(ctx, "stated")[0] == "LEG"                 # chase the loud overstater
    assert R.where_to_grow(ctx, "behavioral")[0] in ("FUN", "GENZ")   # surface understated growth
    assert R.where_to_grow(ctx, "stated")[0] != R.where_to_grow(ctx, "behavioral")[0]


def test_commissioning_costs():
    ok, cost, left = R.commission("price_sensitivity_study", "behavioral", 10.0)
    assert ok and cost == 7.0 and abs(left - 3.0) < TOL
    ok2, cost2, _ = R.commission("price_sensitivity_study", "behavioral", 5.0)
    assert not ok2 and cost2 == 7.0
    for study, spec in R.STUDY_CATALOG.items():
        assert spec["cost_behavioral"] > spec["cost_stated"]
