"""Tests for games/enrichment_overlay.py, run against the vendored engine (no Django needed).

Collected by pytest.ini alongside the engine goldens. Each test pins one of the fixes from
the 2026-09-23 review; the no-launch identity tests are the app-layer analogue of the
golden lock (a game that never launches must be exactly the engine run).
"""
import copy

import pytest

from flexee_engine import api
from games.enrichment_overlay import (
    LINE_EXT_CARRY_COST,
    LINE_EXT_LAUNCH_COST,
    apply_line_extension,
    crisis_inputs_with_line_extension,
    student_research_sheet,
)
import research as R  # vendored (on sys.path via flexee_engine)
from scorecard import compute_scorecard

TEAMS = ["A", "B", "C", "D"]
N = 14
BUDGET = 10.0


@pytest.fixture(scope="module")
def history():
    return api.run_history(TEAMS, [{t: {} for t in TEAMS} for _ in range(N)], {})


def _overlay(history, info, launch=LINE_EXT_LAUNCH_COST, carry=LINE_EXT_CARRY_COST):
    return apply_line_extension(copy.deepcopy(history), info, BUDGET, launch, carry)


def _cum_delta(history, info, team="A", **kw):
    h = _overlay(history, info, **kw)
    return sum(h[r]["firms"][team]["contribution"] - history[r]["firms"][team]["contribution"]
               for r in range(N))


# ------------------------------------------------------------------ 1. research truth leak
@pytest.mark.parametrize("study", sorted(R.STUDY_CATALOG))
@pytest.mark.parametrize("mode", ["stated", "behavioral"])
def test_student_sheet_never_carries_truth(study, mode):
    raw = R.render_study(study, mode, R._ctx())
    assert all("true" in v for v in raw.values())          # the engine sheet has truth...
    safe = student_research_sheet(raw)
    for seg, v in safe.items():
        assert set(v) == {"reported"}                        # ...the student sheet does not
        assert v["reported"] == raw[seg]["reported"]


def test_where_to_grow_sheet_passes_through():
    sheet = {"ranking": R.where_to_grow(R._ctx(), "stated")}
    assert student_research_sheet(sheet) == sheet


# ------------------------------------------------------- 2. no launch == the engine exactly
def test_no_launch_is_identity(history):
    assert _overlay(history, {}) == history


def test_rounds_before_launch_are_untouched(history):
    h = _overlay(history, {"A": {"launch_round": 6, "positioning": 90.0}})
    assert h[:5] == history[:5]
    assert h[5] != history[5]


# ------------------------------------------- 3. what is scored matches what is displayed
def test_scorecard_and_shares_follow_the_overlay(history):
    h = _overlay(history, {"A": {"launch_round": 4, "positioning": 90.0}})
    for rec, base in zip(h[3:], history[3:]):
        firms = rec["firms"]
        assert rec["scorecard"] == compute_scorecard(firms)
        assert rec["scorecard"]["firms"]["A"]["raw"]["financial"] == pytest.approx(
            firms["A"]["contribution"])
        assert sum(f["total_market_share"] for f in firms.values()) == pytest.approx(1.0)
        assert firms["A"]["total_volume"] == pytest.approx(sum(firms["A"]["volume"].values()))
        assert firms["A"]["total_market_share"] > base["firms"]["A"]["total_market_share"]
        assert firms["A"]["revenue"] == pytest.approx(firms["A"]["total_volume"] * firms["A"]["price"])


# ------------------------------------------------------------- 4. the launch costs money
def test_launch_and_carry_costs_are_charged(history):
    info = {"A": {"launch_round": 4, "positioning": 90.0}}
    free = _overlay(history, info, launch=0.0, carry=0.0)
    costed = _overlay(history, info)
    for r in range(N):
        diff = free[r]["firms"]["A"]["contribution"] - costed[r]["firms"]["A"]["contribution"]
        if r + 1 < 4:
            assert diff == 0.0
        elif r + 1 == 4:
            assert diff == pytest.approx(LINE_EXT_LAUNCH_COST + LINE_EXT_CARRY_COST)
        else:
            assert diff == pytest.approx(LINE_EXT_CARRY_COST)


def test_baseline_launch_economics_teach_timing_and_differentiation(history):
    """Guard on the DEFAULT costs in a baseline game. If calibration changes the costs or
    the engine margin, re-check this deliberately rather than loosening it."""
    far = {r: _cum_delta(history, {"A": {"launch_round": r, "positioning": 90.0}})
           for r in (4, 8, 11, 13)}
    near = _cum_delta(history, {"A": {"launch_round": 4, "positioning": 40.0}})
    assert far[4] > far[8] > far[11] > 0.0 > far[13]
    assert near < 0.0


# --------------------------------------------------- 5. the crisis sees the line extension
def test_crisis_credit_folds_into_response_without_mutating_input():
    ci = {t: {"exposure": 0.65, "preempt": 0.35, "response": 0.3} for t in TEAMS}
    frozen = copy.deepcopy(ci)
    info = {"A": {"launch_round": 4, "positioning": 90.0},
            "B": {"launch_round": 8, "positioning": 90.0},
            "C": {"launch_round": 10, "positioning": 90.0}}
    out = crisis_inputs_with_line_extension(ci, info, 9)
    assert ci == frozen
    assert out["A"]["response"] == pytest.approx(1 - 0.7 * (1 - 0.6))   # 6 rounds live, capped
    assert out["B"]["response"] == pytest.approx(1 - 0.7 * (1 - 0.2))   # 2 rounds live
    assert out["C"]["response"] == 0.3                                   # launched after the crisis
    assert out["D"]["response"] == 0.3                                   # never launched
    assert crisis_inputs_with_line_extension(None, info, 9) is None


def test_crisis_credit_reduces_live_damage():
    subs = [{t: {} for t in TEAMS} for _ in range(N)]
    ci = {t: {"exposure": 0.65, "preempt": 0.35, "response": 0.3} for t in TEAMS}
    info = {"A": {"launch_round": 4, "positioning": 90.0}}
    base = api.run_history(TEAMS, subs, {"crisis_inputs": ci})
    adj = api.run_history(TEAMS, subs, {"crisis_inputs": crisis_inputs_with_line_extension(ci, info, 9)})
    r9 = 8
    assert base[r9]["damage"]["A"] > 0.0
    assert adj[r9]["damage"]["A"] < base[r9]["damage"]["A"]
    assert adj[r9]["damage"]["D"] == base[r9]["damage"]["D"]
