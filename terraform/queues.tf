# The placed-orders queue and its dead letter queue.
#
# Checkout writes the order and returns. Payment happens asynchronously, so a
# slow or failing payment provider degrades fulfilment rather than checkout.
# That split is what makes several chaos scenarios possible at all: a poison
# message, a retry storm, and a growing oldest-message age are only
# interesting when there is a queue between the two halves.

resource "aws_sqs_queue" "placed_orders_dlq" {
  name = "${var.project}-placed-orders-dlq"

  # SSE-SQS: encryption at rest with an AWS managed key, at no charge. The
  # alternative, SSE-KMS with a customer managed key, would cost per request
  # and is on the forbidden list. Flagged by the trivy config scan, which is
  # what that check is in CI for.
  sqs_managed_sse_enabled = true

  # 14 days, the maximum. A message only arrives here after repeated failures,
  # and the whole point is that a human or the agent can look at it later.
  message_retention_seconds = 1209600
}

resource "aws_sqs_queue" "placed_orders" {
  name = "${var.project}-placed-orders"

  # See the DLQ above: AWS managed encryption, free.
  sqs_managed_sse_enabled = true

  # AWS guidance is a visibility timeout of at least six times the consumer's
  # function timeout. The worker's timeout is 30 seconds. Too short and a
  # slow-but-succeeding message gets redelivered and processed twice.
  visibility_timeout_seconds = 180

  message_retention_seconds = 345600

  # After three failed receives the message moves to the DLQ instead of
  # cycling forever. Three is low enough that a poison message surfaces
  # quickly and high enough to ride out a transient error.
  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.placed_orders_dlq.arn
    maxReceiveCount     = 3
  })
}

# Only this one queue may use that DLQ. Without this, any queue in the account
# could redrive into it, and a DLQ with messages from an unknown source is
# worse than no DLQ.
resource "aws_sqs_queue_redrive_allow_policy" "placed_orders_dlq" {
  queue_url = aws_sqs_queue.placed_orders_dlq.id

  redrive_allow_policy = jsonencode({
    redrivePermission = "byQueue"
    sourceQueueArns   = [aws_sqs_queue.placed_orders.arn]
  })
}
