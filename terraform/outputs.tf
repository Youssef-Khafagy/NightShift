output "hello_function_name" {
  description = "Name of the M1 smoke-test function."
  value       = module.hello.function_name
}

output "hello_alias_arn" {
  description = "ARN of the live alias. Invoke this, never $LATEST."
  value       = module.hello.alias_arn
}

output "hello_published_version" {
  description = "Version the live alias points at."
  value       = module.hello.published_version
}

output "hello_function_url" {
  description = "Function URL for the live alias. Requires a SigV4-signed request."
  value       = module.hello.function_url
}

output "hello_log_group" {
  description = "CloudWatch log group for the smoke-test function."
  value       = module.hello.log_group_name
}

output "ci_plan_role_arn" {
  description = "Role GitHub Actions assumes to run terraform plan."
  value       = aws_iam_role.ci_plan.arn
}

output "ci_apply_role_arn" {
  description = "Role GitHub Actions assumes to run terraform apply."
  value       = aws_iam_role.ci_apply.arn
}
