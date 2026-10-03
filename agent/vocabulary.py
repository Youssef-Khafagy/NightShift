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
#
# Each category is defined by what changed, not by the error it produces.
# Pass m7 (2026-10-03) showed the first wording described symptoms, and the
# wrong answers fitted it word for word: a slow provider holds its
# concurrency limit longer, so "a limit was reached" called it throttling;
# a role scoped to the real table denies a wrong table name, so "calls are
# denied" called it an IAM regression; and any changed setting read as a
# config regression. The order of checks matters too, so the specific
# setting categories say they come before config_regression.
CATEGORY_MEANINGS: dict[str, str] = {
    "bad_deploy": (
        "a deploy changed the service's code and its errors began with it; a "
        "deploy that changed only settings is not bad_deploy"
    ),
    "config_regression": (
        "a changed setting other than a timeout, queue setting or concurrency "
        "limit broke the service, such as a wrong table name; calls denied "
        "after such a change are this, not iam_regression"
    ),
    "timeout_regression": (
        "a caller's timeout setting was lowered, so calls are cut off although "
        "the callee is no slower; the component is the caller that changed"
    ),
    "slow_dependency": (
        "a service or store that others call got slower with no change to its "
        "callers; the component is the slow one, and throttles or timeouts in "
        "front of it are symptoms"
    ),
    "poison_message": (
        "one malformed message fails every time it is processed and ends in "
        "the dead-letter queue, while other messages succeed"
    ),
    "iam_regression": (
        "a role's policy was changed and lost a permission the service uses; "
        "denied calls alone are not enough without that policy change"
    ),
    "hot_row_contention": (
        "many concurrent transactions update the same row, so the database "
        "aborts and retries them; the retries concentrate on one item"
    ),
    "missing_index": "a query became slow because an index it relied on is gone",
    "throttling": (
        "traffic rose past a concurrency or capacity limit, or the limit was "
        "lowered; not requests that got slower and held the limit longer"
    ),
    "retry_storm": (
        "a queue or retry setting makes the same work run again and again, "
        "such as messages redelivered while still being processed, so healthy "
        "messages can reach the dead-letter queue"
    ),
    "no_fault": (
        "nothing changed and nothing is broken; more traffic handled "
        "correctly, even with some retries or a stray throttle"
    ),
    "insufficient_evidence": "the evidence does not show the cause",
}


def category_lines() -> str:
    """The categories with their meanings, one per line, for a prompt."""
    return "\n".join(
        f"  - {name}: {CATEGORY_MEANINGS[name]}" for name in FAULT_CATEGORIES
    )
