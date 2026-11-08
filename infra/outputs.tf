output "alb_dns_name" {
  value = aws_lb.main.dns_name
}

output "log_group" {
  value = aws_cloudwatch_log_group.api.name
}
