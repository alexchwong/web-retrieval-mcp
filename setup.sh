#!/usr/bin/env bash

set -Eeuo pipefail

readonly ENV_FILE=".env"
readonly ENV_TEMPLATE=".env.example"
readonly SECRET_PLACEHOLDER="replace-with-a-random-secret"

cd -- "$(dirname -- "${BASH_SOURCE[0]}")"

for command in docker openssl; do
    if ! command -v "$command" >/dev/null 2>&1; then
        printf 'Error: required command not found: %s\n' "$command" >&2
        exit 1
    fi
done

if ! docker compose version >/dev/null 2>&1; then
    printf 'Error: Docker Compose is unavailable. Install the Docker Compose plugin.\n' >&2
    exit 1
fi

if [[ ! -f "$ENV_FILE" ]]; then
    if [[ ! -f "$ENV_TEMPLATE" ]]; then
        printf 'Error: environment template not found: %s\n' "$ENV_TEMPLATE" >&2
        exit 1
    fi

    secret="$(openssl rand -hex 32)"
    if [[ -z "$secret" ]]; then
        printf 'Error: OpenSSL did not generate a secret.\n' >&2
        exit 1
    fi

    umask 077
    sed "s/${SECRET_PLACEHOLDER}/${secret}/" "$ENV_TEMPLATE" >"$ENV_FILE"
    chmod 600 "$ENV_FILE"
    printf 'Created %s with a fresh local SearxNG secret.\n' "$ENV_FILE"
else
    printf 'Using existing %s; local settings and secret were not changed.\n' "$ENV_FILE"
fi

docker compose up -d --build
