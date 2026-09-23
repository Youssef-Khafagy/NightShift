# Paging. CloudWatch alarms (M3 step 5) notify this topic, and the topic
# emails the owner. SNS gives 1M requests and 1,000 email notifications a
# month free, which is far above what alarms on this project send.
#
# An email subscription does nothing until someone clicks the confirmation
# link AWS sends. Terraform cannot click it, so after the first apply the
# subscription shows as pending until the owner confirms it.

variable "alert_email" {
  description = "Where alarms are emailed. Approved by the owner in M0."
  type        = string
  default     = "youssef.m.khafagy+nightshift@gmail.com"
}

# Not encrypted, deliberately. CloudWatch alarms cannot publish to a topic
# encrypted with the AWS managed key for SNS (aws/sns): that key's policy does
# not let CloudWatch use it and cannot be edited, so alarm actions fail
# silently. The only working option is a customer managed KMS key, which is
# $1 a month and on this project's forbidden list. The messages carry alarm
# names and metric values from synthetic data, nothing secret. The scanner
# finding for an unencrypted topic is suppressed here, for that reason only.
#trivy:ignore:AWS-0095
resource "aws_sns_topic" "alerts" {
  name = "${var.project}-alerts"
}

data "aws_iam_policy_document" "alerts_topic" {
  # The account itself manages the topic, which is what SNS's default policy
  # grants; stated explicitly because this document replaces the default.
  statement {
    sid    = "AccountManagesTopic"
    effect = "Allow"
    principals {
      type        = "AWS"
      identifiers = ["arn:aws:iam::${local.account_id}:root"]
    }
    actions = [
      "sns:GetTopicAttributes",
      "sns:SetTopicAttributes",
      "sns:AddPermission",
      "sns:RemovePermission",
      "sns:DeleteTopic",
      "sns:Subscribe",
      "sns:ListSubscriptionsByTopic",
      "sns:Publish",
    ]
    resources = [aws_sns_topic.alerts.arn]
  }

  # CloudWatch may publish, but only for alarms in this account whose names
  # start with the project prefix. Without the conditions, any account's
  # alarm could be pointed at this topic.
  statement {
    sid    = "ProjectAlarmsPublish"
    effect = "Allow"
    principals {
      type        = "Service"
      identifiers = ["cloudwatch.amazonaws.com"]
    }
    actions   = ["sns:Publish"]
    resources = [aws_sns_topic.alerts.arn]
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account_id]
    }
    condition {
      test     = "ArnLike"
      variable = "aws:SourceArn"
      values   = ["arn:aws:cloudwatch:${local.region}:${local.account_id}:alarm:${var.project}-*"]
    }
  }
}

resource "aws_sns_topic_policy" "alerts" {
  arn    = aws_sns_topic.alerts.arn
  policy = data.aws_iam_policy_document.alerts_topic.json
}

resource "aws_sns_topic_subscription" "owner_email" {
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email
}
