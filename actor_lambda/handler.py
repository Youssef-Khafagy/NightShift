"""Lambda entry point for the Actor. The code lives in the `actor` package,
zipped beside `agent` (the allowlist and approvals) and `ops` (alias moves),
so tests import exactly what runs."""

from actor.handler import handler

__all__ = ["handler"]
