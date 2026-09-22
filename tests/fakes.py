"""A psycopg-shaped connection that models transaction state.

These tests exist because of a bug that had no functional symptom. Four code
paths left a database transaction open, every request still returned the right
status code, and the only evidence was a billing metric. DSQL charges by how
long a transaction stays open, so each leak cost up to 315 DPU. See the DSQL
cost model in COST.md.

Catching that class of bug needs an assertion about connection state, not
about responses, and the connection has to model psycopg's state machine
honestly or the assertion proves nothing:

- With autocommit off, psycopg opens a transaction on the first statement and
  holds it until someone commits or rolls back. This is the behaviour that
  caused the leak.
- With autocommit on, a lone statement is its own transaction and the
  connection is idle again the moment it returns.
- `conn.transaction()` begins explicitly, commits on a clean exit and rolls
  back on an exception, either way leaving the connection idle.

`test_transaction_hygiene.py` starts by proving this fake still reproduces the
original leak. A fake that cannot fail would make every other test in the file
worthless.
"""

from __future__ import annotations

import inspect
from contextlib import contextmanager
from typing import Any, Self

from botocore.exceptions import ClientError
from psycopg.pq import TransactionStatus


def production_autocommit() -> bool:
    """Whatever `dsql.connect` actually defaults to today.

    The handler tests build their fake connection with this rather than
    hard-coding True, which ties them to the fix instead of to a convention.
    Flip the default in src/common/dsql.py back to False and every one of
    them fails with a transaction left open, which is the original bug
    reproduced rather than merely described.
    """
    from common import dsql

    default = inspect.signature(dsql.connect).parameters["autocommit"].default
    return bool(default)


def normalise(sql: str) -> str:
    """Collapse the whitespace of a multi-line SQL literal onto one line."""
    return " ".join(sql.split())


class FakeInfo:
    def __init__(self, connection: FakeConnection) -> None:
        self._connection = connection

    @property
    def transaction_status(self) -> TransactionStatus:
        return self._connection.status


class FakeCursor:
    def __init__(self, connection: FakeConnection) -> None:
        self._connection = connection
        self.rowcount = -1
        self._rows: list[tuple[Any, ...]] = []

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def execute(self, sql: str, params: Any = None) -> None:
        self._connection.on_execute(sql, params)
        result = self._connection.resolve(sql)
        if isinstance(result, BaseException):
            raise result
        if isinstance(result, list):
            self._rows = list(result)
            self.rowcount = len(result)
        else:
            self._rows = []
            self.rowcount = int(result)

    def executemany(self, sql: str, seq: list[Any]) -> None:
        for params in seq:
            self.execute(sql, params)

    def fetchone(self) -> tuple[Any, ...] | None:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[tuple[Any, ...]]:
        return list(self._rows)


class FakeConnection:
    """Enough of psycopg.Connection to drive a handler and watch its state.

    `rules` maps a fragment of SQL to what running it should produce: a list
    of rows for a query, an integer for a rowcount, or an exception instance
    to raise. Fragments are matched against whitespace-normalised SQL, so a
    multi-line literal in the source can be matched by a readable phrase.
    """

    def __init__(
        self,
        *,
        autocommit: bool = True,
        rules: list[tuple[str, Any]] | None = None,
    ) -> None:
        self.autocommit = autocommit
        self.closed = False
        self.status = TransactionStatus.IDLE
        self.rules = list(rules or [])
        self.statements: list[str] = []
        # 'begin', 'commit' and 'rollback' in the order they happened, which
        # is how the atomicity tests check that checkout is still one unit.
        self.events: list[str] = []
        self._depth = 0

    @property
    def info(self) -> FakeInfo:
        return FakeInfo(self)

    def on_execute(self, sql: str, params: Any) -> None:
        self.statements.append(normalise(sql))
        if self._depth == 0 and not self.autocommit:
            # The leak, faithfully: no explicit transaction, autocommit off,
            # and the connection is now holding one open until told otherwise.
            self.status = TransactionStatus.INTRANS

    def resolve(self, sql: str) -> Any:
        text = normalise(sql)
        for fragment, result in self.rules:
            if normalise(fragment) in text:
                return result
        # An unmatched statement is a write that changed one row. Queries that
        # matter to a test are always given a rule.
        return 1

    def cursor(self) -> FakeCursor:
        return FakeCursor(self)

    @contextmanager
    def transaction(self):
        self._depth += 1
        self.status = TransactionStatus.INTRANS
        self.events.append("begin")
        try:
            yield self
        except BaseException:
            self._depth -= 1
            self.status = TransactionStatus.IDLE
            self.events.append("rollback")
            raise
        else:
            self._depth -= 1
            self.status = TransactionStatus.IDLE
            self.events.append("commit")

    def commit(self) -> None:
        self.status = TransactionStatus.IDLE
        self.events.append("commit")

    def rollback(self) -> None:
        self.status = TransactionStatus.IDLE
        self.events.append("rollback")

    def close(self) -> None:
        self.closed = True

    def is_idle(self) -> bool:
        return self.status == TransactionStatus.IDLE


class FakeSSM:
    """Just ssm.get_parameter, with the error shapes boto3 really raises.

    `values` maps parameter names to string values. Set `error` to an
    exception instance to make every call raise it, which is how the tests
    simulate SSM being throttled, unreachable, or denied.
    """

    def __init__(self, values: dict[str, str] | None = None) -> None:
        self.values = dict(values or {})
        self.error: BaseException | None = None
        self.calls = 0

    def get_parameter(self, Name: str) -> dict[str, Any]:
        self.calls += 1
        if self.error is not None:
            raise self.error
        if Name not in self.values:
            raise ClientError(
                {"Error": {"Code": "ParameterNotFound", "Message": Name}},
                "GetParameter",
            )
        return {"Parameter": {"Name": Name, "Value": self.values[Name]}}
