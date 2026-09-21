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

output "cart_service_url" {
  description = "Function URL for cart-service. SigV4-signed requests only."
  value       = module.cart.function_url
}

output "orders_service_url" {
  description = "Function URL for orders-service. SigV4-signed requests only."
  value       = module.orders.function_url
}

output "orders_execution_role_arn" {
  description = "Execution role for orders-service, mapped to the orders_service database role by scripts/grant_db_roles.py."
  value       = module.orders.execution_role_arn
}

output "payments_function_name" {
  description = "Mock payment provider. Invoked through the Lambda API, no function URL."
  value       = module.payments.function_name
}

output "fulfillment_function_name" {
  description = "SQS consumer that charges placed orders."
  value       = module.fulfillment.function_name
}

output "fulfillment_execution_role_arn" {
  description = "Execution role mapped to the fulfillment_service database role."
  value       = module.fulfillment.execution_role_arn
}
