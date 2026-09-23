"""AWS credentials for an investigation: always the Investigator role.

Locally, the owner's admin login assumes the role, so the agent never runs
with admin rights even on the laptop. In Lambda, the agent function's own
role assumes it the same way: that role holds the API keys and writes
checkpoints, and the tools never run with it.
"""

from __future__ import annotations

import boto3

ROLE_NAME = "nightshift-investigator"
REGION = "ca-central-1"


def assume_investigator(base: boto3.Session, session_name: str) -> boto3.Session:
    """Credentials for the Investigator role, from whatever `base` holds: the
    owner's login on the laptop, the agent function's own role in Lambda."""
    account = base.client("sts").get_caller_identity()["Account"]
    creds = base.client("sts").assume_role(
        RoleArn=f"arn:aws:iam::{account}:role/{ROLE_NAME}",
        RoleSessionName=session_name,
        DurationSeconds=3600,
    )["Credentials"]
    return boto3.Session(
        aws_access_key_id=creds["AccessKeyId"],
        aws_secret_access_key=creds["SecretAccessKey"],
        aws_session_token=creds["SessionToken"],
        region_name=base.region_name,
    )


def investigator_session(
    profile: str | None = None, region: str = REGION
) -> boto3.Session:
    return assume_investigator(
        boto3.Session(profile_name=profile, region_name=region),
        "nightshift-agent-local",
    )
