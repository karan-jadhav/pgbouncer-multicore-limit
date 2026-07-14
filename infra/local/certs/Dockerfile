FROM alpine:3.23
RUN apk add --no-cache openssl
COPY infra/local/certs/generate.sh /usr/local/bin/generate-certificates
ENTRYPOINT ["/usr/local/bin/generate-certificates"]
