resource "aws_security_group" "pgbouncer" {
  name_prefix = "${var.project}-pgbouncer-"
  description = "PgBouncer measured traffic and administration"
  vpc_id      = aws_vpc.experiment.id

  ingress {
    description = "Temporary SSH access with the experiment key"
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    description     = "Shared PgBouncer port from load generators"
    from_port       = 6432
    to_port         = 6432
    protocol        = "tcp"
    security_groups = [aws_security_group.loadgen.id]
  }

  ingress {
    description     = "Isolated export port from load generators"
    from_port       = 6433
    to_port         = 6433
    protocol        = "tcp"
    security_groups = [aws_security_group.loadgen.id]
  }

  ingress {
    description     = "Network preflight from load generators"
    from_port       = 5201
    to_port         = 5201
    protocol        = "tcp"
    security_groups = [aws_security_group.loadgen.id]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  lifecycle { create_before_destroy = true }
}

resource "aws_security_group" "postgres" {
  name_prefix = "${var.project}-postgres-"
  description = "PostgreSQL access from PgBouncer and direct baseline generators"
  vpc_id      = aws_vpc.experiment.id

  ingress {
    description = "Temporary SSH access with the experiment key"
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    description     = "PostgreSQL from PgBouncer"
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [aws_security_group.pgbouncer.id]
  }

  ingress {
    description     = "Direct baseline PostgreSQL from load generators"
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [aws_security_group.loadgen.id]
  }

  ingress {
    description     = "Network preflight from PgBouncer"
    from_port       = 5201
    to_port         = 5201
    protocol        = "tcp"
    security_groups = [aws_security_group.pgbouncer.id]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  lifecycle { create_before_destroy = true }
}

resource "aws_security_group" "loadgen" {
  name_prefix = "${var.project}-loadgen-"
  description = "Load generator administration"
  vpc_id      = aws_vpc.experiment.id

  ingress {
    description = "Temporary SSH access with the experiment key"
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  lifecycle { create_before_destroy = true }
}
