"""The cost check's thresholds and bookkeeping.

The script's job is to say ALERT when an allowance is nearly spent, so the
thresholds are tested at their edges, and so is the check for usage from
services nobody planned to use.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "cost_check.py"


@pytest.fixture(scope="module")
def cost():
    spec = importlib.util.spec_from_file_location("cost_check", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    ("used", "expected"),
    [
        (0, "OK"),
        (49.99, "OK"),
        (50, "WATCH"),
        (84.99, "WATCH"),
        (85, "ALERT"),
        (120, "ALERT"),
    ],
)
def test_thresholds(cost, used, expected):
    assert cost.status(used, 100) == expected


def test_a_zero_limit_is_unknown_not_ok(cost):
    assert cost.status(5, 0) == "?"


def test_free_tier_rows_carry_the_numbers(cost):
    rows = cost.free_tier_rows(
        [
            {
                "service": "AWS Lambda",
                "usageType": "Request",
                "actualUsageAmount": 900_000.0,
                "limit": 1_000_000.0,
                "unit": "Request",
                "forecastedUsageAmount": 1_200_000.0,
            }
        ]
    )
    assert rows[0]["status"] == "ALERT"
    assert rows[0]["percent"] == 90.0
    assert rows[0]["name"] == "AWS Lambda: Request"


def test_unexpected_services_are_listed(cost):
    """EC2 is on this project's forbidden list, so it must always stand out."""
    usages = [
        {"service": "AWS Lambda"},
        {"service": "Amazon Elastic Compute Cloud - Compute"},
        {"service": "Amazon Simple Queue Service"},
    ]
    assert cost.unexpected_services(usages) == [
        "Amazon Elastic Compute Cloud - Compute"
    ]


def test_sqs_estimate_errs_high(cost):
    """Batched calls are counted per message, so the estimate can only be
    above the real request count, never below."""
    assert cost.sqs_requests_upper_bound(56, 56, 56, 119) == 287
