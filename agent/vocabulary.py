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
