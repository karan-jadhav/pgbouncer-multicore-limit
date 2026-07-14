# Experiment design

The authoritative research questions, hypotheses, workload definitions, fixed
connection budget, rejection rules, and analysis rules are in
[`plan.md`](../plan.md).

The headline comparison holds these values constant:

- PostgreSQL application connection budget: 128
- PgBouncer process counts: 1, 2, 4, and 8
- dataset, endpoint port, TLS mode, hosts, and measurement duration
- open-loop offered rate for latency comparisons
- randomized topology order within every repeat

The local 100,000-row dataset is only a functional test. Final AWS runs use the
20,000,000-row dataset and cannot be combined statistically with local results.

Before publication-quality runs, commit the final matrices, tag that commit as
`experiment-design-v1`, and record later design changes in `decision-log.md`.
