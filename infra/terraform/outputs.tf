output "postgres_private_ip" {
  value = aws_instance.postgres.private_ip
}

output "postgres_public_ip" {
  value = aws_instance.postgres.public_ip
}

output "pgbouncer_private_ip" {
  value = aws_instance.pgbouncer.private_ip
}

output "pgbouncer_public_ip" {
  value = aws_instance.pgbouncer.public_ip
}

output "api_loadgen_private_ips" {
  value = aws_instance.loadgen_api[*].private_ip
}

output "api_loadgen_public_ips" {
  value = aws_instance.loadgen_api[*].public_ip
}

output "export_loadgen_private_ip" {
  value = aws_instance.loadgen_export.private_ip
}

output "export_loadgen_public_ip" {
  value = aws_instance.loadgen_export.public_ip
}

output "resolved_ami_id" {
  value = nonsensitive(data.aws_ssm_parameter.ubuntu_ami.value)
}

output "availability_zone" {
  value = local.availability_zone
}
