"""Engine-integrity guard for the Flexee backend.

Runs WITHOUT Django. It asserts the app is wired to the REAL verified engine (not the dev
stub), that the vendored engine is the locked calibrated build, and that a full game computes.
This would fail loudly if a vendored module broke and api.py silently fell back to the stub.
It does NOT replace the golden suite (test_step1..14 etc.) — commit those too.

Drop this at the backend repo root (or tests/) and run `python -m pytest -q test_engine_integrity.py`.
"""
import importlib


def test_real_engine_is_selected():
    api = importlib.import_module("flexee_engine.api")
    assert api.ENGINE_MODE == "real", (
        "app is on the DEV STUB, not the verified engine — a vendored import is failing "
        "and api.py fell back silently")
    assert api.SEGS == ("LEG", "FIT", "FUN", "GENZ", "MAIN")


def test_vendored_engine_is_the_locked_calibrated_build():
    importlib.import_module("flexee_engine")   # puts vendor/ on sys.path
    import personas, advisors
    import flexee_calibrated_config as cfg
    import engine
    from collections import Counter

    assert len(personas.POOL) == 28
    assert dict(Counter(p["segment"] for p in personas.POOL)) == {
        "LEG": 6, "FIT": 5, "FUN": 6, "GENZ": 6, "MAIN": 5}
    assert len(advisors.ADVISORS) == 6
    assert cfg.CALIBRATED_PARAMS.positioning_bonus_max == 32.0
    assert cfg.CALIBRATED_CRISIS_DAMAGE_BASE == 25.0
    # reference (test-baseline) engine defaults must stay untouched
    assert engine.DEFAULT_PARAMS.positioning_bonus_max == 20.0


def test_full_game_computes():
    importlib.import_module("flexee_engine")
    from game_faculty import run_game_with_faculty
    from faculty import DEFAULT_FACULTY_CONFIG
    from crisis import CrisisParams
    from flexee_calibrated_config import (
        CALIBRATED_PARAMS, CALIBRATED_EQUITY_PARAMS, CALIBRATED_PROMOTION_PARAMS,
        CALIBRATED_CRISIS_DAMAGE_BASE)

    teams = ["A", "B", "C", "D"]
    subs = [{t: {} for t in teams} for _ in range(14)]
    for r in range(3, 14):
        for t in teams:
            subs[r][t] = {"brand": 5, "perf": 2, "research": 3, "emphasis": [.2, .2, .2, .2, .2]}
    cp = CrisisParams(crisis_round=9, damage_base=CALIBRATED_CRISIS_DAMAGE_BASE,
                      trust_fraction=.5, prod_fraction=.3, eq_fraction=.2,
                      lower_bound=0.0, upper_bound=100.0)
    hist = run_game_with_faculty(
        teams, subs, DEFAULT_FACULTY_CONFIG, params=CALIBRATED_PARAMS,
        equity_params=CALIBRATED_EQUITY_PARAMS, promotion_params=CALIBRATED_PROMOTION_PARAMS,
        crisis_params=cp, crisis_inputs=None)
    assert len(hist) == 14
    assert {"A", "B", "C", "D"} <= set(hist[-1]["firms"])
    assert "effective_decisions" in hist[-1]
    assert 0.0 <= hist[-1]["firms"]["A"]["total_market_share"] <= 1.0
