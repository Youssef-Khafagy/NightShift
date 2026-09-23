#!/usr/bin/env python3
"""Copy the LLM API keys from .env into SSM Parameter Store for the
investigator Lambda. Dry run by default.

    python scripts/put_llm_keys.py            # say what would be written
    python scripts/put_llm_keys.py --apply    # write them

Each key becomes a SecureString at /nightshift/secrets/<provider>_api_key,
encrypted with the AWS managed key (Standard tier and the managed key are
both free). The values go straight from .env to SSM: never printed, never
through Terraform, so they never land in Terraform state.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import boto3

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from agent.env import load_dotenv
from agent.llm.factory import KEY_VARIABLES

PREFIX = "/nightshift/secrets"


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--profile", default="nightshift-admin")
    args = parser.parse_args()
    load_dotenv(REPO_ROOT / ".env")

    ssm = boto3.Session(profile_name=args.profile, region_name="ca-central-1").client(
        "ssm"
    )
    missing = []
    for provider, variable in sorted(KEY_VARIABLES.items()):
        value = os.environ.get(variable, "").strip()
        name = f"{PREFIX}/{provider}_api_key"
        if not value:
            missing.append(variable)
            print(f"skip   {name}: {variable} is not set in .env")
            continue
        if not args.apply:
            print(f"WOULD  {name} ({len(value)} characters, SecureString)")
            continue
        ssm.put_parameter(
            Name=name,
            Value=value,
            Type="SecureString",
            Tier="Standard",
            Overwrite=True,
            Description=f"{provider} API key for the investigator Lambda",
        )
        print(f"wrote  {name}")
    if not args.apply:
        print("\nDry run. Pass --apply to write.")
    sys.exit(1 if missing else 0)


if __name__ == "__main__":
    main()
