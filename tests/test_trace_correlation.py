"""The correlation check must be able to fail.

scripts/trace_correlation.py decides from log rows whether the chain is
intact. If that decision were wrong in the lenient direction, the live check
would report success on a broken chain, so it is tested here against chains
broken in each way that matters.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "trace_correlation.py"


@pytest.fixture(scope="module")
def trace():
    spec = importlib.util.spec_from_file_location("trace_correlation", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def row(t: int, service: str, message: str, cid: str = "trace-1") -> dict:
    return {
        "timestamp": f"2026-09-22 22:00:{t:02d}.000",
        "service": service,
        "message": message,
        "correlation_id": cid,
    }


def complete() -> list[dict]:
    return [
        row(1, "cart", "cart stored"),
        row(2, "cart", "cart read"),
        row(3, "orders", "checkout complete"),
        row(9, "payments", "charge approved"),
        row(10, "fulfillment", "order paid"),
    ]


def test_a_complete_chain_passes(trace):
    assert trace.check_chain(complete()) == []


def test_extra_lines_do_not_matter(trace):
    rows = complete() + [row(5, "orders", "something else")]
    assert trace.check_chain(rows) == []


def test_input_order_does_not_matter(trace):
    assert trace.check_chain(list(reversed(complete()))) == []


def test_the_chain_stopping_at_the_queue_is_caught(trace):
    """The break this check exists for: nothing after the SQS hop."""
    problems = trace.check_chain(complete()[:3])
    assert problems == [
        "missing or out of order: payments 'charge approved'",
        "missing or out of order: fulfillment 'order paid'",
    ]


def test_steps_out_of_order_are_caught(trace):
    rows = complete()
    rows[3]["timestamp"], rows[4]["timestamp"] = (
        rows[4]["timestamp"],
        rows[3]["timestamp"],
    )
    assert trace.check_chain(rows) != []


def test_a_line_with_the_order_but_another_id_is_a_break(trace):
    lines = [
        row(3, "orders", "checkout complete"),
        row(10, "fulfillment", "order paid", cid="some-message-id"),
    ]
    problems = trace.check_order_lines(lines, "trace-1")
    assert problems == ["fulfillment 'order paid' has correlation_id some-message-id"]


def test_a_line_with_no_id_at_all_is_a_break(trace):
    lines = [{"timestamp": "t", "service": "payments", "message": "charge approved"}]
    assert trace.check_order_lines(lines, "trace-1") == [
        "payments 'charge approved' has correlation_id (none)"
    ]
