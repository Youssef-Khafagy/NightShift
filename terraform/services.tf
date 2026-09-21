# The store's services. Each is the same module with different code, IAM and
# environment, which is the point of having a module at all.

locals {
  # Built from values known at plan time rather than read from the cart
  # module's output. Referencing module.cart.function_arn here would make the
  # policy document unknown until apply, and a resource whose count depends on
  # an unknown value cannot be planned at all. Function names are deterministic,
  # so there is nothing to discover.
  cart_function_arn = "arn:aws:lambda:${local.region}:${local.account_id}:function:${var.project}-cart"
}

# ---------------------------------------------------------------------------
# cart-service
# ---------------------------------------------------------------------------

data "aws_iam_policy_document" "cart" {
  statement {
    sid    = "CartTable"
    effect = "Allow"
    actions = [
      "dynamodb:GetItem",
      "dynamodb:PutItem",
      "dynamodb:DeleteItem",
    ]
    resources = [aws_dynamodb_table.cart.arn]
  }
}

module "cart" {
  source = "./modules/lambda_service"

  name        = "${var.project}-cart"
  description = "Shopping carts, stored in DynamoDB."

  source_dir        = "${path.root}/../src/cart"
  shared_source_dir = "${path.root}/../src/common"

  layer_arns   = [aws_lambda_layer_version.deps.arn]
  tracing_mode = "Active"

  environment = {
    CART_TABLE_NAME = aws_dynamodb_table.cart.name
    # Powertools reads this for the `service` field on every log line.
    POWERTOOLS_SERVICE_NAME = "cart"
    POWERTOOLS_LOG_LEVEL    = "INFO"
  }

  extra_policy_json   = data.aws_iam_policy_document.cart.json
  log_retention_days  = var.log_retention_days
  create_function_url = true
}

# ---------------------------------------------------------------------------
# orders-service
# ---------------------------------------------------------------------------

data "aws_iam_policy_document" "orders" {
  # Connect to DSQL as the orders_service database role, not as admin. The
  # database role is created and mapped to this execution role by
  # scripts/grant_db_roles.py, which is what dsql:DbConnect authorises.
  statement {
    sid       = "ConnectToDsql"
    effect    = "Allow"
    actions   = ["dsql:DbConnect"]
    resources = [aws_dsql_cluster.main.arn]
  }

  statement {
    sid       = "PublishPlacedOrders"
    effect    = "Allow"
    actions   = ["sqs:SendMessage"]
    resources = [aws_sqs_queue.placed_orders.arn]
  }

  # Calling cart-service's function URL. Same-account callers can be
  # authorised by an identity policy alone, so cart-service needs no
  # resource policy. Both ARN forms are listed because the call goes to the
  # alias, and the unqualified form is what some SDK paths present.
  statement {
    sid       = "CallCartService"
    effect    = "Allow"
    actions   = ["lambda:InvokeFunctionUrl"]
    resources = [local.cart_function_arn, "${local.cart_function_arn}:*"]

    condition {
      test     = "StringEquals"
      variable = "lambda:FunctionUrlAuthType"
      values   = ["AWS_IAM"]
    }
  }
}

module "orders" {
  source = "./modules/lambda_service"

  name        = "${var.project}-orders"
  description = "Checkout: validate the cart, take stock, write the order."

  source_dir        = "${path.root}/../src/orders"
  shared_source_dir = "${path.root}/../src/common"

  layer_arns   = [aws_lambda_layer_version.deps.arn]
  tracing_mode = "Active"

  # Longer than the default 5s: a checkout is a call to cart-service plus a
  # database transaction that may be retried several times on conflict.
  timeout = 15

  environment = {
    DSQL_ENDPOINT           = local.dsql_endpoint
    DSQL_ROLE               = "orders_service"
    CART_SERVICE_URL        = module.cart.function_url
    PLACED_ORDERS_QUEUE_URL = aws_sqs_queue.placed_orders.url
    CART_TIMEOUT_SECONDS    = "2.0"
    POWERTOOLS_SERVICE_NAME = "orders"
    POWERTOOLS_LOG_LEVEL    = "INFO"
  }

  extra_policy_json   = data.aws_iam_policy_document.orders.json
  log_retention_days  = var.log_retention_days
  create_function_url = true
}
