"""The fixed words the agent answers in. Grading compares these to the
ground truth mechanically, so both sides must use the same lists.

The chaos package defines the same enums for scenario files, but the agent
must never import chaos (tests/test_integrity.py). So the lists live here
too, and tests/test_agent_loop.py fails if the two ever differ.
"""

from typing import Literal, get_args

# Written out as Literal types so a type checker can follow them; the lists
# below are derived from them, so there is still one source.
FaultCategory = Literal[
    "bad_deploy",
    "config_regression",
    "timeout_regression",
    "slow_dependency",
    "poison_message",
    "iam_regression",
    "hot_row_contention",
    "missing_index",
    "throttling",
    "retry_storm",
    "no_fault",
    "insufficient_evidence",
]
Component = Literal[
    "orders",
    "cart",
    "payments",
    "fulfillment",
    "placed-orders",
    "dsql",
    "cart-table",
    "none",
]

FAULT_CATEGORIES: list[str] = list(get_args(FaultCategory))
COMPONENTS: list[str] = list(get_args(Component))

# What each category means, told to every model that answers in these words.
# Without it the models were given twelve bare names and, in M7's first
# verification sitting, found the right component and the right change and
# then picked a neighbouring category: a lowered timeout setting called
# config_regression, a slow provider behind a caller's timeouts called
# timeout_regression. One line each, because every token here is paid on
# every call.
CATEGORY_MEANINGS: dict[str, str] = {
    "bad_deploy": "new code was deployed and the service's errors began with it",
    "config_regression": (
        "a changed setting breaks the service, such as a wrong table name or "
        "environment value; not a timeout and not a concurrency or capacity limit"
    ),
    "timeout_regression": (
        "a caller's timeout setting was lowered, so calls that used to succeed "
        "are cut off; the component is the caller whose setting changed"
    ),
    "slow_dependency": (
        "a service or store that others call became slow; the component is the "
        "slow one, not the callers whose timeouts are the symptom"
    ),
    "poison_message": (
        "one malformed message fails every time it is processed and ends in "
        "the dead-letter queue, while other messages succeed"
    ),
    "iam_regression": "a permission was removed or changed, so calls are denied",
    "hot_row_contention": (
        "many concurrent transactions update the same row, so the database "
        "aborts and retries them"
    ),
    "missing_index": "a query became slow because an index it relied on is gone",
    "throttling": (
        "requests are rejected because a concurrency or capacity limit was "
        "reached, whether traffic rose or the limit was lowered"
    ),
    "retry_storm": (
        "the same work is attempted over and over, for example messages "
        "delivered again while still being processed, multiplying load"
    ),
    "no_fault": "nothing is broken, for example more traffic handled correctly",
    "insufficient_evidence": "the evidence does not show the cause",
}


def category_lines() -> str:
    """The categories with their meanings, one per line, for a prompt."""
    return "\n".join(
        f"  - {name}: {CATEGORY_MEANINGS[name]}" for name in FAULT_CATEGORIES
    )
