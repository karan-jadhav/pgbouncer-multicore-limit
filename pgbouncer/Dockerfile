FROM debian:bookworm-slim AS build

ARG PGBOUNCER_VERSION
ARG PGBOUNCER_SHA256

RUN apt-get update \
    && apt-get install --yes --no-install-recommends \
        build-essential ca-certificates curl libevent-dev libssl-dev pandoc pkg-config python3 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
RUN curl --fail --location --output pgbouncer.tar.gz \
        "https://www.pgbouncer.org/downloads/files/${PGBOUNCER_VERSION}/pgbouncer-${PGBOUNCER_VERSION}.tar.gz" \
    && echo "${PGBOUNCER_SHA256}  pgbouncer.tar.gz" | sha256sum --check \
    && tar --extract --gzip --file pgbouncer.tar.gz --strip-components=1 \
    && ./configure --prefix=/opt/pgbouncer \
    && make --jobs="$(nproc)" \
    && make install

FROM debian:bookworm-slim

RUN apt-get update \
    && apt-get install --yes --no-install-recommends \
        ca-certificates libevent-2.1-7 libssl3 postgresql-client \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 999 pgbouncer \
    && useradd --system --uid 999 --gid pgbouncer --home-dir /var/lib/pgbouncer pgbouncer \
    && install --directory --owner pgbouncer --group pgbouncer /run/pgbouncer /var/log/pgbouncer

COPY --from=build /opt/pgbouncer /opt/pgbouncer
COPY --chown=pgbouncer:pgbouncer pgbouncer/scripts/start-local.sh /usr/local/bin/start-pgbouncer

USER pgbouncer
EXPOSE 6432 6433
ENTRYPOINT ["/usr/local/bin/start-pgbouncer"]
