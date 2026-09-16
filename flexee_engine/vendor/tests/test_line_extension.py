"""Golden test — Group B line extension. Proves not-launched neutrality, pins the volume-overlay
goldens, checks near-vs-far structure, launch-timing crisis buffer, bounded cannibalization.
Run: python -m pytest -q test_line_extension.py"""
import line_extension as LE

CORE_VOL = {"LEG": 9.6, "FIT": 2.6, "FUN": 2.1, "GENZ": 1.2, "MAIN": 3.2}
SIZES = {"LEG": 30.0, "FIT": 18.0, "FUN": 25.0, "GENZ": 12.0, "MAIN": 15.0}
CORE_POS = 20.0
TOL = 1e-9


def test_not_launched_is_zero():
    off = LE.line_extension_effect({"launched": False, "launch_round": None,
                                    "current_round": 5, "positioning": 90.0}, CORE_VOL, SIZES, CORE_POS)
    assert off["net_total"] == 0.0 and off["crisis_response_credit"] == 0.0
    pre = LE.line_extension_effect({"launched": True, "launch_round": 6,
                                    "current_round": 5, "positioning": 90.0}, CORE_VOL, SIZES, CORE_POS)
    assert pre["net_total"] == 0.0 and not pre["live"]


def test_far_launch_golden():
    far = LE.line_extension_effect({"launched": True, "launch_round": 6, "current_round": 10,
                                    "positioning": 90.0}, CORE_VOL, SIZES, CORE_POS)
    net = {"LEG": -1.008, "FIT": 0.375, "FUN": 2.7795, "GENZ": 0.882, "MAIN": -0.156}
    for s in LE.SEGMENTS:
        assert abs(far["net_by_segment"][s] - net[s]) < TOL
    assert abs(far["net_total"] - 2.8725) < TOL
    assert abs(far["crisis_response_credit"] - 0.5) < TOL
    assert max(far["incremental_by_segment"], key=far["incremental_by_segment"].get) == "FUN"


def test_near_vs_far_structure():
    far = LE.line_extension_effect({"launched": True, "launch_round": 6, "current_round": 10,
                                    "positioning": 90.0}, CORE_VOL, SIZES, CORE_POS)
    near = LE.line_extension_effect({"launched": True, "launch_round": 6, "current_round": 10,
                                     "positioning": 30.0}, CORE_VOL, SIZES, CORE_POS)
    assert abs(near["net_total"] - (-1.2465)) < TOL
    assert sum(near["cannibalized_by_segment"].values()) > sum(far["cannibalized_by_segment"].values())
    assert far["net_total"] > near["net_total"]
    for s in LE.SEGMENTS:
        assert near["cannibalized_by_segment"][s] <= CORE_VOL[s] + 1e-12


def test_launch_timing_buffer():
    early = LE.line_extension_effect({"launched": True, "launch_round": 4, "current_round": 9,
                                      "positioning": 90.0}, CORE_VOL, SIZES, CORE_POS)
    late = LE.line_extension_effect({"launched": True, "launch_round": 8, "current_round": 9,
                                     "positioning": 90.0}, CORE_VOL, SIZES, CORE_POS)
    assert abs(early["crisis_response_credit"] - 0.6) < TOL
    assert abs(late["crisis_response_credit"] - 0.2) < TOL
    assert early["crisis_response_credit"] > late["crisis_response_credit"]
