provider "aws" {
  region = var.region

  default_tags {
    tags = {
      project = "agent-integration-platform"
      owner   = var.owner
    }
  }
}

# ---- storage: documents -----------------------------------------------------
resource "aws_s3_bucket" "docs" {
  bucket = "${var.name_prefix}-docs"
}

resource "aws_s3_bucket_public_access_block" "docs" {
  bucket                  = aws_s3_bucket.docs.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "docs" {
  bucket = aws_s3_bucket.docs.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_versioning" "docs" {
  bucket = aws_s3_bucket.docs.id

  versioning_configuration {
    status = "Enabled"
  }
}

# ---- queues: ingest jobs and the dead-letter queue ---------------------------
resource "aws_sqs_queue" "ingest_dlq" {
  name                    = "${var.name_prefix}-ingest-dlq"
  sqs_managed_sse_enabled = true
}

resource "aws_sqs_queue" "ingest" {
  name                       = "${var.name_prefix}-ingest"
  visibility_timeout_seconds = 60
  sqs_managed_sse_enabled    = true

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.ingest_dlq.arn
    maxReceiveCount     = 3
  })
}

# ---- secret: the LLM key (the value is never in this code) -------------------
resource "aws_secretsmanager_secret" "llm_api_key" {
  name = "${var.name_prefix}/llm-api-key"
}

# ---- IAM: one user with the three resources and nothing else -----------------
data "aws_iam_policy_document" "app_s3_bucket" {
  statement {
    sid       = "ListDocsBucket"
    effect    = "Allow"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.docs.arn]
  }
}

data "aws_iam_policy_document" "app_s3_objects" {
  statement {
    sid       = "ReadWriteDocsObjects"
    effect    = "Allow"
    actions   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
    resources = ["${aws_s3_bucket.docs.arn}/*"]
  }
}

data "aws_iam_policy_document" "app_sqs" {
  statement {
    sid    = "UseIngestQueues"
    effect = "Allow"
    actions = [
      "sqs:SendMessage",
      "sqs:ReceiveMessage",
      "sqs:DeleteMessage",
      "sqs:GetQueueAttributes",
      "sqs:GetQueueUrl",
    ]
    resources = [aws_sqs_queue.ingest.arn, aws_sqs_queue.ingest_dlq.arn]
  }
}

data "aws_iam_policy_document" "app_secret" {
  statement {
    sid       = "ReadLlmKey"
    effect    = "Allow"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [aws_secretsmanager_secret.llm_api_key.arn]
  }
}

data "aws_iam_policy_document" "app" {
  source_policy_documents = [
    data.aws_iam_policy_document.app_s3_bucket.json,
    data.aws_iam_policy_document.app_s3_objects.json,
    data.aws_iam_policy_document.app_sqs.json,
    data.aws_iam_policy_document.app_secret.json,
  ]
}

resource "aws_iam_user" "app" {
  name = "aip-local-app"
}

resource "aws_iam_user_policy" "app" {
  name   = "aip-local-app-access"
  user   = aws_iam_user.app.name
  policy = data.aws_iam_policy_document.app.json
}

# ---- cost: a monthly budget with an alert at 80 percent ----------------------
resource "aws_budgets_budget" "monthly" {
  name         = "${var.name_prefix}-monthly"
  budget_type  = "COST"
  limit_amount = tostring(var.monthly_budget_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 80
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.alert_email]
  }
}
