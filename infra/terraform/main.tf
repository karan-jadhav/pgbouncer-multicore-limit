terraform {
  required_version = ">= 1.8.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.0, < 7.0"
    }
  }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = local.common_tags
  }
}

data "aws_availability_zones" "available" {
  state = "available"
}

data "aws_ssm_parameter" "ubuntu_ami" {
  name = "/aws/service/canonical/ubuntu/server/24.04/stable/current/amd64/hvm/ebs-gp3/ami-id"
}

locals {
  availability_zone = coalesce(var.availability_zone, data.aws_availability_zones.available.names[0])
  common_tags = {
    Project     = var.project
    Environment = var.environment
    Owner       = var.owner
    ExpiresAt   = var.expires_at
    ManagedBy   = "terraform"
  }
}
