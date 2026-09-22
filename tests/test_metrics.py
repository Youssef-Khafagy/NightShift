"""Every metric the services emit must already be budgeted in COST.md.

A CloudWatch custom metric is billed per unique combination of namespace,
name and dimension values, and the free allowance is ten. Nothing about a
metric's cost shows up in a response or a log line: adding a dimension, or a
new name, quietly creates billed metrics the first time the code runs in AWS.

So this file is a cost test in the same sense as test_transaction_hygiene.py.
It drives each handler through every path that emits a metric, captures the
EMF documents printed to stdout, and checks them against the custom metric
ledger in COST.md. Adding a metric without a ledger row, or a dimension
beyond `service`, fails CI before it can cost anything.
"""

from __future__ import annotations

import json
import re
import uuid
from pathlib import Path

import boto3
import psycopg
import pytest
from fakes import FakeConnection, production_autocommit
from moto import mock_aws

REPO_ROOT = Path(__file__).resolve().parent.parent
REGION = "ca-central-1"

KEYBOARD = "11111111-1111-4111-8111-111111111111"
CART = [{"product_id": KEYBOARD, "quantity": 1}]
CHECKOUT_RULES = [
    ("FROM products p", [(KEYBOARD, 12900, 100)]),
    ("UPDATE inventory", 1),
    ("INSERT INTO orders", 1),
    ("INSERT INTO order_items", 1),
    ("INSERT INTO idempotency_keys", 1),
]


# --------------------------------------------------------------------------
# The ledger, read from COST.md
# --------------------------------------------------------------------------

LEDGER_ROW = re.compile(r"^\| `(\w+)` \| `(\w+)` \| `service=(\w+)` \|")


def ledger() -> set[tuple[str, str, str]]:
    """(namespace, metric name, service) for every row of the ledger table."""
    text = (REPO_ROOT / "COST.md").read_text()
    return {m.groups() for line in text.splitlines() if (m := LEDGER_ROW.match(line))}


def test_the_ledger_is_readable():
    """If the table's format changes, the parser must fail loudly, not match
    nothing and let every test below pass against an empty ledger."""
    rows = ledger()
    assert len(rows) == 5
    assert ("NightShift", "CheckoutsPlaced", "orders") in rows
    assert len(rows) <= 10, "the ledger itself is over the free allowance"


# --------------------------------------------------------------------------
# Capturing what the handlers emit
# --------------------------------------------------------------------------


@pytest.fixture
def emitted(capsys):
    """Read the EMF documents a handler printed, as (namespace, name, service).

    Also checks the one rule that has to hold for every document: its only
    dimension set is exactly ["service"].
    """

    def read() -> list[tuple[str, str, str]]:
        found = []
        for line in capsys.readouterr().out.splitlines():
            if not line.startswith("{"):
                continue
            doc = json.loads(line)
            if "_aws" not in doc:
                continue
            for directive in doc["_aws"]["CloudWatchMetrics"]:
                assert directive["Dimensions"] == [["service"]], (
                    f"dimension sets {directive['Dimensions']} would be billed "
                    "as separate metrics; `service` is the only dimension allowed"
                )
                for metric in directive["Metrics"]:
                    found.append(
                        (directive["Namespace"], metric["Name"], doc["service"])
                    )
        return found

    return read


def assert_budgeted(found):
    unbudgeted = set(found) - ledger()
    assert not unbudgeted, f"metrics not in the COST.md ledger: {unbudgeted}"
    assert not any(name == "ColdStart" for _, name, _ in found)


# --------------------------------------------------------------------------
# orders-service
# --------------------------------------------------------------------------


@pytest.fixture
def orders(orders_app, monkeypatch):
    """orders-service with a fake database, a fake cart and a moto queue."""
    monkeypatch.setattr(
        orders_app.service_client, "call", lambda *a, **k: {"cart": {"items": CART}}
    )
    with mock_aws():
        sqs = boto3.client("sqs", region_name=REGION)
        url = sqs.create_queue(QueueName="placed-orders-test")["QueueUrl"]
        monkeypatch.setattr(orders_app, "_sqs", sqs)
        monkeypatch.setattr(orders_app, "PLACED_ORDERS_QUEUE_URL", url)

        def use(conn):
            monkeypatch.setattr(
                orders_app.dsql, "shared_connection", lambda **kwargs: conn
            )

        yield orders_app, use


def checkout(key: str | None = None) -> dict:
    headers = {"x-correlation-id": "metrics-test"}
    if key is not None:
        headers["idempotency-key"] = key
    return {
        "headers": headers,
        "body": json.dumps({"cart_id": "c", "customer_id": "u"}),
    }


def test_a_placed_checkout(orders, emitted, lambda_context):
    app, use = orders
    use(FakeConnection(autocommit=production_autocommit(), rules=CHECKOUT_RULES))

    assert app.handler(checkout(str(uuid.uuid4())), lambda_context)["statusCode"] == 201

    found = emitted()
    assert found == [("NightShift", "CheckoutsPlaced", "orders")]
    assert_budgeted(found)


def test_a_rejected_checkout(orders, emitted, lambda_context):
    app, _ = orders
    assert app.handler(checkout(key=None), lambda_context)["statusCode"] == 400

    found = emitted()
    assert found == [("NightShift", "CheckoutsRejected", "orders")]
    assert_budgeted(found)


def test_a_rate_limited_checkout_counts_as_rejected(
    orders, fake_ssm, emitted, lambda_context, monkeypatch
):
    app, _ = orders
    from common.ratelimit import TokenBucket

    monkeypatch.setattr(app, "_bucket", TokenBucket(lambda: 0.0))
    fake_ssm.values["/nightshift/flags/checkout_rate_limit"] = "1"
    app.handler(checkout(key=None), lambda_context)
    emitted()

    assert app.handler(checkout(key=None), lambda_context)["statusCode"] == 429
    assert emitted() == [("NightShift", "CheckoutsRejected", "orders")]


def test_a_retried_checkout(orders, emitted, lambda_context):
    app, use = orders
    attempts = {"n": 0}

    class ConflictOnce(FakeConnection):
        def resolve(self, sql):
            if "INSERT INTO orders" in " ".join(sql.split()):
                attempts["n"] += 1
                if attempts["n"] == 1:
                    return psycopg.errors.SerializationFailure()
            return super().resolve(sql)

    use(ConflictOnce(autocommit=production_autocommit(), rules=CHECKOUT_RULES))
    assert app.handler(checkout(str(uuid.uuid4())), lambda_context)["statusCode"] == 201

    found = emitted()
    assert sorted(found) == [
        ("NightShift", "CheckoutsPlaced", "orders"),
        ("NightShift", "SerializationRetries", "orders"),
    ]
    assert_budgeted(found)


def test_a_replayed_checkout_is_not_a_new_placement(orders, emitted, lambda_context):
    app, use = orders
    existing = str(uuid.uuid4())
    use(
        FakeConnection(
            autocommit=production_autocommit(),
            rules=[
                *CHECKOUT_RULES[:-1],
                (
                    "INSERT INTO idempotency_keys",
                    psycopg.errors.UniqueViolation(),
                ),
                ("SELECT order_id FROM idempotency_keys", [(existing,)]),
            ],
        )
    )
    assert app.handler(checkout(str(uuid.uuid4())), lambda_context)["statusCode"] == 200
    assert emitted() == []


# --------------------------------------------------------------------------
# fulfillment-worker
# --------------------------------------------------------------------------


def one_message() -> dict:
    return {
        "Records": [
            {
                "messageId": "m1",
                "body": json.dumps({"order_id": "order-1"}),
                "messageAttributes": {},
            }
        ]
    }


@pytest.fixture
def fulfillment(fulfillment_app, monkeypatch):
    conn = FakeConnection(
        autocommit=production_autocommit(),
        rules=[
            ("SELECT total_cents, status FROM orders", [(12900, "placed")]),
            ("UPDATE orders SET status = 'paid'", 1),
        ],
    )
    monkeypatch.setattr(fulfillment_app.dsql, "shared_connection", lambda **k: conn)
    return fulfillment_app


def test_a_paid_order(fulfillment, emitted, lambda_context, monkeypatch):
    monkeypatch.setattr(
        fulfillment.service_client, "call", lambda *a, **k: {"payment_id": "p1"}
    )
    fulfillment.handler(one_message(), lambda_context)

    found = emitted()
    assert found == [("NightShift", "OrdersPaid", "fulfillment")]
    assert_budgeted(found)


def test_a_failed_payment(fulfillment, emitted, lambda_context, monkeypatch):
    def down(*args, **kwargs):
        raise TimeoutError("payment provider timed out")

    monkeypatch.setattr(fulfillment.service_client, "call", down)
    result = fulfillment.handler(one_message(), lambda_context)

    assert result == {"batchItemFailures": [{"itemIdentifier": "m1"}]}
    found = emitted()
    assert found == [("NightShift", "PaymentFailures", "fulfillment")]
    assert_budgeted(found)


def test_every_ledger_metric_is_emitted_somewhere():
    """The other direction: a ledger row nothing emits is a budget line for a
    metric that does not exist, which makes the ledger wrong in the safe
    direction but still wrong. Checked by name against the source."""
    source = "".join(
        (REPO_ROOT / "src" / name / "app.py").read_text()
        for name in ("orders", "fulfillment")
    )
    for _, name, _ in ledger():
        assert f'name="{name}"' in source, f"{name} is budgeted but never emitted"
