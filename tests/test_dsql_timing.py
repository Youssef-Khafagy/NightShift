"""A new DSQL connection logs how long it took, split into its two parts.

A new execution environment's first request takes 1.9 to 3.0 s against 0.3 s
warm, and the connection is the prime suspect. These lines are how that gets
measured in production instead of guessed.
"""

from __future__ import annotations

import psycopg

from common import dsql


class RecordingLogger:
    def __init__(self):
        self.lines = []

    def info(self, message, extra=None):
        self.lines.append((message, dict(extra or {})))


def test_a_new_connection_logs_token_and_connect_time(monkeypatch):
    log = RecordingLogger()
    monkeypatch.setattr(dsql, "logger", log)
    monkeypatch.setattr(dsql, "_shared", None)
    monkeypatch.setattr(dsql, "auth_token", lambda *a, **k: "token")

    class Conn:
        closed = False

    monkeypatch.setattr(psycopg, "connect", lambda **kwargs: Conn())

    dsql.shared_connection(role="orders_service")

    assert [message for message, _ in log.lines] == ["database connected"]
    timings = log.lines[0][1]
    assert set(timings) == {"token_ms", "connect_ms"}
    assert all(value >= 0 for value in timings.values())


def test_a_reused_connection_logs_nothing(monkeypatch):
    log = RecordingLogger()
    monkeypatch.setattr(dsql, "logger", log)

    class Conn:
        closed = False

    monkeypatch.setattr(dsql, "_shared", Conn())
    dsql.shared_connection(role="orders_service")
    assert log.lines == []
