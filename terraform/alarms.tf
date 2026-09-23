# CloudWatch alarms, each budgeted in COST.md's alarm ledger before it exists.
# tests/test_alarm_ledger.py checks that the names below and the ledger rows
# are the same set, so an alarm cannot be added without its budget line.
#
# 10 alarm metrics are free. Every alarm here watches exactly one metric
# (no metric math, which would bill each metric in the expression).
#
# Rules that apply to every alarm:
# - Named nightshift-*: the alerts topic only accepts CloudWatch publishes
#   from alarms with that prefix.
# - treat_missing_data = notBreaching: an idle store publishes no data at all,
#   and silence must not page anyone.
# - Notify on ALARM and on OK, so a recovery is an email too.
# - Thresholds are first estimates from the measured load runs; M4's
#   scenarios are where they get tuned.

locals {
  lambda_functions = {
    orders      = module.orders.function_name
    cart        = module.cart.function_name
    payments    = module.payments.function_name
    fulfillment = module.fulfillment.function_name
  }

  alarms = {
    # Invocations that raised or timed out. At this project's traffic any
    # error is unusual, so one in a minute pages.
    "orders-errors" = {
      namespace  = "AWS/Lambda", metric = "Errors", statistic = "Sum"
      dimensions = { FunctionName = local.lambda_functions.orders }
      period     = 60, evaluation_periods = 1, threshold = 1
      why        = "Checkout raising or timing out: bad deploy, IAM regression, timeout regression."
    }
    "cart-errors" = {
      namespace  = "AWS/Lambda", metric = "Errors", statistic = "Sum"
      dimensions = { FunctionName = local.lambda_functions.cart }
      period     = 60, evaluation_periods = 1, threshold = 1
      why        = "Cart failing: config regression (wrong table name), IAM regression. Also covers most of orders' 502 path, since a cart failure raises in cart first."
    }
    "payments-errors" = {
      namespace  = "AWS/Lambda", metric = "Errors", statistic = "Sum"
      dimensions = { FunctionName = local.lambda_functions.payments }
      period     = 60, evaluation_periods = 1, threshold = 1
      why        = "The payment provider failing."
    }
    "fulfillment-errors" = {
      namespace  = "AWS/Lambda", metric = "Errors", statistic = "Sum"
      dimensions = { FunctionName = local.lambda_functions.fulfillment }
      period     = 60, evaluation_periods = 1, threshold = 1
      why        = "Worker crashes and timeouts only. Per-message payment failures are reported as batchItemFailures and do not count as errors; payment-failures covers those."
    }
    # p99 over three consecutive minutes. One cold start (about 3 s) can
    # push a single minute's p99 over the line at low traffic; three in a
    # row is a slowdown, not a cold start.
    "checkout-latency" = {
      namespace  = "AWS/Lambda", metric = "Duration", statistic = "p99"
      dimensions = { FunctionName = local.lambda_functions.orders }
      period     = 60, evaluation_periods = 3, threshold = 2000
      why        = "Slow checkout: slow query from a dropped index, hot-row retries, slow dependency."
    }
    # Actions only while the consumer is enabled. While paused, a message
    # waiting in the queue is expected (every deploy's smoke test leaves one),
    # and paging on it would be noise. The alarm still records its state.
    "queue-age" = {
      namespace       = "AWS/SQS", metric = "ApproximateAgeOfOldestMessage", statistic = "Maximum"
      dimensions      = { QueueName = aws_sqs_queue.placed_orders.name }
      period          = 300, evaluation_periods = 1, threshold = 300
      actions_enabled = var.queue_consumer_enabled
      why             = "Orders not being fulfilled: slow payment provider, stuck consumer. Pages only while the consumer is enabled."
    }
    "dlq-depth" = {
      namespace  = "AWS/SQS", metric = "ApproximateNumberOfMessagesVisible", statistic = "Maximum"
      dimensions = { QueueName = aws_sqs_queue.placed_orders_dlq.name }
      period     = 300, evaluation_periods = 1, threshold = 1
      why        = "Any message in the DLQ: poison message."
    }
    # No dimensions: AWS/Lambda publishes an account-wide Throttles total.
    "throttles" = {
      namespace  = "AWS/Lambda", metric = "Throttles", statistic = "Sum"
      dimensions = {}
      period     = 60, evaluation_periods = 1, threshold = 1
      why        = "Any function throttled: throttling scenario, retry storm."
    }
    # One retry in 60 orders was seen in a normal load run; contention is
    # many per minute, sustained.
    "serialization-retries" = {
      namespace  = "NightShift", metric = "SerializationRetries", statistic = "Sum"
      dimensions = { service = "orders" }
      period     = 60, evaluation_periods = 2, threshold = 10
      why        = "Hot-row contention, visible before latency moves."
    }
    "payment-failures" = {
      namespace  = "NightShift", metric = "PaymentFailures", statistic = "Sum"
      dimensions = { service = "fulfillment" }
      period     = 60, evaluation_periods = 1, threshold = 3
      why        = "Payment calls failing or timing out inside the worker, which fulfillment-errors cannot see."
    }
  }
}

resource "aws_cloudwatch_metric_alarm" "this" {
  for_each = local.alarms

  alarm_name        = "${var.project}-${each.key}"
  alarm_description = each.value.why

  namespace           = each.value.namespace
  metric_name         = each.value.metric
  dimensions          = each.value.dimensions
  period              = each.value.period
  evaluation_periods  = each.value.evaluation_periods
  threshold           = each.value.threshold
  comparison_operator = "GreaterThanOrEqualToThreshold"

  # Plain statistics (Sum, Maximum) and percentiles use different arguments.
  statistic          = startswith(each.value.statistic, "p") ? null : each.value.statistic
  extended_statistic = startswith(each.value.statistic, "p") ? each.value.statistic : null

  treat_missing_data = "notBreaching"

  actions_enabled = lookup(each.value, "actions_enabled", true)
  alarm_actions   = [aws_sns_topic.alerts.arn]
  ok_actions      = [aws_sns_topic.alerts.arn]
}
