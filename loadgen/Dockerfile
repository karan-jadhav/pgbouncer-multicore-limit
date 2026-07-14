FROM rust:1.97-bookworm AS build

WORKDIR /build
COPY loadgen/Cargo.toml loadgen/Cargo.lock ./
COPY loadgen/src ./src
RUN cargo build --release --locked

FROM debian:bookworm-slim
RUN apt-get update \
    && apt-get install --yes --no-install-recommends ca-certificates libssl3 \
    && rm -rf /var/lib/apt/lists/*
COPY --from=build /build/target/release/pgbouncer-loadgen /usr/local/bin/loadgen
USER nobody
ENTRYPOINT ["/usr/local/bin/loadgen"]
