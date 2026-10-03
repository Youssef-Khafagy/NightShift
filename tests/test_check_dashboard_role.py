"""The dashboard role checker: it must fail loudly on a wrong decision and on
a trust policy that admits anything but this project's production."""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location(
    "check_dashboard_role", REPO_ROOT / "scripts" / "check_dashboard_role.py"
)
assert spec and spec.loader
check = importlib.util.module_from_spec(spec)
# Dataclasses look their module up by name, so it must be registered first.
sys.modules["check_dashboard_role"] = check
spec.loader.exec_module(check)

TEAM, PROJECT = "team-x", "proj-y"
ACCOUNT = "123456789012"


def good_trust() -> dict:
    host = f"oidc.vercel.com/{TEAM}"
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {
                    "Federated": f"arn:aws:iam::{ACCOUNT}:oidc-provider/{host}"
                },
                "Action": "sts:AssumeRoleWithWebIdentity",
                "Condition": {
                    "StringEquals": {
                        f"{host}:aud": f"https://vercel.com/{TEAM}",
                        f"{host}:sub": f"owner:{TEAM}:project:{PROJECT}:environment:production",
                    }
                },
            }
        ],
    }


def test_the_production_only_trust_policy_passes():
    assert check.trust_problems(good_trust(), TEAM, PROJECT) == []


def test_a_trust_policy_that_admits_previews_fails():
    doc = good_trust()
    s = doc["Statement"][0]
    s["Condition"]["StringLike"] = {
        f"oidc.vercel.com/{TEAM}:sub": f"owner:{TEAM}:project:*:environment:*"
    }
    del s["Condition"]["StringEquals"][f"oidc.vercel.com/{TEAM}:sub"]
    assert check.trust_problems(doc, TEAM, PROJECT)


def test_a_second_statement_or_another_issuer_fails():
    doc = good_trust()
    doc["Statement"].append(dict(doc["Statement"][0]))
    assert check.trust_problems(doc, TEAM, PROJECT)
    doc = good_trust()
    doc["Statement"][0]["Principal"]["Federated"] = (
        f"arn:aws:iam::{ACCOUNT}:oidc-provider/token.actions.githubusercontent.com"
    )
    assert check.trust_problems(doc, TEAM, PROJECT)


def test_every_case_is_reported_and_a_wrong_decision_is_a_problem(capsys):
    """A simulator that allows everything must produce one problem per
    case that should be denied, and print no account ID."""
    wrong = check.simulate(None, ACCOUNT, lambda iam, case, resource: "allowed")
    denied = [c for c in check.CASES if not c.allowed]
    assert len(wrong) == len(denied)
    out = capsys.readouterr().out
    assert ACCOUNT not in out
    assert out.count("\n") == len(check.CASES)


def test_the_expected_decisions_pass():
    def as_expected(iam, case, resource):
        return "allowed" if case.allowed else "implicitDeny"

    assert check.simulate(None, ACCOUNT, as_expected) == []


def test_the_rejection_attributes_match_what_reject_writes():
    """If agent/approvals.py's reject starts writing another attribute, the
    role must allow it too, or rejecting fails in production. Read the names
    from reject's own UpdateExpression ("#s" is its alias for status)."""
    source = (REPO_ROOT / "agent" / "approvals.py").read_text()
    body = source[source.index("def reject(") :]
    expression = re.search(r'UpdateExpression="SET ([^"]+)"', body)
    assert expression
    written = {
        "status" if name == "#s" else name
        for name in re.findall(r"([#\w]+) = :", expression.group(1))
    }
    assert written | {"investigation_id", "item"} == set(check.REJECTION)
    tf = (REPO_ROOT / "terraform" / "dashboard.tf").read_text()
    for name in check.REJECTION:
        assert f'"{name}"' in tf
