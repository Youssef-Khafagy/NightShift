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

  # Its own flag and nothing else. Least privilege, and it also makes this
  # policy an exact record of which service reads which flag.
  statement {
    sid       = "ReadCheckoutRateLimit"
    effect    = "Allow"
    actions   = ["ssm:GetParameter"]
    resources = ["${local.flag_arn_prefix}/checkout_rate_limit"]
  }

  statement {
    sid       = "PublishPlacedOrders"
    effect    = "Allow"
    actions   = ["sqs:SendMessage"]
    resources = [aws_sqs_queue.placed_orders.arn]
  }

  # Invoking cart-service through the Lambda API rather than its function
  # URL. Both ARN forms are listed because the call targets the alias.
  statement {
    sid       = "CallCartService"
    effect    = "Allow"
    actions   = ["lambda:InvokeFunction"]
    resources = [local.cart_function_arn, "${local.cart_function_arn}:*"]
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
    DSQL_ENDPOINT                 = local.dsql_endpoint
    DSQL_ROLE                     = "orders_service"
    CART_FUNCTION_NAME            = module.cart.function_name
    PLACED_ORDERS_QUEUE_URL       = aws_sqs_queue.placed_orders.url
    CART_TIMEOUT_SECONDS          = "2.0"
    CHECKOUT_RATE_LIMIT_PARAMETER = aws_ssm_parameter.checkout_rate_limit.name
    POWERTOOLS_SERVICE_NAME       = "orders"
    POWERTOOLS_LOG_LEVEL          = "INFO"
  }

  extra_policy_json   = data.aws_iam_policy_document.orders.json
  log_retention_days  = var.log_retention_days
  create_function_url = true
}

# ---------------------------------------------------------------------------
# payment-provider
# ---------------------------------------------------------------------------

module "payments" {
  source = "./modules/lambda_service"

  name        = "${var.project}-payments"
  description = "Mock third-party payment API with configurable latency and errors."

  source_dir        = "${path.root}/../src/payments"
  shared_source_dir = "${path.root}/../src/common"

  layer_arns   = [aws_lambda_layer_version.deps.arn]
  tracing_mode = "Active"
  timeout      = 30

  environment = {
    PAYMENT_LATENCY_MS        = var.payment_latency_ms
    PAYMENT_LATENCY_JITTER_MS = var.payment_latency_jitter_ms
    PAYMENT_ERROR_RATE        = var.payment_error_rate
    POWERTOOLS_SERVICE_NAME   = "payments"
    POWERTOOLS_LOG_LEVEL      = "INFO"
  }

  log_retention_days = var.log_retention_days

  # No function URL. Nothing outside AWS calls this, and an unused public
  # door is still a door.
  create_function_url = false
}

# ---------------------------------------------------------------------------
# fulfillment-worker
# ---------------------------------------------------------------------------

locals {
  payments_function_arn = "arn:aws:lambda:${local.region}:${local.account_id}:function:${var.project}-payments"
}

data "aws_iam_policy_document" "fulfillment" {
  statement {
    sid       = "ConnectToDsql"
    effect    = "Allow"
    actions   = ["dsql:DbConnect"]
    resources = [aws_dsql_cluster.main.arn]
  }

  # The event source mapping polls on the function's behalf using these.
  statement {
    sid    = "ConsumePlacedOrders"
    effect = "Allow"
    actions = [
      "sqs:ReceiveMessage",
      "sqs:DeleteMessage",
      "sqs:GetQueueAttributes",
      "sqs:ChangeMessageVisibility",
    ]
    resources = [aws_sqs_queue.placed_orders.arn]
  }

  statement {
    sid       = "ReadPaymentsDegradedMode"
    effect    = "Allow"
    actions   = ["ssm:GetParameter"]
    resources = ["${local.flag_arn_prefix}/payments_degraded_mode"]
  }

  statement {
    sid       = "CallPaymentProvider"
    effect    = "Allow"
    actions   = ["lambda:InvokeFunction"]
    resources = [local.payments_function_arn, "${local.payments_function_arn}:*"]
  }
}

module "fulfillment" {
  source = "./modules/lambda_service"

  name        = "${var.project}-fulfillment"
  description = "Consumes placed orders, charges them, marks them paid."

  source_dir        = "${path.root}/../src/fulfillment"
  shared_source_dir = "${path.root}/../src/common"

  layer_arns   = [aws_lambda_layer_version.deps.arn]
  tracing_mode = "Active"

  # Must stay well under the queue's 180 second visibility timeout, which AWS
  # recommends be at least six times this.
  timeout = 30

  environment = {
    DSQL_ENDPOINT                    = local.dsql_endpoint
    DSQL_ROLE                        = "fulfillment_service"
    PAYMENTS_FUNCTION_NAME           = module.payments.function_name
    PAYMENT_TIMEOUT_SECONDS          = "3.0"
    PAYMENTS_DEGRADED_MODE_PARAMETER = aws_ssm_parameter.payments_degraded_mode.name
    POWERTOOLS_SERVICE_NAME          = "fulfillment"
    POWERTOOLS_LOG_LEVEL             = "INFO"
  }

  extra_policy_json   = data.aws_iam_policy_document.fulfillment.json
  log_retention_days  = var.log_retention_days
  create_function_url = false
}

# The trigger. Disabled by default, and that is the cost control: an enabled
# mapping long-polls with five connections around the clock, roughly 648,000
# SQS requests a month, which is about two thirds of the free allowance spent
# on an idle queue. It is turned on for a run and turned off afterwards.
resource "aws_lambda_event_source_mapping" "placed_orders" {
  event_source_arn = aws_sqs_queue.placed_orders.arn
  function_name    = module.fulfillment.alias_arn
  enabled          = var.queue_consumer_enabled

  batch_size = 10

  # Wait up to 5 seconds to fill a batch. Larger batches mean fewer Lambda
  # invocations and fewer SQS requests, both of which are metered.
  maximum_batching_window_in_seconds = 5

  # Without this, one failed message fails its whole batch and all ten are
  # redelivered, so nine successful messages get processed twice and every
  # one of them burns a delivery attempt against maxReceiveCount.
  function_response_types = ["ReportBatchItemFailures"]
}
