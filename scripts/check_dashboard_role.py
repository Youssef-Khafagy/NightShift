"""Check the dashboard role with the IAM policy simulator, before and after apply.

    terraform -chdir=terraform plan -out=dashboard.tfplan
    python scripts/check_dashboard_role.py --plan terraform/dashboard.tfplan
    # ... apply ...
    python scripts/check_dashboard_role.py --live

the project notes's rule: never conclude a permission works, or is unnecessary,
from a request that happened to succeed or fail, because IAM caches
decisions in both directions. The simulator answers from the policies
themselves, immediately.

Before apply (--plan) it reads the rendered inline policy and permissions
boundary out of a saved plan and runs every case below through
SimulateCustomPolicy. After apply (--live) it reads the deployed role,
checks its trust policy admits only the production subject, and runs the
same cases through SimulatePrincipalPolicy against the real role.

Every call here is a free, read-only IAM or STS call. The account ID is used
in memory to build ARNs and is printed as <ACCOUNT_ID>.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
REGION = "ca-central-1"
PROJECT = "nightshift"
ROLE = f"{PROJECT}-dashboard"
VERCEL_TEAM = "youssef-khafagys-projects"
VERCEL_PROJECT = "night-shift"
STATE_BUCKET = "nightshift-tfstate-ca-central-1-2f0ad894"

# What a rejection touches (agent/approvals.py, reject), keys included.
REJECTION = ["investigation_id", "item", "status", "rejected_by", "rejected_at"]


@dataclass(frozen=True)
class Case:
    action: str
    resource: str  # with {a} for the account ID
    allowed: bool
    why: str
    context: dict[str, tuple[str, list[str]]] = field(default_factory=dict)


def update(attributes: list[str], returns: str = "NONE") -> dict:
    return {
        "dynamodb:Attributes": ("stringList", attributes),
        "dynamodb:ReturnValues": ("string", [returns]),
    }


TABLE = f"arn:aws:dynamodb:{REGION}:{{a}}:table/{PROJECT}-investigations"
FN = f"arn:aws:lambda:{REGION}:{{a}}:function:{PROJECT}"

CASES = [
    # What the live pages need.
    Case(
        "cloudwatch:DescribeAlarms",
        f"arn:aws:cloudwatch:{REGION}:{{a}}:alarm:*",
        True,
        "alarm states",
    ),
    Case("cloudwatch:GetMetricStatistics", "*", True, "health numbers"),
    Case("dynamodb:GetItem", TABLE, True, "incident lock, checkpoint, report"),
    Case("dynamodb:Query", TABLE, True, "one investigation's approval items"),
    Case("dynamodb:UpdateItem", TABLE, True, "reject an approval", update(REJECTION)),
    Case(
        "lambda:InvokeFunction",
        f"{FN}-actor:live",
        True,
        "approve: hand the Actor the hash",
    ),
    # A rejection cannot be stretched into any other write.
    Case(
        "dynamodb:UpdateItem",
        TABLE,
        False,
        "change an approval's action",
        update([*REJECTION, "action", "action_hash"]),
    ),
    Case(
        "dynamodb:UpdateItem",
        TABLE,
        False,
        "extend an approval's expiry",
        update([*REJECTION, "expires_at"]),
    ),
    Case(
        "dynamodb:UpdateItem",
        TABLE,
        False,
        "overwrite a checkpoint",
        update(["investigation_id", "item", "state"]),
    ),
    Case(
        "dynamodb:UpdateItem",
        TABLE,
        False,
        "read the whole item back",
        update(REJECTION, "ALL_OLD"),
    ),
    Case("dynamodb:Scan", TABLE, False, "scan the table (capacity)"),
    Case("dynamodb:PutItem", TABLE, False, "write a whole item"),
    Case("dynamodb:DeleteItem", TABLE, False, "delete anything"),
    Case(
        "dynamodb:GetItem",
        f"arn:aws:dynamodb:{REGION}:{{a}}:table/{PROJECT}-cart",
        False,
        "another table",
    ),
    Case(
        "dynamodb:UpdateItem",
        f"arn:aws:dynamodb:{REGION}:{{a}}:table/{PROJECT}-deployments",
        False,
        "the deployments record",
        update(REJECTION),
    ),
    # Only the Actor's live alias can be invoked.
    Case("lambda:InvokeFunction", f"{FN}-actor", False, "the Actor's $LATEST"),
    Case("lambda:InvokeFunction", f"{FN}-agent:live", False, "the agent"),
    Case("lambda:InvokeFunction", f"{FN}-orders:live", False, "a store service"),
    Case("lambda:UpdateAlias", f"{FN}-orders:live", False, "move an alias"),
    # Cost and everything else.
    Case("cloudwatch:GetMetricData", "*", False, "always billed"),
    Case(
        "cloudwatch:SetAlarmState",
        f"arn:aws:cloudwatch:{REGION}:{{a}}:alarm:{PROJECT}-orders-errors",
        False,
        "silence an alarm",
    ),
    Case(
        "ssm:GetParameter",
        f"arn:aws:ssm:{REGION}:{{a}}:parameter/{PROJECT}/secrets/gemini_api_key",
        False,
        "the LLM keys",
    ),
    Case("iam:CreateUser", "*", False, "IAM"),
    Case(
        "sts:AssumeRole",
        f"arn:aws:iam::{{a}}:role/{PROJECT}-investigator",
        False,
        "role chaining",
    ),
    Case(
        "s3:GetObject",
        f"arn:aws:s3:::{STATE_BUCKET}/nightshift/terraform.tfstate",
        False,
        "Terraform state",
    ),
    Case(
        "sqs:SendMessage",
        f"arn:aws:sqs:{REGION}:{{a}}:{PROJECT}-placed-orders",
        False,
        "the queue",
    ),
]


def context_entries(case: Case) -> list[dict[str, Any]]:
    return [
        {"ContextKeyName": key, "ContextKeyType": kind, "ContextKeyValues": values}
        for key, (kind, values) in case.context.items()
    ]


def simulate(iam: Any, account: str, run: Any) -> list[str]:
    """Run every case; return one line per case that did not go as expected.
    `run` makes one simulator call for one case and returns its decision."""
    wrong = []
    for case in CASES:
        resource = case.resource.format(a=account)
        decision = run(iam, case, resource)
        ok = (decision == "allowed") == case.allowed
        shown = resource.replace(account, "<ACCOUNT_ID>")
        mark = "ok   " if ok else "WRONG"
        expect = "allow" if case.allowed else "deny "
        print(
            f"{mark} expect {expect} got {decision:13} {case.action:32} {case.why} ({shown})"
        )
        if not ok:
            wrong.append(
                f"{case.action} on {shown}: expected {expect.strip()}, got {decision}"
            )
    return wrong


def trust_problems(document: dict[str, Any], team: str, project: str) -> list[str]:
    """The trust policy must admit one subject: this project's production."""
    host = f"oidc.vercel.com/{team}"
    expected = {
        f"{host}:aud": f"https://vercel.com/{team}",
        f"{host}:sub": f"owner:{team}:project:{project}:environment:production",
    }
    statements = document.get("Statement", [])
    if len(statements) != 1:
        return [f"expected one trust statement, found {len(statements)}"]
    s = statements[0]
    problems = []
    if s.get("Effect") != "Allow" or s.get("Action") != "sts:AssumeRoleWithWebIdentity":
        problems.append("the statement must allow only sts:AssumeRoleWithWebIdentity")
    federated = s.get("Principal", {}).get("Federated", "")
    if not str(federated).endswith(f":oidc-provider/{host}"):
        problems.append("the principal must be the Vercel team issuer")
    conditions = s.get("Condition", {})
    if set(conditions) != {"StringEquals"}:
        problems.append(
            f"only StringEquals conditions expected, found {sorted(conditions)}"
        )
    if conditions.get("StringEquals") != expected:
        problems.append(f"conditions must be exactly {expected}")
    return problems


def from_plan(plan: Path) -> tuple[str, str]:
    shown = subprocess.run(
        [
            "terraform",
            "-chdir=" + str(REPO_ROOT / "terraform"),
            "show",
            "-json",
            str(plan.resolve()),
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    resources = {
        r["address"]: r
        for r in json.loads(shown)["planned_values"]["root_module"]["resources"]
    }
    inline = resources["aws_iam_role_policy.dashboard"]["values"]["policy"]
    boundary = resources["aws_iam_policy.dashboard_boundary"]["values"]["policy"]
    return inline, boundary


def main() -> None:
    import boto3

    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--plan", type=Path, help="a saved plan file, before apply")
    mode.add_argument(
        "--live", action="store_true", help="the deployed role, after apply"
    )
    parser.add_argument("--profile", default="nightshift-admin")
    args = parser.parse_args()

    session = boto3.Session(profile_name=args.profile, region_name=REGION)
    iam = session.client("iam")
    account = session.client("sts").get_caller_identity()["Account"]
    problems: list[str] = []

    if args.plan:
        inline, boundary = from_plan(args.plan)

        def run(iam: Any, case: Case, resource: str) -> str:
            result = iam.simulate_custom_policy(
                PolicyInputList=[inline],
                PermissionsBoundaryPolicyInputList=[boundary],
                ActionNames=[case.action],
                ResourceArns=[resource],
                ContextEntries=context_entries(case),
            )
            return result["EvaluationResults"][0]["EvalDecision"]

    else:
        role = iam.get_role(RoleName=ROLE)["Role"]
        problems += trust_problems(
            role["AssumeRolePolicyDocument"], VERCEL_TEAM, VERCEL_PROJECT
        )
        if not role.get("PermissionsBoundary"):
            problems.append("the role has no permissions boundary")
        role_arn = role["Arn"]

        def run(iam: Any, case: Case, resource: str) -> str:
            result = iam.simulate_principal_policy(
                PolicySourceArn=role_arn,
                ActionNames=[case.action],
                ResourceArns=[resource],
                ContextEntries=context_entries(case),
            )
            return result["EvaluationResults"][0]["EvalDecision"]

    problems += simulate(iam, account, run)
    print()
    if problems:
        print(f"{len(problems)} problem(s):")
        for p in problems:
            print(f"  {p}")
        sys.exit(1)
    print(f"All {len(CASES)} cases as expected.")


if __name__ == "__main__":
    main()
