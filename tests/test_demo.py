"""scripts/demo.py: a live demo starts only when it is safe to, and stops
everything it started."""

from __future__ import annotations

import importlib.util
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))
spec = importlib.util.spec_from_file_location("demo", REPO_ROOT / "scripts" / "demo.py")
assert spec and spec.loader
demo = importlib.util.module_from_spec(spec)
sys.modules["demo"] = demo
spec.loader.exec_module(demo)


class Events:
    def __init__(self, state="DISABLED"):
        self.state = state

    def describe_rule(self, Name):
        return {"State": self.state}

    def enable_rule(self, Name):
        self.state = "ENABLED"

    def disable_rule(self, Name):
        self.state = "DISABLED"


class Lambda:
    def __init__(self):
        self.enabled = None

    def list_event_source_mappings(self, FunctionName):
        return {"EventSourceMappings": [{"UUID": "u", "State": "Disabled"}]}

    def update_event_source_mapping(self, UUID, Enabled):
        self.enabled = Enabled


class CloudWatch:
    def __init__(self):
        self.actions = None

    def enable_alarm_actions(self, AlarmNames):
        self.actions = True

    def disable_alarm_actions(self, AlarmNames):
        self.actions = False


class Session:
    def __init__(self, trigger="DISABLED"):
        self.clients = {
            "events": Events(trigger),
            "lambda": Lambda(),
            "cloudwatch": CloudWatch(),
            "cloudtrail": object(),
        }

    def client(self, name):
        return self.clients[name]


@pytest.fixture(autouse=True)
def no_marker(monkeypatch):
    """Never write the real quiet-gap marker from a test."""
    monkeypatch.setattr(demo.quiet, "touch", lambda reason: None)
    monkeypatch.setattr(
        demo.quiet, "last_write", lambda: datetime(2026, 10, 3, 12, 0, tzinfo=UTC)
    )


def test_the_demo_is_ready_45_minutes_after_the_last_write():
    last = datetime(2026, 10, 3, 12, 0, tzinfo=UTC)
    assert demo.ready_at(last) == last + timedelta(minutes=45)
    assert demo.ready_at(None) is None


def test_start_refuses_until_the_store_has_been_quiet(monkeypatch, capsys):
    monkeypatch.setattr(
        demo.quiet, "check", lambda cw, trail: ["3 project writes in the gap"]
    )
    ran = []
    monkeypatch.setattr(demo.subprocess, "run", lambda *a, **k: ran.append(a))
    assert demo.start(Session(trigger="ENABLED"), "p") == 1
    assert ran == []
    assert "12:45 UTC" in capsys.readouterr().out


def test_start_refuses_without_the_agent_trigger(monkeypatch):
    monkeypatch.setattr(demo.quiet, "check", lambda cw, trail: [])
    ran = []
    monkeypatch.setattr(demo.subprocess, "run", lambda *a, **k: ran.append(a))
    assert demo.start(Session(trigger="DISABLED"), "p") == 1
    assert ran == []


def test_prepare_and_stop_switch_both_the_consumer_and_the_trigger():
    session = Session()
    demo.prepare(session)
    assert session.clients["events"].state == "ENABLED"
    assert session.clients["lambda"].enabled is True
    assert session.clients["cloudwatch"].actions is True
    demo.stop(session)
    assert session.clients["events"].state == "DISABLED"
    assert session.clients["lambda"].enabled is False
    assert session.clients["cloudwatch"].actions is False
