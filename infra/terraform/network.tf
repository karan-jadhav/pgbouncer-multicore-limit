resource "aws_vpc" "experiment" {
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = { Name = "${var.project}-${var.environment}" }
}

resource "aws_internet_gateway" "experiment" {
  vpc_id = aws_vpc.experiment.id
  tags   = { Name = "${var.project}-${var.environment}" }
}

resource "aws_subnet" "experiment" {
  vpc_id                  = aws_vpc.experiment.id
  cidr_block              = var.subnet_cidr
  availability_zone       = local.availability_zone
  map_public_ip_on_launch = true
  tags                    = { Name = "${var.project}-${var.environment}" }
}

resource "aws_route_table" "experiment" {
  vpc_id = aws_vpc.experiment.id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.experiment.id
  }
  tags = { Name = "${var.project}-${var.environment}" }
}

resource "aws_route_table_association" "experiment" {
  subnet_id      = aws_subnet.experiment.id
  route_table_id = aws_route_table.experiment.id
}
