terraform {
  backend "s3" {
    bucket = "nightshift-tfstate-ca-central-1-2f0ad894"
    key    = "nightshift/terraform.tfstate"
    region = "ca-central-1"

    # Encrypt the state object at rest. The bucket also has default
    # encryption, so this is belt and braces.
    encrypt = true

    # S3 native locking. Terraform writes a small <key>.tflock object next to
    # the state and relies on S3 conditional writes to make the lock atomic.
    # This replaced the old DynamoDB lock table, which is now deprecated.
    use_lockfile = true
  }
}
