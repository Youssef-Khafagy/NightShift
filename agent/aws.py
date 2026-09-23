"""AWS credentials for an investigation: always the Investigator role.

Locally, the owner's admin login assumes the role, so the agent never runs
with admin rights even on the laptop. In Lambda (step 6) the function's
execution role is the Investigator role, so the default session already is.
"""

from __future__ import annotations

import boto3

ROLE_NAME = "nightshift-investigator"
REGION = "ca-central-1"


def investigator_session(
    profile: str | None = None, region: str = REGION
) -> boto3.Session:
    base = boto3.Session(profile_name=profile, region_name=region)
    account = base.client("sts").get_caller_identity()["Account"]
    creds = base.client("sts").assume_role(
        RoleArn=f"arn:aws:iam::{account}:role/{ROLE_NAME}",
        RoleSessionName="nightshift-agent-local",
        DurationSeconds=3600,
    )["Credentials"]
    return boto3.Session(
        aws_access_key_id=creds["AccessKeyId"],
        aws_secret_access_key=creds["SecretAccessKey"],
        aws_session_token=creds["SessionToken"],
        region_name=region,
    )
