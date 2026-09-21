"""Test setup: fake credentials, fake configuration, and loading the services.

Two things need explaining.

**Credentials are fake and set before anything imports boto3.** This project's
first rule is that it costs nothing, and a unit test that quietly reached the
real account could create real resources. Setting obviously fake credentials
means a test that escapes its mock fails with an authentication error instead
of succeeding against production.

**Each service is loaded by path under its own module name.** Every function's
deployment package has its handler at `app.py` in the root of the zip, so all
four services are called `app`. Importing them normally would collide. This
mirrors what Lambda does: put the function's directory on the path, load its
`app`, and let `from common import ...` resolve to the shared code that the
build copies into every artifact.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
REGION = "ca-central-1"

sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

# A profile name would send botocore to the real login cache. Unset beats
# empty: botocore reads an empty AWS_PROFILE as a profile literally named "",
# then fails with ProfileNotFound.
os.environ.pop("AWS_PROFILE", None)

os.environ.update(
    {
        "AWS_DEFAULT_REGION": REGION,
        "AWS_REGION": REGION,
        "AWS_ACCESS_KEY_ID": "testing",
        "AWS_SECRET_ACCESS_KEY": "testing",
        "AWS_SESSION_TOKEN": "testing",
        "AWS_SECURITY_TOKEN": "testing",
        # Configuration the handlers read at import time.
        "CART_FUNCTION_NAME": "nightshift-cart:live",
        "PLACED_ORDERS_QUEUE_URL": "https://sqs.invalid/placeholder",
        "PAYMENTS_FUNCTION_NAME": "nightshift-payments:live",
        "DSQL_ENDPOINT": "cluster.dsql.ca-central-1.on.aws",
        # Powertools writes a JSON line per log call; keep the output readable.
        "POWERTOOLS_LOG_LEVEL": "CRITICAL",
    }
)


def load_service(name: str) -> ModuleType:
    """Load src/<name>/app.py as its own module, the way Lambda would."""
    module_name = f"{name}_app"
    if module_name in sys.modules:
        return sys.modules[module_name]

    path = REPO_ROOT / "src" / name / "app.py"
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def orders_app() -> ModuleType:
    return load_service("orders")


@pytest.fixture(scope="session")
def fulfillment_app() -> ModuleType:
    return load_service("fulfillment")


class FakeContext:
    """The bits of the Lambda context object the handlers actually read."""

    aws_request_id = "test-request-id"


@pytest.fixture
def lambda_context() -> FakeContext:
    return FakeContext()
