"""
Flexee Marketing Management (Torque) — Group B, Decision 8: LINE EXTENSION (the functional launch).
REFERENCE + golden source. Self-contained. Priyanshuu ports this into
flexee_engine/vendor/line_extension.py.

WHAT IT MODELS
  Whether and WHEN to launch the functional line, and how to position it against the core
  (positioning 0..100, functional/wellness at the high end). Unlike channel and message, this is
  NOT a perception tweak on the core — it is a VOLUME OVERLAY computed AFTER the core round, plus
  a crisis buffer. Two forces:
    - incremental reach: the extension brings NEW volume into the segments it fits (growth/wellness).
    - cannibalization: the closer the extension sits to the core's positioning, the more of the
      core's own volume it eats. Far positioning = mostly incremental; near = mostly cannibalizing.
  And a place to stand at the crisis: a line that is already LIVE at round 9 earns a crisis
  "response" credit that grows with how many rounds it has been live.

WHERE IT PLUGS IN (engine/GameService, AFTER compute_round for the round)
  core = compute_round(...); ext = line_extension_effect(launch_state, core_volume, seg_sizes, params_le)
  team total_volume += sum(ext.net_by_segment.values())
  and at the crisis round, feed ext.crisis_response_credit into crisis_damage(...) as `response`.
  Revenue/contribution recompute off the adjusted volume with the existing formulas.

GOLDEN-LOCK GUARANTEE
  If not launched (or before the launch round), EVERY effect is exactly zero — net volume 0 and
  crisis credit 0 — so the base engine is reproduced. All coefficients are calibrate-later.
"""

SEGMENTS = ("LEG", "FIT", "FUN", "GENZ", "MAIN")
# Positioning ideals reused from the engine's segments (heritage low -> functional/wellness high).
SEGMENT_POSITIONING_IDEAL = {"LEG": 10.0, "FIT": 55.0, "FUN": 90.0, "GENZ": 75.0, "MAIN": 45.0}

INCR_COEF = 0.12          # fraction of a segment's size the extension can newly reach at perfect fit
CANNIB_COEF = 0.35        # fraction of core volume eaten at full positioning overlap
RESP_PER_ROUND = 0.10     # crisis "response" credit earned per round the line has been live
RESP_CAP = 0.60           # ceiling on that credit


def _fit(pos, ideal):
    """Positioning fit in [0,1]: 1 at the ideal, 0 at 50 points away or more."""
    return max(0.0, 1.0 - 2.0 * abs(pos - ideal) / 100.0)


def _overlap(pos_ext, pos_core):
    """Overlap in [0,1]: 1 when the extension sits on the core, 0 at 100 points away."""
    return max(0.0, 1.0 - abs(pos_ext - pos_core) / 100.0)


def line_extension_effect(launch_state, core_volume, segment_sizes, core_positioning):
    """
    launch_state: {"launched":bool, "launch_round":int|None, "current_round":int,
                   "positioning":float 0..100}
    core_volume:  {segment: units} from compute_round for the team.
    segment_sizes:{segment: size} for the round.
    core_positioning: the team's core positioning (0..100).
    Returns dict: incremental_by_segment, cannibalized_by_segment, net_by_segment,
                  net_total, crisis_response_credit, live (bool), rounds_live (int).
    """
    launched = bool(launch_state.get("launched", False))
    lr = launch_state.get("launch_round")
    cr = int(launch_state["current_round"])
    live = launched and lr is not None and cr >= lr
    if not live:
        zero = {s: 0.0 for s in SEGMENTS}
        return {"incremental_by_segment": dict(zero), "cannibalized_by_segment": dict(zero),
                "net_by_segment": dict(zero), "net_total": 0.0,
                "crisis_response_credit": 0.0, "live": False, "rounds_live": 0}

    pos_ext = float(launch_state["positioning"])
    rounds_live = cr - int(lr) + 1
    incr, cann, net = {}, {}, {}
    ov = _overlap(pos_ext, float(core_positioning))
    for s in SEGMENTS:
        incr[s] = INCR_COEF * _fit(pos_ext, SEGMENT_POSITIONING_IDEAL[s]) * float(segment_sizes[s])
        # cannibalization is bounded by the core volume actually present in the segment
        cann[s] = min(float(core_volume.get(s, 0.0)), CANNIB_COEF * ov * float(core_volume.get(s, 0.0)))
        net[s] = incr[s] - cann[s]
    credit = min(RESP_CAP, RESP_PER_ROUND * rounds_live)
    return {"incremental_by_segment": incr, "cannibalized_by_segment": cann,
            "net_by_segment": net, "net_total": sum(net.values()),
            "crisis_response_credit": credit, "live": True, "rounds_live": rounds_live}


if __name__ == "__main__":
    # A representative core volume (units) and segment sizes for the golden.
    core_vol = {"LEG": 9.6, "FIT": 2.6, "FUN": 2.1, "GENZ": 1.2, "MAIN": 3.2}
    sizes = {"LEG": 30.0, "FIT": 18.0, "FUN": 25.0, "GENZ": 12.0, "MAIN": 15.0}
    core_pos = 20.0   # a heritage-leaning core

    # (1) NEUTRALITY / back-compat: not launched -> every effect exactly zero
    off = line_extension_effect({"launched": False, "launch_round": None, "current_round": 5,
                                 "positioning": 90.0}, core_vol, sizes, core_pos)
    assert off["net_total"] == 0.0 and off["crisis_response_credit"] == 0.0
    # also zero BEFORE the launch round
    pre = line_extension_effect({"launched": True, "launch_round": 6, "current_round": 5,
                                 "positioning": 90.0}, core_vol, sizes, core_pos)
    assert pre["net_total"] == 0.0 and not pre["live"]
    print("NEUTRALITY: not-launched and pre-launch both zero effect  (base engine reproduced)")

    # (2) GOLDEN: functional launch (positioning 90, far from the heritage core) at R6, seen at R10
    far = line_extension_effect({"launched": True, "launch_round": 6, "current_round": 10,
                                 "positioning": 90.0}, core_vol, sizes, core_pos)
    near = line_extension_effect({"launched": True, "launch_round": 6, "current_round": 10,
                                  "positioning": 30.0}, core_vol, sizes, core_pos)
    print("\n=== GOLDEN: functional launch (pos 90), rounds_live=5 ===")
    print("  incremental:", {s: round(far['incremental_by_segment'][s], 6) for s in SEGMENTS})
    print("  cannibalized:", {s: round(far['cannibalized_by_segment'][s], 6) for s in SEGMENTS})
    print("  net_by_segment:", {s: round(far['net_by_segment'][s], 6) for s in SEGMENTS})
    print(f"  net_total={far['net_total']:.6f}  crisis_response_credit={far['crisis_response_credit']:.6f}")
    print("\n=== GOLDEN: near-core launch (pos 30), rounds_live=5 ===")
    print("  net_by_segment:", {s: round(near['net_by_segment'][s], 6) for s in SEGMENTS})
    print(f"  net_total={near['net_total']:.6f}")

    # (3) STRUCTURE: near-core positioning cannibalizes more than far; far positioning nets more.
    assert sum(near["cannibalized_by_segment"].values()) > sum(far["cannibalized_by_segment"].values()), \
        "near-core launch must cannibalize more"
    assert far["net_total"] > near["net_total"], "far (differentiated) launch must net more volume"
    # incremental volume concentrates in the growth/wellness segments (FUN highest at pos 90)
    assert max(far["incremental_by_segment"], key=far["incremental_by_segment"].get) == "FUN"

    # (4) TIMING: earlier launch => more rounds live at the crisis => larger response credit
    early = line_extension_effect({"launched": True, "launch_round": 4, "current_round": 9,
                                   "positioning": 90.0}, core_vol, sizes, core_pos)
    late = line_extension_effect({"launched": True, "launch_round": 8, "current_round": 9,
                                  "positioning": 90.0}, core_vol, sizes, core_pos)
    print(f"\ncrisis response credit @R9:  launch@R4={early['crisis_response_credit']:.4f}  "
          f"launch@R8={late['crisis_response_credit']:.4f}")
    assert early["crisis_response_credit"] > late["crisis_response_credit"], "earlier launch = bigger buffer"

    # (5) cannibalization can never exceed core volume in a segment
    for s in SEGMENTS:
        assert near["cannibalized_by_segment"][s] <= core_vol[s] + 1e-12

    print("\nALL LINE-EXTENSION CHECKS PASSED: not-launched neutrality, incremental/cannibalization "
          "goldens, near-vs-far structure, launch-timing crisis buffer, bounded cannibalization.  "
          "(coefficients calibrate-later)")
