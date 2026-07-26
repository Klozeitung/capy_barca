#!/bin/bash

set -e

HOST="0.0.0.0"
PORT="${PORT_BACKEND:-8000}"

# ─── Database migrations ──────────────────────────────────────────────────────
# Alembic is the single source of truth for schema state. Migrations run before
# uvicorn starts so the app always boots against a fully migrated database.
# The retry loop handles the window between the Docker healthcheck passing and
# PostgreSQL being ready to accept connections from this container.

echo "Running database migrations..."
for i in $(seq 1 10); do
    if alembic upgrade head; then
        echo "Migrations completed."
        break
    fi
    if [ "$i" -eq 10 ]; then
        echo "Migrations failed after 10 attempts. Aborting."
        exit 1
    fi
    echo "Migration attempt $i failed, retrying in 2s..."
    sleep 2
done

# ─── Proxy headers ────────────────────────────────────────────────────────────
# Without --proxy-headers every request appears to originate from the nginx
# container, so the login rate limiter keys all clients into a single global
# bucket instead of one bucket per caller.
#
# FORWARDED_ALLOW_IPS selects the peers whose X-Forwarded-For header is
# trusted. The default trusts every peer, which is sound here because the
# backend port is only published inside the Compose network, never to the
# host, and because nginx overwrites X-Forwarded-For with $remote_addr so the
# header can never carry a client-supplied value.
#
# The two halves belong together: when uvicorn trusts every peer it reads the
# leftmost entry of the header, which is exactly the entry an appending nginx
# configuration would let the client choose. Do not relax one side without the
# other.
#
# Residual exposure: another container on the same Compose network (Collabora)
# could forge the header. Closing that means setting FORWARDED_ALLOW_IPS to the
# nginx container address, which in turn requires a fixed address for it.

FORWARDED_IPS="${FORWARDED_ALLOW_IPS:-*}"

# ─── Uvicorn ──────────────────────────────────────────────────────────────────
# Arguments are collected in an array rather than a single string so that no
# value is subject to word splitting or pathname expansion on the exec line.
# The default "*" for --forwarded-allow-ips depends on this: as an unquoted
# string it would be expanded against the working directory.

ARGS=(
    app.main:app
    --host "${HOST}"
    --port "${PORT}"
    --log-level info
    --proxy-headers
    --forwarded-allow-ips "${FORWARDED_IPS}"
)

if [ "${DEBUG:-false}" = "true" ]; then
    echo "WARNING: DEBUG=true — development mode."
    echo "         uvicorn runs with --reload and the session cookie is issued"
    echo "         without the Secure flag. Do not use this in production."
    ARGS+=(--reload)
fi

SSL_KEY="/app/ssl/key.pem"
SSL_CERT="/app/ssl/cert.pem"

if [ -f "${SSL_KEY}" ] && [ -f "${SSL_CERT}" ]; then
    echo "SSL-certificates found. Starting with HTTPS."
    ARGS+=(--ssl-keyfile "${SSL_KEY}" --ssl-certfile "${SSL_CERT}")
else
    echo "No SSL-certificates found. Starting without HTTPS."
fi

DISPLAY_HOST="${TAILSCALE_IP:-${HOST}}"
echo "Trusting proxy headers from: ${FORWARDED_IPS}"
echo "Backend starting on ${DISPLAY_HOST}:${PORT}"
exec uvicorn "${ARGS[@]}"
