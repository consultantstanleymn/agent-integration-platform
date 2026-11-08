variable "region" {
  type    = string
  default = "us-east-1"
}

variable "name" {
  type    = string
  default = "aip"
}

variable "environment" {
  type    = string
  default = "dev"
}

variable "image" {
  type        = string
  description = "Container image URI, for example an ECR image."
}

variable "certificate_arn" {
  type        = string
  description = "ACM certificate ARN for the HTTPS listener."
}

variable "db_instance_class" {
  type    = string
  default = "db.t4g.micro"
}

variable "desired_count" {
  type    = number
  default = 2
}

variable "llm_provider" {
  type    = string
  default = "anthropic"
}

variable "embedding_provider" {
  type    = string
  default = "openai_compat"
}
