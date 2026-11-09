output "bucket_name" {
  value = aws_s3_bucket.docs.bucket
}

output "queue_url" {
  value = aws_sqs_queue.ingest.url
}

output "dlq_url" {
  value = aws_sqs_queue.ingest_dlq.url
}

output "secret_id" {
  value = aws_secretsmanager_secret.llm_api_key.name
}

output "app_user_name" {
  value = aws_iam_user.app.name
}
