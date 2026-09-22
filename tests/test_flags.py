"""The flag cache, the rate limiter, and what each flag does to its service.

The cache tests drive a fake clock rather than sleeping, so "30 seconds later"
takes no time and cannot flake.
"""

from __future__ import annotations

import json

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError
from fakes import FakeSSM

from common import flags as flags_module
from common.flags import Flags
from common.ratelimit import TokenBucket

NAME = "/nightshift/flags/example"
RATE_LIMIT = "/nightshift/flags/checkout_rate_limit"
DEGRADED = "/nightshift/flags/payments_degraded_mode"


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def throttled() -> ClientError:
    return ClientError(
        {"Error": {"Code": "ThrottlingException", "Message": "Rate exceeded"}},
        "GetParameter",
    )


@pytest.fixture
def clock() -> Clock:
    return Clock()


# --------------------------------------------------------------------------
# The cache
# --------------------------------------------------------------------------


def test_reads_are_cached_for_the_ttl(clock):
    ssm = FakeSSM({NAME: "true"})
    f = Flags(ssm, clock=clock)

    assert f.get_bool(NAME, False) is True
    ssm.values[NAME] = "false"
    clock.now += 29.9
    assert f.get_bool(NAME, False) is True, "changed inside the TTL, still cached"
    assert ssm.calls == 1


def test_a_change_is_seen_once_the_ttl_expires(clock):
    ssm = FakeSSM({NAME: "true"})
    f = Flags(ssm, clock=clock)

    f.get_bool(NAME, False)
    ssm.values[NAME] = "false"
    clock.now += 30
    assert f.get_bool(NAME, True) is False
    assert ssm.calls == 2


def test_missing_parameter_uses_the_default(clock):
    f = Flags(FakeSSM(), clock=clock)
    assert f.get_bool(NAME, True) is True
    assert f.get_int(NAME, 7) == 7


def test_ssm_failure_with_no_history_uses_the_default(clock):
    ssm = FakeSSM({NAME: "5"})
    ssm.error = throttled()
    f = Flags(ssm, clock=clock)
    assert f.get_int(NAME, 0) == 0


def test_ssm_failure_keeps_the_last_good_value(clock):
    """A flag service outage must not silently undo an operator's change."""
    ssm = FakeSSM({NAME: "5"})
    f = Flags(ssm, clock=clock)
    assert f.get_int(NAME, 0) == 5

    ssm.error = throttled()
    clock.now += 31
    assert f.get_int(NAME, 0) == 5


def test_network_errors_are_handled_like_api_errors(clock):
    ssm = FakeSSM({NAME: "true"})
    ssm.error = EndpointConnectionError(endpoint_url="https://ssm.invalid")
    f = Flags(ssm, clock=clock)
    assert f.get_bool(NAME, False) is False


def test_a_failure_is_cached_too(clock):
    """Otherwise an SSM outage adds one failing call to every request."""
    ssm = FakeSSM({NAME: "true"})
    ssm.error = throttled()
    f = Flags(ssm, clock=clock)

    for _ in range(50):
        f.get_bool(NAME, False)
    assert ssm.calls == 1

    clock.now += 30
    f.get_bool(NAME, False)
    assert ssm.calls == 2


@pytest.mark.parametrize(
    ("value", "expected"),
    [("true", True), ("TRUE", True), (" 1 ", True), ("false", False), ("0", False)],
)
def test_boolean_spellings(clock, value, expected):
    f = Flags(FakeSSM({NAME: value}), clock=clock)
    assert f.get_bool(NAME, not expected) is expected


@pytest.mark.parametrize("value", ["banana", "", "-3", "2.5"])
def test_invalid_values_use_the_default(clock, value):
    f = Flags(FakeSSM({NAME: value}), clock=clock)
    assert f.get_int(NAME, 0) == 0
    if value not in ("-3", "2.5"):
        assert f.get_bool(NAME, False) is False


def test_the_real_client_has_short_timeouts():
    """boto3's defaults would let a slow SSM hold a checkout for a minute."""
    config = flags_module._ssm.meta.config
    assert config.connect_timeout == 1
    assert config.read_timeout == 1
    assert config.retries["total_max_attempts"] == 2


# --------------------------------------------------------------------------
# The token bucket
# --------------------------------------------------------------------------


def test_zero_means_no_limit(clock):
    bucket = TokenBucket(clock)
    assert all(bucket.allow(0) for _ in range(1000))


def test_allows_a_burst_of_rate_then_rejects(clock):
    bucket = TokenBucket(clock)
    assert [bucket.allow(3) for _ in range(4)] == [True, True, True, False]


def test_refills_at_rate_per_second(clock):
    bucket = TokenBucket(clock)
    for _ in range(2):
        bucket.allow(2)
    assert bucket.allow(2) is False

    clock.now += 0.5
    assert bucket.allow(2) is True
    assert bucket.allow(2) is False


def test_lowering_the_rate_takes_effect_immediately(clock):
    bucket = TokenBucket(clock)
    bucket.allow(10)  # full bucket of 10, one taken
    assert [bucket.allow(1) for _ in range(2)] == [True, False]


def test_never_accumulates_more_than_one_second_of_tokens(clock):
    bucket = TokenBucket(clock)
    bucket.allow(2)
    clock.now += 3600
    assert [bucket.allow(2) for _ in range(3)] == [True, True, False]


# --------------------------------------------------------------------------
# checkout_rate_limit in orders-service
# --------------------------------------------------------------------------


@pytest.fixture
def orders_limited(orders_app, monkeypatch, fake_ssm, clock):
    """orders-service with a fresh bucket and every downstream call forbidden.

    Requests are sent without an idempotency key, so one that gets past the
    limiter stops at the 400 immediately after it. That separates "shed by the
    limiter" (429) from "let through" (400) without a cart or a database.
    """
    monkeypatch.setattr(orders_app, "_bucket", TokenBucket(clock))

    def forbidden(*args, **kwargs):
        raise AssertionError("a request reached a downstream dependency")

    monkeypatch.setattr(orders_app.service_client, "call", forbidden)
    monkeypatch.setattr(orders_app.dsql, "shared_connection", forbidden)
    return orders_app


def checkout_event() -> dict:
    return {"headers": {}, "body": json.dumps({"cart_id": "c", "customer_id": "u"})}


def test_rate_limit_sheds_with_429_and_retry_after(
    orders_limited, fake_ssm, lambda_context
):
    fake_ssm.values[RATE_LIMIT] = "1"

    first = orders_limited.handler(checkout_event(), lambda_context)
    second = orders_limited.handler(checkout_event(), lambda_context)

    assert first["statusCode"] == 400
    assert second["statusCode"] == 429
    assert second["headers"]["retry-after"] == "1"
    assert second["headers"]["x-correlation-id"]


def test_no_limit_by_default(orders_limited, lambda_context):
    statuses = {
        orders_limited.handler(checkout_event(), lambda_context)["statusCode"]
        for _ in range(20)
    }
    assert statuses == {400}


def test_unreadable_flag_does_not_block_checkout(
    orders_limited, fake_ssm, lambda_context
):
    """Failing open: SSM being down must not become a checkout outage."""
    fake_ssm.values[RATE_LIMIT] = "1"
    fake_ssm.error = throttled()
    statuses = {
        orders_limited.handler(checkout_event(), lambda_context)["statusCode"]
        for _ in range(5)
    }
    assert statuses == {400}


# --------------------------------------------------------------------------
# payments_degraded_mode in fulfillment-worker
# --------------------------------------------------------------------------


def sqs_batch(n: int) -> dict:
    return {
        "Records": [
            {
                "messageId": f"m-{i}",
                "body": json.dumps({"order_id": f"order-{i}"}),
                "messageAttributes": {},
            }
            for i in range(n)
        ]
    }


def test_degraded_mode_defers_without_touching_anything(
    fulfillment_app, monkeypatch, fake_ssm, lambda_context
):
    """No payment call, no database, and every message acknowledged.

    Acknowledged is the important part. Reporting them as failures would
    redeliver them against the same sick provider and walk each one into the
    DLQ after three attempts.
    """
    fake_ssm.values[DEGRADED] = "true"

    def forbidden(*args, **kwargs):
        raise AssertionError("degraded mode reached a dependency")

    monkeypatch.setattr(fulfillment_app.service_client, "call", forbidden)
    monkeypatch.setattr(fulfillment_app.dsql, "shared_connection", forbidden)

    result = fulfillment_app.handler(sqs_batch(3), lambda_context)
    assert result == {"batchItemFailures": []}


def test_flag_is_read_once_per_batch(fulfillment_app, fake_ssm, lambda_context):
    fake_ssm.values[DEGRADED] = "true"
    fulfillment_app.handler(sqs_batch(10), lambda_context)
    assert fake_ssm.calls == 1
