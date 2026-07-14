variable "aws_region" {
  description = "AWS region for every experiment resource."
  type        = string
  default     = "us-east-1"
}

variable "availability_zone" {
  description = "Single availability zone for all measured hosts. The first available AZ is used when null."
  type        = string
  default     = null
}

variable "project" {
  type    = string
  default = "pgbouncer-multicore-limit"
}

variable "environment" {
  type    = string
  default = "experiment"
}

variable "owner" {
  description = "Owner tag applied to all resources."
  type        = string
}

variable "expires_at" {
  description = "ISO-8601 expiry timestamp recorded as a resource tag."
  type        = string
}

variable "max_runtime_hours" {
  description = "Hours after boot before each instance shuts down and terminates itself."
  type        = number
  default     = 8

  validation {
    condition     = var.max_runtime_hours >= 1 && var.max_runtime_hours <= 24
    error_message = "max_runtime_hours must be between 1 and 24."
  }
}

variable "ssh_key_name" {
  description = "Existing EC2 key pair used for SSH access."
  type        = string
}

variable "vpc_cidr" {
  type    = string
  default = "10.80.0.0/16"
}

variable "subnet_cidr" {
  type    = string
  default = "10.80.0.0/24"
}

variable "postgres_instance_type" {
  type    = string
  default = "m7i.4xlarge"
}

variable "pgbouncer_instance_type" {
  type    = string
  default = "c7i.8xlarge"
}

variable "loadgen_instance_type" {
  type    = string
  default = "c7i.4xlarge"
}

variable "enable_second_api_generator" {
  type    = bool
  default = false
}

variable "postgres_volume_size_gib" {
  type    = number
  default = 500
}

variable "postgres_volume_iops" {
  type    = number
  default = 12000
}

variable "postgres_volume_throughput" {
  type    = number
  default = 500
}
