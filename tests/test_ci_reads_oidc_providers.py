"""Every OIDC provider Terraform manages must be readable by the CI apply role.

An apply refreshes every resource in state before planning, so a provider
the apply role cannot read fails every apply. That happened once: the
Vercel provider was applied locally in M8 step 8, and the next CI apply
stopped at GetOpenIDConnectProvider. The role may read providers but never
change them (DenyChangingOidcProvider), so a missing read is easy to miss.
"""

from __future__ import annotations

import re
from pathlib import Path

TERRAFORM = Path(__file__).resolve().parent.parent / "terraform"


def test_the_ci_apply_role_can_read_every_oidc_provider():
    providers = {
        name
        for tf in TERRAFORM.glob("*.tf")
        for name in re.findall(
            r'resource\s+"aws_iam_openid_connect_provider"\s+"(\w+)"', tf.read_text()
        )
    }
    assert providers, "no OIDC providers found; the pattern is stale"
    ci = (TERRAFORM / "ci_oidc.tf").read_text()
    block = re.search(r'sid\s*=\s*"ReadOidcProviders".*?\n  }', ci, re.DOTALL)
    assert block, "the ReadOidcProviders statement is missing"
    readable = set(
        re.findall(r"aws_iam_openid_connect_provider\.(\w+)\.arn", block.group(0))
    )
    assert providers == readable
