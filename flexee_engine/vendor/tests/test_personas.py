"""Validation tests for personas.py — the 28-persona Torque pool data module.

personas.py is a data module (no logic to golden-test), so this locks its shape:
the pool size and segment distribution, unique ids, the record schema the customer-agent
layer needs, the idiosyncrasy range, and a couple of anchor personas the downstream chunks
reference by id.
"""
from collections import Counter

import personas


VALID_SEGMENTS = {"LEG", "FIT", "FUN", "GENZ", "MAIN"}
EXPECTED_COUNTS = {"LEG": 6, "FIT": 5, "FUN": 6, "GENZ": 6, "MAIN": 5}


def test_pool_size_and_distribution():
    assert len(personas.POOL) == 28
    counts = Counter(p["segment"] for p in personas.POOL)
    assert dict(counts) == EXPECTED_COUNTS
    assert personas.SEGMENT_COUNTS == EXPECTED_COUNTS


def test_ids_unique_and_segments_valid():
    ids = [p["id"] for p in personas.POOL]
    assert len(set(ids)) == 28
    assert all(p["segment"] in VALID_SEGMENTS for p in personas.POOL)


def test_record_schema():
    required = {"id", "name", "segment", "idiosyncrasy"}
    for p in personas.POOL:
        assert required <= set(p)
        assert isinstance(p["id"], str) and p["id"]
        assert isinstance(p["name"], str) and p["name"]
        assert isinstance(p["segment"], str)
        assert isinstance(p["idiosyncrasy"], (int, float))


def test_idiosyncrasy_range():
    # per-persona say-do offset is a small calibration placeholder
    assert all(abs(float(p["idiosyncrasy"])) <= 0.05 for p in personas.POOL)


def test_anchor_personas_present():
    by_id = {p["id"]: p for p in personas.POOL}
    assert by_id["leg_marcus"]["segment"] == "LEG"
    assert by_id["leg_marcus"]["name"] == "Marcus"
    # a few honest anchors the design relies on
    for pid in ("fun_sarah", "fit_denise", "fun_david", "main_kevin"):
        assert by_id[pid]["honest"] is True


def test_persona_records_shape():
    recs = personas.persona_records()
    assert len(recs) == 28
    assert all(set(r) == {"id", "name", "segment", "idiosyncrasy"} for r in recs)
    assert recs[0]["id"] == personas.POOL[0]["id"]
