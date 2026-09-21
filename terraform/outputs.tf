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

output "dsql_cluster_identifier" {
  description = "Generated identifier of the Aurora DSQL cluster."
  value       = aws_dsql_cluster.main.identifier
}

output "dsql_endpoint" {
  description = "Hostname to connect to. Built from the cluster identifier; DSQL exposes no endpoint attribute."
  value       = local.dsql_endpoint
}

output "cart_table_name" {
  description = "DynamoDB table holding carts."
  value       = aws_dynamodb_table.cart.name
}

output "placed_orders_queue_url" {
  description = "Queue checkout writes to and the fulfilment worker reads."
  value       = aws_sqs_queue.placed_orders.url
}

output "placed_orders_dlq_url" {
  description = "Dead letter queue for messages that failed three times."
  value       = aws_sqs_queue.placed_orders_dlq.url
}
