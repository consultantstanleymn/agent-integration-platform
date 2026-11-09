variable "region" {
  type        = string
  description = "AWS region."
  default     = "us-east-1"
}

variable "name_prefix" {
  type        = string
  description = "Prefix for resource names. The bucket name must be globally unique."
  default     = "aip-engagement"
}

variable "owner" {
  type        = string
  description = "Value of the owner tag."
  default     = "Stanley Sujith Nelavala"
}

variable "monthly_budget_usd" {
  type        = number
  description = "Monthly cost limit in USD."
  default     = 10
}

variable "alert_email" {
  type        = string
  description = "Address that receives the budget alert. Set it before you apply."
}
