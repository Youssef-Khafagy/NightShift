output "function_name" {
  description = "Name of the function."
  value       = aws_lambda_function.this.function_name
}

output "function_arn" {
  description = "Unqualified function ARN."
  value       = aws_lambda_function.this.arn
}

output "alias_arn" {
  description = "ARN of the live alias. This is what callers invoke."
  value       = aws_lambda_alias.live.arn
}

output "published_version" {
  description = "Version number the live alias currently points at."
  value       = aws_lambda_alias.live.function_version
}

output "function_url" {
  description = "HTTPS endpoint for the live alias, or null if no URL was created."
  value       = try(aws_lambda_function_url.live[0].function_url, null)
}

output "log_group_name" {
  description = "CloudWatch log group for this function."
  value       = aws_cloudwatch_log_group.this.name
}

output "execution_role_arn" {
  description = "ARN of the function's execution role."
  value       = aws_iam_role.this.arn
}
