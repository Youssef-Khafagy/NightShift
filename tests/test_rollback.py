"""Rollback: which version it picks, and every case where it refuses to guess.

The refusals matter most, because from M6 the agent can call this. A rollback
that quietly picks the wrong version is worse than one that stops and asks.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
from test_deploy import CURRENT, FakeLambda, rows, table  # noqa: F401

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

from ops import deployments


@pytest.fixture(scope="module")
def rb():
    spec = importlib.util.spec_from_file_location(
        "rollback_script", SCRIPTS / "rollback.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def row(kind, previous, new):
    return {"kind": kind, "previous": previous, "new": new}


def test_undoes_the_last_deploy(rb):
    assert rb.choose_target("17", [row("deploy", "16", "17")], None) == "16"


def test_explicit_to_wins(rb):
    assert rb.choose_target("17", [row("deploy", "16", "17")], "12") == "12"


@pytest.mark.parametrize(
    ("current", "rows_", "to", "fragment"),
    [
        ("17", [], None, "no recorded moves"),
        ("17", [row("deploy", "15", "16")], None, "moved outside the recorded path"),
        ("16", [row("rollback", "17", "16")], None, "already a rollback"),
        ("16", [row("auto-rollback", "17", "16")], None, "already a auto-rollback"),
        ("17", [row("deploy", "16", "17")], "17", "already on version 17"),
    ],
)
def test_refuses_to_guess(rb, current, rows_, to, fragment):
    with pytest.raises(rb.Refused, match=fragment):
        rb.choose_target(current, rows_, to)


def test_a_rollback_moves_records_and_smoke_tests(rb, table):  # noqa: F811
    deployments.record(
        table,
        service="orders",
        previous="16",
        new="17",
        kind="deploy",
        actor="ci",
        at="2026-09-23T00:00:00.000+00:00",
    )
    lam = FakeLambda({**CURRENT, "orders": "17"})
    smoked = []
    code = rb.rollback(
        lam,
        table,
        "orders",
        to=None,
        reason="5xx after deploy",
        actor="youssef",
        version_exists=lambda s, v: True,
        smoke=lambda: smoked.append(1) or True,
    )
    assert code == 0
    assert lam.versions["orders"] == "16"
    assert smoked == [1]
    last = rows(table)[-1]
    assert (last["kind"], last["previous"], last["new"], last["reason"]) == (
        "rollback",
        "17",
        "16",
        "5xx after deploy",
    )


def test_a_failed_smoke_test_reports_but_moves_nothing_again(rb, table):  # noqa: F811
    deployments.record(
        table,
        service="orders",
        previous="16",
        new="17",
        kind="deploy",
        actor="ci",
        at="2026-09-23T00:00:00.000+00:00",
    )
    lam = FakeLambda({**CURRENT, "orders": "17"})
    code = rb.rollback(
        lam,
        table,
        "orders",
        to=None,
        reason="r",
        actor="y",
        version_exists=lambda s, v: True,
        smoke=lambda: False,
    )
    assert code == 1
    assert lam.versions["orders"] == "16"
    assert [r["kind"] for r in rows(table)] == ["deploy", "rollback"]


def test_a_version_that_does_not_exist_is_refused_before_moving(rb, table):  # noqa: F811
    lam = FakeLambda({**CURRENT, "orders": "17"})
    with pytest.raises(rb.Refused, match="no published version 99"):
        rb.rollback(
            lam,
            table,
            "orders",
            to="99",
            reason="r",
            actor="y",
            version_exists=lambda s, v: False,
            smoke=lambda: True,
        )
    assert lam.versions["orders"] == "17"
    assert rows(table) == []
