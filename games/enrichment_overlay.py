"""Pure, Django-free helpers for the enrichment overlays (research, line extension, crisis).

Everything here composes over engine outputs. Nothing edits ``flexee_engine/vendor/`` —
the golden-locked engine and its 755 tests are untouched. Keeping these as plain functions
(no ORM, no request) means they are tested directly against the vendored engine in
``games/test_enrichment_overlay.py``, which runs in the same pytest pass as the goldens.

Fixes in this module (review of 2026-09-23):

1. ``student_research_sheet`` — a research sheet from ``research.render_study`` carries BOTH
   the engine truth and the reported value per segment. Only ``reported`` may leave the
   server; otherwise a student who pays for the cheap stated study reads behavioral truth
   in the JSON. (The vendored reference returns both for verification; the gap was that
   nothing stripped it at the API boundary.)
2. ``crisis_inputs_with_line_extension`` — the line extension's crisis-response credit was
   computed but never reached the crisis. It now folds into each launched team's
   ``response`` before the engine runs, so a live functional line is a place to stand at R9.
3. ``apply_line_extension`` — the overlay now (a) charges a launch cost and a per-round
   carry cost, so the launch is a trade-off rather than free margin, (b) updates per-segment
   volume and every firm's shares, since the extension changes the market, and
   (c) recomputes the scorecard, so what is scored matches what is displayed.
"""
from __future__ import annotations

import flexee_engine  # noqa: F401  (puts vendor/ on sys.path)

import line_extension as LE
from engine import DEFAULT_PARAMS as _ENGINE_DEFAULTS
from scorecard import compute_scorecard

GROWTH_SEGMENTS = tuple(_ENGINE_DEFAULTS.growth_segments)

# Calibrate-later defaults, in the engine's abstract budget units (round budget = 10).
# Sized from a measured baseline game (4 teams, calibrated params): without costs a far
# (positioning 90) launch adds about +4.2 contribution a round, and even a near-core launch
# is positive, so the lever was free margin. At 8.0 to launch plus 1.5 a round, cumulative
# contribution for a far launch is +15.0 at R4, +8.3 at R8, +2.1 at R11, about breakeven at
# R12 and -2.7 at R13, and a near-core launch (positioning 40 against a core at 35) is -13.4.
# Early and differentiated pays; late or near-core does not. Re-check when calibrating.
LINE_EXT_LAUNCH_COST = 8.0
LINE_EXT_CARRY_COST = 1.5


# --------------------------------------------------------------------------- research
def student_research_sheet(sheet):
    """Remove engine truth from a research sheet before it reaches a student.

    Study sheets are ``{segment: {"true": x, "reported": y}}``; only ``reported`` leaves.
    Where-to-grow sheets are ``{"ranking": [...]}`` and carry no truth, so they pass through.
    """
    if not isinstance(sheet, dict) or "ranking" in sheet:
        return sheet
    return {seg: ({"reported": v.get("reported")} if isinstance(v, dict) else v)
            for seg, v in sheet.items()}


# ----------------------------------------------------------------------------- crisis
def _crisis_credit(launch_round: int, crisis_round: int, positioning: float) -> float:
    """The vendored reference's own credit at the crisis round (no duplicated formula)."""
    ext = LE.line_extension_effect(
        {"launched": True, "launch_round": int(launch_round),
         "current_round": int(crisis_round), "positioning": float(positioning)},
        {}, {s: 0.0 for s in LE.SEGMENTS}, 0.0)
    return float(ext["crisis_response_credit"])


def crisis_inputs_with_line_extension(crisis_inputs, line_info, crisis_round):
    """Return a NEW crisis_inputs dict with each launched team's credit folded into ``response``.

    Protections combine independently: response' = 1 - (1 - response) * (1 - credit), which
    stays below 1 and rewards an early launch more than a late one. Teams that launched
    after the crisis round get nothing. The input is never mutated, so re-deriving history
    cannot compound the credit.
    """
    if not crisis_inputs:
        return crisis_inputs
    out = {team: dict(v) for team, v in crisis_inputs.items()}
    for team, li in (line_info or {}).items():
        lr = li.get("launch_round")
        if team not in out or lr is None or int(lr) > int(crisis_round):
            continue
        credit = _crisis_credit(int(lr), int(crisis_round), float(li.get("positioning", 90.0)))
        base = float(out[team].get("response", 0.0))
        out[team]["response"] = 1.0 - (1.0 - base) * (1.0 - credit)
    return out


# --------------------------------------------------------------------- line extension
def _refresh_market_shares(firms: dict, sizes: dict, growth_segments=GROWTH_SEGMENTS) -> None:
    """Recompute per-segment share, total market share and growth share for every firm
    from current volumes, using the engine's own definitions."""
    segs = list(sizes)
    seg_total = {s: sum(float((f.get("volume") or {}).get(s, 0.0)) for f in firms.values())
                 for s in segs}
    market = sum(float(f.get("total_volume") or 0.0) for f in firms.values())
    growth_size = sum(float(sizes[s]) for s in growth_segments if s in sizes)
    for f in firms.values():
        v = f.get("volume") or {}
        f["share"] = {s: (float(v.get(s, 0.0)) / seg_total[s] if seg_total[s] else 0.0)
                      for s in segs}
        f["total_market_share"] = (float(f.get("total_volume") or 0.0) / market) if market else 0.0
        f["growth_segment_share"] = (
            sum(float(v.get(s, 0.0)) for s in growth_segments) / growth_size if growth_size else 0.0)


def apply_line_extension(history: list, line_info: dict, budget: float,
                         launch_cost: float = LINE_EXT_LAUNCH_COST,
                         carry_cost: float = LINE_EXT_CARRY_COST,
                         growth_segments=GROWTH_SEGMENTS) -> list:
    """Overlay each launched team's functional line onto the engine history, in place.

    ``line_info`` is ``{team: {"launch_round": int, "positioning": float}}``. With no
    launches the history is returned untouched, so a game that never launches is
    byte-identical to the engine run.
    """
    if not line_info:
        return history
    for rec in history:
        firms = rec.get("firms") or {}
        sizes = rec.get("segment_sizes") or {}
        eff = rec.get("effective_decisions") or {}
        touched = False
        for team, li in line_info.items():
            firm = firms.get(team)
            if not firm:
                continue
            state = {"launched": True, "launch_round": li["launch_round"],
                     "current_round": rec["round"], "positioning": li["positioning"]}
            core_vol = {s: float(x) for s, x in (firm.get("volume") or {}).items()}
            ext = LE.line_extension_effect(
                state, core_vol, sizes, float(eff.get(team, {}).get("positioning", 35.0)))
            if not ext.get("live"):
                continue
            orig_total = float(firm.get("total_volume") or 0.0)
            # per-unit margin implied by the engine: contribution = units * margin - budget
            margin = ((float(firm.get("contribution") or 0.0) + budget) / orig_total
                      if orig_total else 0.0)
            vol = {s: max(0.0, core_vol.get(s, 0.0) + float(ext["net_by_segment"].get(s, 0.0)))
                   for s in core_vol}
            new_total = sum(vol.values())
            cost = float(carry_cost) + (float(launch_cost) if rec["round"] == li["launch_round"] else 0.0)
            firm["volume"] = vol
            firm["total_volume"] = new_total
            firm["revenue"] = new_total * float(firm.get("price") or 0.0)
            firm["contribution"] = margin * new_total - budget - cost
            firm["line_extension"] = {
                "net_total": ext["net_total"], "rounds_live": ext["rounds_live"],
                "incremental": ext["incremental_by_segment"],
                "cannibalized": ext["cannibalized_by_segment"],
                "crisis_response_credit": ext["crisis_response_credit"],
                "cost": cost,
            }
            touched = True
        if touched:
            _refresh_market_shares(firms, sizes, growth_segments)
            rec["scorecard"] = compute_scorecard(firms)
    return history
