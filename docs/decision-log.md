# Decision log

## 2026-07-13 — Local verification path

Added a scaled-down Docker Compose environment using the same PostgreSQL major
version, PgBouncer source release, fixed pool budget, shared port, isolated
topology, load generator, manifests, collectors, and validation path as AWS.
Local results are functional checks only.

## 2026-07-13 — Version pins

Pinned PostgreSQL 18.4 and PgBouncer 1.25.2. The PgBouncer source tarball is
verified with SHA-256 before compilation.

## 2026-07-13 — Article scope

Article and social-post writing and an `article/` directory are excluded from
the repository at the user's request. Experiment results, charts, and decision
data remain in scope.

## 2026-07-14 — Simplified temporary AWS network

Use one public subnet for the four measured hosts. Public IPs are used only for
direct SSH and artifact copying from the local runner; database and network
measurements use private IPs. The NAT gateway, private subnet, bastion/control
host, and Prometheus installation were removed because they do not contribute
to the experiment. SSH port 22 is open to `0.0.0.0/0` at the user's request and
requires the configured EC2 key pair.
