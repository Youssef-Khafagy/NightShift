"""The report's rules, the postmortem's honesty, and deterministic grading."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from agent import postmortem, report
from agent.state import Call, InvestigationState, Step
from evaluation.grade import grade

TRIGGER = {
    "name": "nightshift-cart-errors",
    "state": "ALARM",
    "since": "2026-09-23T14:49:20Z",
    "reason": "Threshold Crossed",
}


def state(*tools: str) -> InvestigationState:
    s = InvestigationState(
        "abc123", TRIGGER, "2026-09-23T14:50:00+00:00", "groq", "gpt-oss"
    )
    for i, tool in enumerate(tools, 1):
        s.steps.append(
            Step(
                i,
                i,
                f"2026-09-23T14:5{i}:00+00:00",
                tool,
                {},
                f"c{i}",
                "{}",
                f"summary of {tool}",
            )
        )
    s.calls.append(Call(1, "t", "groq", "gpt-oss", 1000, 100, 0, 1200))
    return s


ANSWER = {
    "root_cause_component": "cart",
    "fault_category": "config_regression",
    "confidence": 85,
    "summary": "cart points at a table that does not exist.",
    "evidence_steps": "1, 2",
    "proposed_actions": "- roll cart back to the previous version\n- fix CART_TABLE_NAME",
}


def test_a_good_answer_becomes_a_report():
    r = report.from_answer(state("get_function_config", "query_logs"), ANSWER)
    assert r.evidence == [1, 2]
    assert r.evidence_tools == {1: "get_function_config", 2: "query_logs"}
    assert r.proposed_actions == [
        "roll cart back to the previous version",
        "fix CART_TABLE_NAME",
    ]


@pytest.mark.parametrize(
    "change, message",
    [
        ({"fault_category": "no_fault"}, "root_cause_component must be none"),
        ({"root_cause_component": "none"}, "use no_fault or insufficient_evidence"),
        ({"evidence_steps": ""}, "at least one evidence step"),
        ({"evidence_steps": "9"}, "at least one evidence step that found something"),
        ({"evidence_steps": "3"}, "at least one evidence step that found something"),
        ({"confidence": 150}, "less than or equal to 100"),
        ({"fault_category": "gremlins"}, "Input should be"),
    ],
)
def test_bad_answers_are_refused_with_a_readable_reason(change, message):
    with pytest.raises(ValueError, match=message):
        report.from_answer(
            state("get_function_config", "query_logs", "note_hypotheses"),
            {**ANSWER, **change},
        )


def test_no_fault_needs_no_evidence():
    answer = {
        **ANSWER,
        "root_cause_component": "none",
        "fault_category": "no_fault",
        "evidence_steps": "",
    }
    assert report.from_answer(state("get_metrics"), answer).fault_category == "no_fault"


def test_a_stopped_investigation_reports_insufficient_evidence_and_why():
    s = state("get_metrics")
    s.finished, s.stop_reason = True, "max_tokens"
    r = report.build(s)
    assert (r.root_cause_component, r.fault_category, r.confidence) == (
        "none",
        "insufficient_evidence",
        0,
    )
    assert "max_tokens" in r.summary and r.stop_reason == "max_tokens"


def test_the_postmortem_is_built_from_the_record():
    s = state("get_function_config", "query_logs")
    s.final = ANSWER
    s.hypotheses = [{"status": "likely", "text": "wrong table name"}]
    s.hypothesis_changes = [
        {
            "step": 1,
            "before": [{"status": "possible", "text": "DynamoDB outage"}],
            "after": s.hypotheses,
        }
    ]
    md = postmortem.render(s, report.build(s))
    for expected in [
        "# Postmortem: nightshift-cart-errors",
        "| Fault category | `config_regression` |",
        "step 1: `get_function_config`",
        "- Step 2, `query_logs`: summary of query_logs",
        "- fix CART_TABLE_NAME",
        "- likely: wrong table name",
        "Dropped along the way:",
        "- DynamoDB outage",
        "1,000 input and 100 output tokens",
        "the agent changed nothing",
    ]:
        assert expected in md, expected


# -- grading ---------------------------------------------------------------------

RESULT = {
    "run_id": "02-config-regression-x",
    "scenario": 2,
    "ground_truth": {"component": "cart", "fault_category": "config_regression"},
    "injected_at": "2026-09-23T14:48:44+00:00",
}


def answered(component: str, category: str) -> dict:
    return {
        "investigation_id": "abc",
        "root_cause_component": component,
        "fault_category": category,
        "confidence": 70,
    }


def test_right_answer():
    g = grade(
        RESULT, answered("cart", "config_regression"), "2026-09-23T14:53:44+00:00"
    )
    assert g.root_cause_correct and not g.hedged and g.diagnosis_seconds == 300


def test_right_category_wrong_component_is_wrong():
    g = grade(RESULT, answered("orders", "config_regression"))
    assert g.category_correct and not g.component_correct and not g.root_cause_correct


def test_hedging_is_counted_apart_from_being_wrong():
    g = grade(RESULT, answered("none", "insufficient_evidence"))
    assert g.hedged and not g.root_cause_correct


def test_no_fault_is_graded_on_the_category():
    spike = {
        **RESULT,
        "ground_truth": {"component": "none", "fault_category": "no_fault"},
    }
    assert grade(spike, answered("none", "no_fault")).root_cause_correct
    assert not grade(spike, answered("orders", "throttling")).root_cause_correct


def test_a_skipped_or_failed_step_is_dropped_from_the_evidence():
    s = state("get_alarm", "get_topology", "get_metrics")
    s.steps[1].result = '{"error": "skipped: at most 3 calls per reply"}'
    s.steps[2].result = (
        '{"tool": "get_metrics", "truncated": false, '
        '"untrusted_data": {"error": "AWS AccessDenied: no"}}'
    )
    # The M6 live check: a correct answer citing real and skipped steps.
    r = report.from_answer(s, {**ANSWER, "evidence_steps": "1,2,3"})
    assert r.evidence == [1] and r.evidence_dropped == [2, 3]
    # Nothing real left: refused.
    with pytest.raises(ValueError, match="found something"):
        report.from_answer(s, {**ANSWER, "evidence_steps": "2,3"})


def envelope(tool: str, data: dict) -> str:
    return json.dumps({"tool": tool, "truncated": False, "untrusted_data": data})


@pytest.mark.parametrize(
    "data, empty",
    [
        ({"status": "Complete", "rows": [], "bytes_scanned": 0}, True),
        ({"status": "Complete", "rows": [{"message": "boom"}]}, False),
        ({"metric": "Errors", "points": [], "summary": "no data"}, True),
        ({"metric": "Errors", "points": [["02:33Z", 35.0]]}, False),
        ({"service": "x", "traces": 0, "with_error": 0}, True),
        ({"service": "x", "traces": 284, "with_error": 117}, False),
        ({"window_hours": 2, "moves": []}, True),
        ({"window_minutes": 60, "changes": []}, True),
        ({"function": "nightshift-cart", "timeout_seconds": 5}, False),
        ({"name": "orders-errors", "state": "ALARM"}, False),
    ],
)
def test_found_nothing_knows_each_tools_empty_shape(data, empty):
    assert report.found_nothing(envelope("t", data)) is empty


def test_an_empty_check_is_not_root_cause_evidence():
    """Postmortem 6ee6c5aa5f70 listed an empty log query and an empty metric
    as root cause evidence for a bad deploy."""
    s = state("query_logs", "get_function_config", "get_metrics")
    s.steps[0].result = envelope("query_logs", {"status": "Complete", "rows": []})
    s.steps[2].result = envelope("get_metrics", {"metric": "m", "points": []})
    r = report.from_answer(s, {**ANSWER, "evidence_steps": "1,2,3"})
    assert r.evidence == [2] and r.evidence_empty == [1, 3]
    md = postmortem.render(s, r)
    assert "- Step 1," not in md and "- Step 2," in md
    assert "Cited but found nothing, so not counted as evidence: step(s) 1, 3." in md
    assert "step 1: `query_logs` (found nothing)" in md
    with pytest.raises(ValueError, match="empty results rule things out"):
        report.from_answer(s, {**ANSWER, "evidence_steps": "1,3"})


def test_the_timeline_shows_a_refused_answer_as_refused():
    s = state("get_metrics", "finish_investigation", "finish_investigation")
    s.steps[1].result = json.dumps(
        {"error": "finish_investigation rejected", "problems": ["evidence step 9"]}
    )
    s.final = {**ANSWER, "evidence_steps": "1"}
    md = postmortem.render(s, report.build(s))
    assert "step 2: `finish_investigation` (refused: evidence step 9)" in md
