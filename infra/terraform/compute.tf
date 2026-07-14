locals {
  expiry_user_data = templatefile("${path.module}/templates/expiry-cloud-init.yaml.tftpl", {
    max_runtime_hours = var.max_runtime_hours
  })
}

resource "aws_instance" "postgres" {
  ami                                  = data.aws_ssm_parameter.ubuntu_ami.value
  instance_type                        = var.postgres_instance_type
  subnet_id                            = aws_subnet.experiment.id
  vpc_security_group_ids               = [aws_security_group.postgres.id]
  key_name                             = var.ssh_key_name
  associate_public_ip_address          = true
  instance_initiated_shutdown_behavior = "terminate"
  user_data_replace_on_change          = true
  user_data                            = local.expiry_user_data

  root_block_device {
    volume_type           = "gp3"
    volume_size           = var.postgres_volume_size_gib
    iops                  = var.postgres_volume_iops
    throughput            = var.postgres_volume_throughput
    delete_on_termination = true
    encrypted             = true
  }

  metadata_options {
    http_endpoint = "enabled"
    http_tokens   = "required"
  }

  tags = { Name = "${var.project}-${var.environment}-postgres", Role = "postgres" }
}

resource "aws_instance" "pgbouncer" {
  ami                                  = data.aws_ssm_parameter.ubuntu_ami.value
  instance_type                        = var.pgbouncer_instance_type
  subnet_id                            = aws_subnet.experiment.id
  vpc_security_group_ids               = [aws_security_group.pgbouncer.id]
  key_name                             = var.ssh_key_name
  associate_public_ip_address          = true
  instance_initiated_shutdown_behavior = "terminate"
  user_data_replace_on_change          = true
  user_data                            = local.expiry_user_data

  root_block_device {
    volume_type           = "gp3"
    volume_size           = 40
    delete_on_termination = true
    encrypted             = true
  }

  metadata_options {
    http_endpoint = "enabled"
    http_tokens   = "required"
  }

  tags = { Name = "${var.project}-${var.environment}-pgbouncer", Role = "pgbouncer" }
}

resource "aws_instance" "loadgen_api" {
  count = var.enable_second_api_generator ? 2 : 1

  ami                                  = data.aws_ssm_parameter.ubuntu_ami.value
  instance_type                        = var.loadgen_instance_type
  subnet_id                            = aws_subnet.experiment.id
  vpc_security_group_ids               = [aws_security_group.loadgen.id]
  key_name                             = var.ssh_key_name
  associate_public_ip_address          = true
  instance_initiated_shutdown_behavior = "terminate"
  user_data_replace_on_change          = true
  user_data                            = local.expiry_user_data

  root_block_device {
    volume_type           = "gp3"
    volume_size           = 40
    delete_on_termination = true
    encrypted             = true
  }

  metadata_options {
    http_endpoint = "enabled"
    http_tokens   = "required"
  }

  tags = {
    Name = "${var.project}-${var.environment}-loadgen-api-${count.index + 1}"
    Role = "loadgen_api"
  }
}

resource "aws_instance" "loadgen_export" {
  ami                                  = data.aws_ssm_parameter.ubuntu_ami.value
  instance_type                        = var.loadgen_instance_type
  subnet_id                            = aws_subnet.experiment.id
  vpc_security_group_ids               = [aws_security_group.loadgen.id]
  key_name                             = var.ssh_key_name
  associate_public_ip_address          = true
  instance_initiated_shutdown_behavior = "terminate"
  user_data_replace_on_change          = true
  user_data                            = local.expiry_user_data

  root_block_device {
    volume_type           = "gp3"
    volume_size           = 40
    delete_on_termination = true
    encrypted             = true
  }

  metadata_options {
    http_endpoint = "enabled"
    http_tokens   = "required"
  }

  tags = { Name = "${var.project}-${var.environment}-loadgen-export", Role = "loadgen_export" }
}
