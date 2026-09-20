#!/usr/bin/env bash
#
# Creates the S3 bucket that holds Terraform remote state.
#
# Why this is a script and not Terraform: Terraform cannot create the bucket
# that stores its own state. The backend has to exist before `terraform init`
# runs. Keeping the bucket outside Terraform also means `terraform destroy`
# can never delete the state it is writing to.
#
# Run it once. It is safe to re-run: every step is idempotent.
#
#   ./bootstrap/state/create-state-bucket.sh            # show what it would do
#   ./bootstrap/state/create-state-bucket.sh --apply    # actually do it
#
set -euo pipefail

BUCKET="nightshift-tfstate-ca-central-1-2f0ad894"
REGION="ca-central-1"
PROFILE="nightshift-admin"
APPLY=0
[[ "${1:-}" == "--apply" ]] && APPLY=1

run() {
  if [[ $APPLY -eq 1 ]]; then
    echo "+ $*"
    "$@"
  else
    echo "would run: $*"
  fi
}

aws_cli() { run aws --profile "$PROFILE" --region "$REGION" "$@"; }

echo "Bucket:  $BUCKET"
echo "Region:  $REGION"
echo "Profile: $PROFILE"
echo "Mode:    $([[ $APPLY -eq 1 ]] && echo APPLY || echo DRY RUN)"
echo

# 1. The bucket. ca-central-1 is not us-east-1, so it needs a LocationConstraint.
if aws --profile "$PROFILE" --region "$REGION" s3api head-bucket --bucket "$BUCKET" 2>/dev/null; then
  echo "bucket already exists, skipping create"
else
  aws_cli s3api create-bucket \
    --bucket "$BUCKET" \
    --create-bucket-configuration "LocationConstraint=$REGION"
fi

# 2. Block all public access. State files describe the whole account.
aws_cli s3api put-public-access-block \
  --bucket "$BUCKET" \
  --public-access-block-configuration \
  "BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true"

# 3. Versioning. This is the undo button: a corrupted or truncated state file
#    can be restored from the previous version.
aws_cli s3api put-bucket-versioning \
  --bucket "$BUCKET" \
  --versioning-configuration "Status=Enabled"

# 4. Server-side encryption with S3-managed keys (SSE-S3, free). A customer
#    managed KMS key would cost money and is on the forbidden list.
aws_cli s3api put-bucket-encryption \
  --bucket "$BUCKET" \
  --server-side-encryption-configuration \
  '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}'

# 5. Ownership controls. Disables ACLs entirely, so access is decided only by
#    IAM and the bucket policy.
aws_cli s3api put-bucket-ownership-controls \
  --bucket "$BUCKET" \
  --ownership-controls '{"Rules":[{"ObjectOwnership":"BucketOwnerEnforced"}]}'

# 6. Lifecycle. Old state versions are kept for 30 days, which is long enough
#    to recover from a bad apply, then deleted so storage cost stays near zero.
#    Incomplete multipart uploads are billed until aborted.
aws_cli s3api put-bucket-lifecycle-configuration \
  --bucket "$BUCKET" \
  --lifecycle-configuration file://"$(dirname "$0")/lifecycle.json"

# 7. Bucket policy: refuse any request that did not arrive over TLS.
aws_cli s3api put-bucket-policy \
  --bucket "$BUCKET" \
  --policy file://"$(dirname "$0")/bucket-policy.json"

# 8. Tag it like everything else in the project.
aws_cli s3api put-bucket-tagging \
  --bucket "$BUCKET" \
  --tagging 'TagSet=[{Key=Project,Value=nightshift},{Key=ManagedBy,Value=bootstrap-script}]'

echo
if [[ $APPLY -eq 1 ]]; then
  echo "Done. Verify with:"
  echo "  aws s3api get-bucket-versioning --bucket $BUCKET --profile $PROFILE --region $REGION"
  echo "  aws s3api get-public-access-block --bucket $BUCKET --profile $PROFILE --region $REGION"
else
  echo "Dry run only. Re-run with --apply to create the bucket."
fi
