"""Every handler path must leave the connection with no transaction open.

This is a cost test, not a correctness test, which is why it did not exist
until a billing metric found the bug. Aurora DSQL charges compute by how long
a transaction stays open: one DPU per transaction-second, measured 2026-09-21.
A handler that returns while holding one open gets its execution environment
frozen by Lambda with the transaction still live on the server, and DSQL bills
it until the cap kills it, 315 seconds and 315 DPU later.

Nothing about that is visible in a response. Every leaked request in the
original incident returned the correct status code.
"""

from __future__ import annotations

import json
import uuid

import boto3
import psycopg
import pytest
from fakes import FakeConnection, production_autocommit
from moto import mock_aws
from psycopg.pq import TransactionStatus

REGION = "ca-central-1"

KEYBOARD = "11111111-1111-4111-8111-111111111111"
MOUSE = "22222222-2222-4222-8222-222222222222"

PRICED_CART = [
    {"product_id": KEYBOARD, "quantity": 2},
    {"product_id": MOUSE, "quantity": 1},
]
# (product_id, price_cents, quantity_in_stock)
CATALOGUE_ROWS = [(KEYBOARD, 12900, 100), (MOUSE, 4900, 100)]
EXPECTED_TOTAL = 2 * 12900 + 4900


def connection(rules=None, cls=FakeConnection):
    """A fake connection configured the way production configures a real one."""
    return cls(autocommit=production_autocommit(), rules=rules)


CHECKOUT_RULES = [
    ("FROM products p", CATALOGUE_ROWS),
    ("UPDATE inventory", 1),
    ("INSERT INTO orders", 1),
    ("INSERT INTO order_items", 1),
    ("INSERT INTO idempotency_keys", 1),
]


# --------------------------------------------------------------------------
# The harness has to be able to fail.
#
# Every test below asserts that a connection is idle. If the fake were idle
# unconditionally, all of them would pass against the broken code too. These
# two prove it models the real difference.
# --------------------------------------------------------------------------


def test_the_fake_reproduces_the_original_leak():
    """autocommit off plus one lone statement equals a transaction left open."""
    conn = FakeConnection(autocommit=False)
    with conn.cursor() as cur:
        cur.execute("SELECT order_id FROM idempotency_keys WHERE key = %s", ("k",))
    assert conn.info.transaction_status == TransactionStatus.INTRANS
    assert not conn.is_idle()


def test_the_fake_stays_idle_with_autocommit_on():
    conn = FakeConnection(autocommit=True)
    with conn.cursor() as cur:
        cur.execute("SELECT order_id FROM idempotency_keys WHERE key = %s", ("k",))
    assert conn.is_idle()


# --------------------------------------------------------------------------
# The connection helper's default is the actual fix, so it gets a test.
# --------------------------------------------------------------------------


def test_connect_defaults_to_autocommit(monkeypatch):
    from common import dsql

    captured = {}

    def fake_connect(**kwargs):
        captured.update(kwargs)
        return FakeConnection()

    monkeypatch.setattr(dsql, "auth_token", lambda *a, **k: "token")
    monkeypatch.setattr(psycopg, "connect", fake_connect)

    dsql.connect("cluster.example", REGION)
    assert captured["autocommit"] is True, (
        "autocommit must default to on. With it off, psycopg holds a "
        "transaction open from the first statement and DSQL bills for it."
    )


def test_connect_still_allows_autocommit_off(monkeypatch):
    """The migration runner needs the choice; it is the default that changed."""
    from common import dsql

    captured = {}
    monkeypatch.setattr(dsql, "auth_token", lambda *a, **k: "token")
    monkeypatch.setattr(
        psycopg, "connect", lambda **kw: captured.update(kw) or FakeConnection()
    )

    dsql.connect("cluster.example", REGION, autocommit=False)
    assert captured["autocommit"] is False


# --------------------------------------------------------------------------
# orders-service
# --------------------------------------------------------------------------


@pytest.fixture
def checkout_event():
    def build(**overrides):
        body = {"cart_id": str(uuid.uuid4()), "customer_id": "test-customer"}
        body.update(overrides)
        return {
            "rawPath": "/checkout",
            "requestContext": {"http": {"method": "POST"}},
            "headers": {
                "x-correlation-id": "test-correlation",
                "idempotency-key": str(uuid.uuid4()),
            },
            "body": json.dumps(body),
        }

    return build


@pytest.fixture
def orders_wired(orders_app, monkeypatch):
    """Point orders at a fake database and a moto queue, and hand both back."""

    def wire(conn):
        monkeypatch.setattr(orders_app.dsql, "shared_connection", lambda **kwargs: conn)
        monkeypatch.setattr(
            orders_app.service_client,
            "call",
            lambda *a, **k: {"cart": {"items": PRICED_CART}},
        )
        return conn

    with mock_aws():
        sqs = boto3.client("sqs", region_name=REGION)
        url = sqs.create_queue(QueueName="placed-orders-test")["QueueUrl"]
        monkeypatch.setattr(orders_app, "_sqs", sqs)
        monkeypatch.setattr(orders_app, "PLACED_ORDERS_QUEUE_URL", url)
        yield wire, sqs, url


def test_successful_checkout_leaves_no_transaction_open(
    orders_app, orders_wired, checkout_event, lambda_context
):
    wire, _, _ = orders_wired
    conn = wire(connection(CHECKOUT_RULES))

    response = orders_app.handler(checkout_event(), lambda_context)

    assert response["statusCode"] == 201
    assert json.loads(response["body"])["total_cents"] == EXPECTED_TOTAL
    assert conn.is_idle()


def test_checkout_is_one_transaction(
    orders_app, orders_wired, checkout_event, lambda_context
):
    """Autocommit must not have cost checkout its atomicity."""
    wire, _, _ = orders_wired
    conn = wire(connection(CHECKOUT_RULES))

    orders_app.handler(checkout_event(), lambda_context)

    assert conn.events == ["begin", "commit"]
    inside = conn.statements
    assert any("FROM products p" in s for s in inside)
    assert any("UPDATE inventory" in s for s in inside)
    assert any("INSERT INTO orders" in s for s in inside)
    assert any("INSERT INTO idempotency_keys" in s for s in inside)


def test_replayed_idempotency_key_leaves_no_transaction_open(
    orders_app, orders_wired, checkout_event, lambda_context
):
    """The exact path that leaked: the replay lookup after a failed insert.

    The duplicate insert rolls the checkout back, then the service looks up
    the original order. That lookup used to be left open, which is what cost
    315 DPU every time the environment froze afterwards.
    """
    wire, _, _ = orders_wired
    original = str(uuid.uuid4())
    conn = wire(
        connection(
            [
                ("FROM products p", CATALOGUE_ROWS),
                ("UPDATE inventory", 1),
                ("INSERT INTO orders", 1),
                ("INSERT INTO order_items", 1),
                ("INSERT INTO idempotency_keys", psycopg.errors.UniqueViolation()),
                ("SELECT order_id FROM idempotency_keys", [(original,)]),
            ]
        )
    )

    response = orders_app.handler(checkout_event(), lambda_context)

    assert response["statusCode"] == 200
    assert json.loads(response["body"])["order_id"] == original
    assert conn.is_idle()
    assert conn.events == ["begin", "rollback"]


def test_rejected_checkout_leaves_no_transaction_open(
    orders_app, orders_wired, checkout_event, lambda_context
):
    """Out of stock: the transaction rolls back and nothing stays open."""
    wire, _, _ = orders_wired
    conn = wire(
        connection([("FROM products p", [(KEYBOARD, 12900, 0), (MOUSE, 4900, 0)])])
    )

    response = orders_app.handler(checkout_event(), lambda_context)

    assert response["statusCode"] == 409
    assert conn.is_idle()
    assert conn.events == ["begin", "rollback"]


def test_retried_checkout_leaves_no_transaction_open(
    orders_app, orders_wired, checkout_event, lambda_context
):
    """A serialization conflict is retried, and neither attempt leaks.

    DSQL's optimistic concurrency makes this the normal path under contention,
    not an edge case, so a leak here would be billed once per attempt.
    """
    wire, _, _ = orders_wired
    attempts = {"n": 0}

    class ConflictOnce(FakeConnection):
        def resolve(self, sql):
            if "INSERT INTO orders" in " ".join(sql.split()):
                attempts["n"] += 1
                if attempts["n"] == 1:
                    return psycopg.errors.SerializationFailure()
            return super().resolve(sql)

    conn = wire(connection(CHECKOUT_RULES, cls=ConflictOnce))

    response = orders_app.handler(checkout_event(), lambda_context)

    assert response["statusCode"] == 201
    assert attempts["n"] == 2, "expected one conflict and one successful retry"
    assert conn.is_idle()
    assert conn.events == ["begin", "rollback", "begin", "commit"]


def test_checkout_publishes_to_the_queue_after_committing(
    orders_app, orders_wired, checkout_event, lambda_context
):
    """Publishing happens after the commit, and outside any transaction."""
    wire, sqs, url = orders_wired
    conn = wire(connection(CHECKOUT_RULES))

    orders_app.handler(checkout_event(), lambda_context)

    received = sqs.receive_message(
        QueueUrl=url, MessageAttributeNames=["All"], MaxNumberOfMessages=1
    )
    message = received["Messages"][0]
    assert "order_id" in json.loads(message["Body"])
    attribute = message["MessageAttributes"]["correlationId"]["StringValue"]
    assert attribute == "test-correlation"
    assert conn.is_idle()


# --------------------------------------------------------------------------
# fulfillment-worker
# --------------------------------------------------------------------------


def sqs_event(order_id: str = "order-1", message_id: str = "m1") -> dict:
    return {
        "Records": [
            {
                "messageId": message_id,
                "body": json.dumps({"order_id": order_id}),
                "messageAttributes": {
                    "correlationId": {"stringValue": "test-correlation"}
                },
            }
        ]
    }


@pytest.fixture
def fulfillment_wired(fulfillment_app, monkeypatch):
    """Point fulfillment at a fake database and a recorded payment provider."""

    def wire(conn, payment=None):
        calls = []

        def fake_call(*args, **kwargs):
            # The whole point of the fixture: capture the transaction state at
            # the moment the payment provider is invoked, not afterwards.
            calls.append(conn.info.transaction_status)
            return payment if payment is not None else {"payment_id": "pay-1"}

        monkeypatch.setattr(
            fulfillment_app.dsql, "shared_connection", lambda **kwargs: conn
        )
        monkeypatch.setattr(fulfillment_app.service_client, "call", fake_call)
        return calls

    return wire


def test_missing_order_leaves_no_transaction_open(
    fulfillment_app, fulfillment_wired, lambda_context
):
    """An order that does not exist returns early. It used to return holding
    the lookup open."""
    conn = connection([("SELECT total_cents, status FROM orders", [])])
    fulfillment_wired(conn)

    result = fulfillment_app.handler(sqs_event(), lambda_context)

    assert result == {"batchItemFailures": []}
    assert conn.is_idle()


def test_settled_order_leaves_no_transaction_open(
    fulfillment_app, fulfillment_wired, lambda_context
):
    """A redelivery of an order already paid also returned early."""
    conn = connection([("SELECT total_cents, status FROM orders", [(30700, "paid")])])
    fulfillment_wired(conn)

    result = fulfillment_app.handler(sqs_event(), lambda_context)

    assert result == {"batchItemFailures": []}
    assert conn.is_idle()


def test_no_transaction_is_open_while_the_payment_provider_is_called(
    fulfillment_app, fulfillment_wired, lambda_context
):
    """The most expensive version of the bug.

    The lookup used to stay open across the call to the payment provider, so
    DSQL billed the provider's latency as database compute. One chaos scenario
    makes that provider deliberately slow, which would have turned a latency
    incident into a cost incident for reasons having nothing to do with
    queries.
    """
    conn = connection(
        [
            ("SELECT total_cents, status FROM orders", [(30700, "placed")]),
            ("UPDATE orders SET status = 'paid'", 1),
        ]
    )
    calls = fulfillment_wired(conn)

    fulfillment_app.handler(sqs_event(), lambda_context)

    assert calls == [TransactionStatus.IDLE], (
        "a transaction was open while waiting on the payment provider, "
        "so its latency is being billed as DSQL compute time"
    )


def test_successful_fulfillment_leaves_no_transaction_open(
    fulfillment_app, fulfillment_wired, lambda_context
):
    conn = connection(
        [
            ("SELECT total_cents, status FROM orders", [(30700, "placed")]),
            ("UPDATE orders SET status = 'paid'", 1),
        ]
    )
    fulfillment_wired(conn)

    result = fulfillment_app.handler(sqs_event(), lambda_context)

    assert result == {"batchItemFailures": []}
    assert conn.is_idle()


def test_failed_message_leaves_no_transaction_open(
    fulfillment_app, fulfillment_wired, lambda_context
):
    """A message that fails is reported for redelivery, not left holding a
    transaction. A poison message is one of the chaos scenarios, so this path
    runs repeatedly by design."""
    conn = connection([("SELECT total_cents, status FROM orders", [(30700, "placed")])])

    def explode(*args, **kwargs):
        raise RuntimeError("payment provider is down")

    fulfillment_wired(conn)
    from common import service_client

    service_client.call = explode

    result = fulfillment_app.handler(sqs_event(), lambda_context)

    assert result == {"batchItemFailures": [{"itemIdentifier": "m1"}]}
    assert conn.is_idle()
