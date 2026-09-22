"""The traffic generator's arithmetic, refusals and pacing.

Everything that decides whether and how fast to send is a pure function or a
class with an injectable clock, so it is tested here without AWS and without
waiting. The refusals matter most: they are what keeps a typo in --rate or
--duration from spending a month's free allowance.
"""

from __future__ import annotations

import importlib.util
from collections import Counter
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "load.py"


@pytest.fixture(scope="module")
def load():
    spec = importlib.util.spec_from_file_location("load_script", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PLENTY = {
    pid: 10_000
    for pid in (
        "11111111-1111-4111-8111-111111111111",
        "22222222-2222-4222-8222-222222222222",
        "33333333-3333-4333-8333-333333333333",
        "44444444-4444-4444-8444-444444444444",
        "55555555-5555-4555-8555-555555555555",
    )
}


def ok_args(load, **overrides):
    """A request that should pass every check; override one thing to fail it."""
    args = {
        "rate": 2.0,
        "duration": 1200,
        "month_dpu": 100.0,
        "month_invocations": 1000.0,
        "stock": PLENTY,
        "needed": Counter(),
        "consumer_on": True,
        "checkout_only": False,
    }
    args.update(overrides)
    args["projected"] = load.projection(args["rate"], args["duration"])
    return args


# --------------------------------------------------------------------------
# Projection
# --------------------------------------------------------------------------


def test_the_planned_incident_projects_as_documented(load):
    """2/s for 20 minutes, the incident shape in COST.md."""
    p = load.projection(2, 1200)
    assert p["orders"] == 2400
    assert p["lambda_invocations"] == pytest.approx(11_280)
    assert p["sqs_requests"] == pytest.approx(2400 * 2.2 + 20 * 20)
    assert p["dsql_dpu"] == pytest.approx(600)
    assert p["log_mb"] == pytest.approx(14.06, abs=0.01)


# --------------------------------------------------------------------------
# Refusals
# --------------------------------------------------------------------------


def test_a_normal_incident_is_allowed(load):
    assert load.refusals(**ok_args(load)) == []


@pytest.mark.parametrize(
    ("overrides", "fragment"),
    [
        ({"rate": 50}, "rate 50/s"),
        ({"rate": 0}, "rate 0/s"),
        ({"duration": 7200}, "duration 7200s"),
        ({"rate": 5, "duration": 1800}, "exceeds 3600 per run"),
        ({"month_dpu": 49_800}, "DSQL"),
        ({"month_invocations": 495_000}, "Lambda"),
        ({"consumer_on": False}, "consumer is disabled"),
    ],
)
def test_each_limit_refuses(load, overrides, fragment):
    reasons = load.refusals(**ok_args(load, **overrides))
    assert any(fragment in r for r in reasons), reasons


def test_checkout_only_allows_a_disabled_consumer(load):
    assert load.refusals(**ok_args(load, consumer_on=False, checkout_only=True)) == []


def test_insufficient_stock_refuses_and_says_how_to_restock(load):
    keyboard = "11111111-1111-4111-8111-111111111111"
    reasons = load.refusals(
        **ok_args(
            load,
            stock={**PLENTY, keyboard: 100},
            needed=Counter({keyboard: 150}),
        )
    )
    assert reasons == [
        f"stock: {keyboard} needs 150 units, has 100",
        "restock with: scripts/seed_catalogue.py --apply --restock --quantity 1150",
    ]


def test_every_failed_limit_is_reported_not_just_the_first(load):
    reasons = load.refusals(**ok_args(load, rate=50, consumer_on=False))
    assert len(reasons) >= 2


# --------------------------------------------------------------------------
# Carts
# --------------------------------------------------------------------------


def test_carts_are_reproducible_from_the_seed(load):
    assert load.plan_carts(50, seed=7) == load.plan_carts(50, seed=7)
    assert load.plan_carts(50, seed=7) != load.plan_carts(50, seed=8)


def test_carts_hold_one_to_three_distinct_products(load):
    for cart in load.plan_carts(500, seed=1):
        ids = [item["product_id"] for item in cart]
        assert 1 <= len(ids) <= 3
        assert len(set(ids)) == len(ids)
        assert all(1 <= item["quantity"] <= 2 for item in cart)


def test_units_needed_adds_up(load):
    carts = [
        [{"product_id": "a", "quantity": 2}, {"product_id": "b", "quantity": 1}],
        [{"product_id": "a", "quantity": 1}],
    ]
    assert load.units_needed(carts) == Counter({"a": 3, "b": 1})


# --------------------------------------------------------------------------
# Pacing
# --------------------------------------------------------------------------


class FakeTime:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


def test_orders_go_out_on_schedule(load):
    t = FakeTime()
    sent_at = []

    def submit(i, done):
        sent_at.append(t.now)
        done()

    pacer = load.Pacer(rate=2, total=5, max_in_flight=8, clock=t.clock, sleep=t.sleep)
    pacer.run(submit)

    assert sent_at == [0.0, 0.5, 1.0, 1.5, 2.0]
    assert (pacer.sent, pacer.dropped) == (5, 0)


def test_slow_responses_do_not_slow_the_schedule(load):
    """Open loop: sends keep to the schedule while earlier orders are still out.

    In the real run, submit hands the order to a thread pool and returns at
    once, so here it returns without calling done(): every order is still in
    flight when the next one is due. A closed-loop generator would wait for a
    response before sending again and stall at the first one.
    """
    t = FakeTime()
    sent_at = []

    pacer = load.Pacer(rate=1, total=3, max_in_flight=8, clock=t.clock, sleep=t.sleep)
    pacer.run(lambda i, done: sent_at.append(t.now))

    assert sent_at == [0.0, 1.0, 2.0]
    assert (pacer.sent, pacer.dropped, pacer.in_flight) == (3, 0, 3)


def test_a_full_pipe_counts_drops_instead_of_queueing(load):
    """Requests that never finish: after the cap, every tick is a drop."""
    t = FakeTime()
    pacer = load.Pacer(rate=10, total=5, max_in_flight=2, clock=t.clock, sleep=t.sleep)
    pacer.run(lambda i, done: None)  # never calls done
    assert (pacer.sent, pacer.dropped) == (2, 3)


def test_percentile_nearest_rank(load):
    values = [float(v) for v in range(1, 101)]
    assert load.percentile(values, 50) == 50.0
    assert load.percentile(values, 99) == 99.0
    assert load.percentile([], 50) is None
