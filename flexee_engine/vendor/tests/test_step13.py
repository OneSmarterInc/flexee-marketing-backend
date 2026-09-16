from __future__ import annotations

from collections.abc import MutableMapping
from copy import deepcopy
import inspect
from types import MappingProxyType

import pytest

import defense_grader
from defense_grader import (
    PROSE_DIMENSIONS,
    MockModelClient,
    _cache_key,
    _packet_facts,
    build_grading_prompt,
    combine_report,
    grade_written_defense,
)


GOLDEN_PACKET = {
    "focus_group_evidence": [],
    "final_scorecard": {"overall": 7.5},
    "decisions": [
        {
            "emphasis": (0.45, 0.15, 0.05, 0.05, 0.30),
            "positioning": 20,
        }
        for _ in range(6)
    ],
}
DEFENSE_TEXT = "We defended the core while acknowledging the growth trade-off."
VALID_JSON = (
    '{"argument_coherence":70,'
    '"honest_evidence_citation":80,'
    '"ownership_of_tradeoffs":75,'
    '"justification":"The defense is structured, cites its evidence, and acknowledges key sacrifices."}'
)
EDGE_JSON = (
    '{"argument_coherence":0,'
    '"honest_evidence_citation":100,'
    '"ownership_of_tradeoffs":100,'
    '"justification":"Uses the full score range."}'
)


class RejectingCache(MutableMapping):
    def __init__(self):
        self._data = {}

    def __getitem__(self, key):
        return self._data[key]

    def __setitem__(self, key, value):
        raise TypeError("no writes")

    def __delitem__(self, key):
        del self._data[key]

    def __iter__(self):
        return iter(self._data)

    def __len__(self):
        return len(self._data)


def _golden_facts():
    return _packet_facts(GOLDEN_PACKET)


def _golden_cache_key(text=DEFENSE_TEXT, packet=GOLDEN_PACKET):
    return _cache_key(text, _packet_facts(packet))


def test_packet_facts_golden_compact_packet_and_type_validation():
    assert _packet_facts(GOLDEN_PACKET) == {
        "n_focus_groups": 0,
        "n_decisions": 6,
        "dominant_emphasis": "LEG",
        "final_overall": 7.5,
    }
    with pytest.raises(ValueError):
        _packet_facts([])


def test_packet_facts_supports_assembled_packet_shape_defensively():
    packet = {
        "focus_group_evidence": [{"team": "A"}, {"team": "A"}],
        "decision_trajectory": [
            {"round": 1, "decision": {"emphasis": (0.1, 0.6, 0.1, 0.1, 0.1), "positioning": 40}},
            {"round": 2, "decision": {"emphasis": (0.1, 0.5, 0.2, 0.1, 0.1), "positioning": 45}},
        ],
        "scorecard_arc": [
            {"round": 1, "scorecard": {"overall": 6.0}},
            {"round": 2, "scorecard": {"overall": 8.25}},
        ],
    }

    assert _packet_facts(packet) == {
        "n_focus_groups": 2,
        "n_decisions": 2,
        "dominant_emphasis": "FIT",
        "final_overall": 8.25,
    }
    assert _packet_facts({"focus_group_evidence": "bad", "decisions": [{"emphasis": "bad"}]}) == {
        "n_focus_groups": 0,
        "n_decisions": 1,
        "dominant_emphasis": None,
        "final_overall": None,
    }


def test_build_grading_prompt_locked_shape_and_facts():
    system, user = build_grading_prompt(DEFENSE_TEXT, GOLDEN_PACKET)

    assert "STRICT JSON" in system
    assert "argument_coherence" in system
    assert "honest_evidence_citation" in system
    assert "ownership_of_tradeoffs" in system
    assert "0 to 100" in system
    assert "focus groups run=0" in user
    assert "decisions recorded=6" in user
    assert "emphasized most=LEG" in user
    assert "final overall=7.5" in user
    assert DEFENSE_TEXT in user
    assert "comparing the student's claims to that factual record" in user

    _, empty_user = build_grading_prompt("No facts.", {"focus_group_evidence": [], "decisions": []})
    assert "emphasized most=none" in empty_user
    assert "final overall=unavailable" in empty_user


def test_build_grading_prompt_validation():
    with pytest.raises(ValueError):
        build_grading_prompt("", GOLDEN_PACKET)
    with pytest.raises(ValueError):
        build_grading_prompt(123, GOLDEN_PACKET)
    with pytest.raises(ValueError):
        build_grading_prompt(DEFENSE_TEXT, [])


def test_cache_key_canonical_payload_and_reproducibility():
    facts = _golden_facts()
    same_facts_different_order = {
        "decisions": list(reversed(GOLDEN_PACKET["decisions"])),
        "final_scorecard": {"overall": 7.5},
        "focus_group_evidence": [],
        "unrelated": "ignored",
    }
    changed_packet = deepcopy(GOLDEN_PACKET)
    changed_packet["final_scorecard"]["overall"] = 8.0

    assert _cache_key(DEFENSE_TEXT, facts) == _golden_cache_key()
    assert _golden_cache_key(packet=same_facts_different_order) == _golden_cache_key()
    assert _golden_cache_key(text=DEFENSE_TEXT + " More.") != _golden_cache_key()
    assert _golden_cache_key(packet=changed_packet) != _golden_cache_key()


def test_grade_written_defense_first_call_result_cache_key_and_temperature_zero():
    model = MockModelClient(VALID_JSON)

    grade = grade_written_defense(DEFENSE_TEXT, GOLDEN_PACKET, model)

    assert grade == {
        "cache_key": _golden_cache_key(),
        "prose_dimensions": {
            "argument_coherence": 70,
            "honest_evidence_citation": 80,
            "ownership_of_tradeoffs": 75,
        },
        "prose_overall": 75.0,
        "justification": "The defense is structured, cites its evidence, and acknowledges key sacrifices.",
        "cached": False,
    }
    assert len(model.calls) == 1
    assert model.calls[0]["temperature"] == 0.0


def test_cache_hit_no_model_call_stored_shape_and_hit_copy_independence():
    model = MockModelClient(VALID_JSON)
    cache = {}

    first = grade_written_defense(DEFENSE_TEXT, GOLDEN_PACKET, model, cache)
    stored = deepcopy(cache)
    second = grade_written_defense(DEFENSE_TEXT, deepcopy(GOLDEN_PACKET), model, cache)
    second["prose_dimensions"]["argument_coherence"] = 0

    assert first["cached"] is False
    assert second["cached"] is True
    assert second["cache_key"] == first["cache_key"]
    assert len(model.calls) == 1
    assert cache == stored
    assert cache[first["cache_key"]] == {
        "prose_dimensions": {
            "argument_coherence": 70,
            "honest_evidence_citation": 80,
            "ownership_of_tradeoffs": 75,
        },
        "prose_overall": 75.0,
        "justification": "The defense is structured, cites its evidence, and acknowledges key sacrifices.",
    }
    assert "cache_key" not in cache[first["cache_key"]]
    assert "cached" not in cache[first["cache_key"]]


def test_changed_inputs_independent_caches_cache_none_and_temperature():
    model = MockModelClient([
        VALID_JSON,
        '{"argument_coherence":60,"honest_evidence_citation":60,"ownership_of_tradeoffs":60,"justification":"Different text."}',
        '{"argument_coherence":50,"honest_evidence_citation":50,"ownership_of_tradeoffs":50,"justification":"Different fact."}',
        VALID_JSON,
        VALID_JSON,
    ])
    cache_a = {}
    cache_b = {}
    changed_packet = deepcopy(GOLDEN_PACKET)
    changed_packet["final_scorecard"]["overall"] = 8.0

    first = grade_written_defense(DEFENSE_TEXT, GOLDEN_PACKET, model, cache_a)
    changed_text = grade_written_defense(DEFENSE_TEXT + " More detail.", GOLDEN_PACKET, model, cache_a)
    changed_fact = grade_written_defense(DEFENSE_TEXT, changed_packet, model, cache_a)
    independent = grade_written_defense(DEFENSE_TEXT, GOLDEN_PACKET, model, cache_b)
    no_cache = grade_written_defense(DEFENSE_TEXT, GOLDEN_PACKET, model, None)

    assert len(model.calls) == 5
    assert first["cache_key"] != changed_text["cache_key"]
    assert first["cache_key"] != changed_fact["cache_key"]
    assert first["cache_key"] == independent["cache_key"] == no_cache["cache_key"]
    assert set(cache_a) == {first["cache_key"], changed_text["cache_key"], changed_fact["cache_key"]}
    assert set(cache_b) == {independent["cache_key"]}
    assert all(call["temperature"] == 0.0 for call in model.calls)


@pytest.mark.parametrize(
    "response",
    [
        "not json",
        '```json\n{"argument_coherence":70,"honest_evidence_citation":80,"ownership_of_tradeoffs":75,"justification":"x"}\n```',
        'Here is the grade: {"argument_coherence":70,"honest_evidence_citation":80,"ownership_of_tradeoffs":75,"justification":"x"}',
        '{"argument_coherence":70,"honest_evidence_citation":80,"ownership_of_tradeoffs":75,"justification":"x"} thanks',
        "[]",
        "{}",
        '{"argument_coherence":70,"honest_evidence_citation":80,"justification":"x"}',
        '{"argument_coherence":70,"honest_evidence_citation":80,"ownership_of_tradeoffs":75,"justification":"x","extra":1}',
        '{"argument_coherence":75.0,"honest_evidence_citation":80,"ownership_of_tradeoffs":75,"justification":"x"}',
        '{"argument_coherence":"75","honest_evidence_citation":80,"ownership_of_tradeoffs":75,"justification":"x"}',
        '{"argument_coherence":true,"honest_evidence_citation":80,"ownership_of_tradeoffs":75,"justification":"x"}',
        '{"argument_coherence":-1,"honest_evidence_citation":80,"ownership_of_tradeoffs":75,"justification":"x"}',
        '{"argument_coherence":101,"honest_evidence_citation":80,"ownership_of_tradeoffs":75,"justification":"x"}',
        '{"argument_coherence":NaN,"honest_evidence_citation":80,"ownership_of_tradeoffs":75,"justification":"x"}',
        '{"argument_coherence":Infinity,"honest_evidence_citation":80,"ownership_of_tradeoffs":75,"justification":"x"}',
        '{"argument_coherence":70,"honest_evidence_citation":80,"ownership_of_tradeoffs":75,"justification":""}',
        '{"argument_coherence":70,"honest_evidence_citation":80,"ownership_of_tradeoffs":75,"justification":"   "}',
        '{"argument_coherence":70,"honest_evidence_citation":80,"ownership_of_tradeoffs":75,"justification":3}',
    ],
)
def test_grade_written_defense_rejects_strict_json_failures(response):
    with pytest.raises(ValueError):
        grade_written_defense(DEFENSE_TEXT, GOLDEN_PACKET, MockModelClient(response))


def test_edge_scores_zero_and_one_hundred_are_accepted():
    grade = grade_written_defense(DEFENSE_TEXT, GOLDEN_PACKET, MockModelClient(EDGE_JSON))

    assert grade["prose_dimensions"] == {
        "argument_coherence": 0,
        "honest_evidence_citation": 100,
        "ownership_of_tradeoffs": 100,
    }
    assert grade["prose_overall"] == pytest.approx(200 / 3)
    assert grade["cached"] is False


@pytest.mark.parametrize(
    "cached_value",
    [
        [],
        {},
        {
            "prose_dimensions": {
                "argument_coherence": 70,
                "honest_evidence_citation": 80,
                "ownership_of_tradeoffs": 75,
            },
            "prose_overall": 75.0,
            "justification": "x",
            "extra": 1,
        },
        {"prose_dimensions": {"argument_coherence": 70}, "prose_overall": 70.0, "justification": "x"},
        {
            "prose_dimensions": {
                "argument_coherence": 70.0,
                "honest_evidence_citation": 80,
                "ownership_of_tradeoffs": 75,
            },
            "prose_overall": 75.0,
            "justification": "x",
        },
        {
            "prose_dimensions": {
                "argument_coherence": True,
                "honest_evidence_citation": 80,
                "ownership_of_tradeoffs": 75,
            },
            "prose_overall": 75.0,
            "justification": "x",
        },
        {
            "prose_dimensions": {
                "argument_coherence": -1,
                "honest_evidence_citation": 80,
                "ownership_of_tradeoffs": 75,
            },
            "prose_overall": 154 / 3,
            "justification": "x",
        },
        {
            "prose_dimensions": {
                "argument_coherence": 70,
                "honest_evidence_citation": 80,
                "ownership_of_tradeoffs": 75,
            },
            "prose_overall": "75",
            "justification": "x",
        },
        {
            "prose_dimensions": {
                "argument_coherence": 70,
                "honest_evidence_citation": 80,
                "ownership_of_tradeoffs": 75,
            },
            "prose_overall": float("nan"),
            "justification": "x",
        },
        {
            "prose_dimensions": {
                "argument_coherence": 70,
                "honest_evidence_citation": 80,
                "ownership_of_tradeoffs": 75,
            },
            "prose_overall": float("inf"),
            "justification": "x",
        },
        {
            "prose_dimensions": {
                "argument_coherence": 70,
                "honest_evidence_citation": 80,
                "ownership_of_tradeoffs": 75,
            },
            "prose_overall": 1.0,
            "justification": "x",
        },
        {
            "prose_dimensions": {
                "argument_coherence": 70,
                "honest_evidence_citation": 80,
                "ownership_of_tradeoffs": 75,
            },
            "prose_overall": 75.0,
            "justification": "",
        },
        {
            "prose_dimensions": {
                "argument_coherence": 70,
                "honest_evidence_citation": 80,
                "ownership_of_tradeoffs": 75,
            },
            "prose_overall": 75.0,
            "justification": 3,
        },
    ],
)
def test_malformed_cache_records_raise_value_error(cached_value):
    with pytest.raises(ValueError):
        grade_written_defense(
            DEFENSE_TEXT,
            GOLDEN_PACKET,
            MockModelClient(VALID_JSON),
            {_golden_cache_key(): cached_value},
        )


def test_cache_mutability_validation_and_assignment_failure():
    with pytest.raises(ValueError):
        grade_written_defense(DEFENSE_TEXT, GOLDEN_PACKET, MockModelClient(VALID_JSON), [])
    with pytest.raises(ValueError):
        grade_written_defense(DEFENSE_TEXT, GOLDEN_PACKET, MockModelClient(VALID_JSON), MappingProxyType({}))
    with pytest.raises(ValueError):
        grade_written_defense(DEFENSE_TEXT, GOLDEN_PACKET, MockModelClient(VALID_JSON), RejectingCache())


def test_combine_report_keeps_deterministic_and_prose_separate_without_blending():
    deterministic = {"defense_score": 82.0, "dimensions": {"outcome": 90.0}}
    prose = {
        "cache_key": _golden_cache_key(),
        "prose_dimensions": {
            "argument_coherence": 70,
            "honest_evidence_citation": 80,
            "ownership_of_tradeoffs": 75,
        },
        "prose_overall": 75.0,
        "justification": "Clear case with honest limits.",
        "cached": False,
    }

    report = combine_report(deterministic, prose)

    assert report == {
        "deterministic": deterministic,
        "prose": prose,
        "note": "reported separately; faculty weight them",
    }
    assert "overall" not in report
    assert "merged" not in report
    assert "blended" not in report
    deterministic["defense_score"] = 0.0
    prose["prose_overall"] = 0.0
    assert report["deterministic"]["defense_score"] == 82.0
    assert report["prose"]["prose_overall"] == 75.0
    with pytest.raises(ValueError):
        combine_report([], prose)
    with pytest.raises(ValueError):
        combine_report(deterministic, [])


def test_public_constants_imports_and_architecture_boundary():
    assert PROSE_DIMENSIONS == (
        "argument_coherence",
        "honest_evidence_citation",
        "ownership_of_tradeoffs",
    )
    source = inspect.getsource(defense_grader)
    assert "from live_voice import MockModelClient, ModelClient" in source
    assert "class MockModelClient" not in source
    assert "class ModelClient" not in source
    forbidden = (
        "requests",
        "openai",
        "random",
        "run_game",
        "compute_round",
        "compute_scorecard",
        "build_defense",
        "assemble_defense_packet",
        "from defense",
        "import defense",
        "Path(",
        "open(",
    )
    for token in forbidden:
        assert token not in source


def test_purity_and_non_mutation():
    packet = deepcopy(GOLDEN_PACKET)
    original_packet = deepcopy(packet)
    cache = {}
    model = MockModelClient(VALID_JSON)

    first = grade_written_defense(DEFENSE_TEXT, packet, model, cache)
    second = grade_written_defense(DEFENSE_TEXT, packet, model, cache)

    assert packet == original_packet
    assert first["cache_key"] == second["cache_key"]
    assert second["cached"] is True
