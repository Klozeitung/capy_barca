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

# ─── Uvicorn ──────────────────────────────────────────────────────────────────

ARGS="app.main:app --host ${HOST} --port ${PORT} --log-level info"

if [ "${DEBUG:-false}" = "true" ]; then
    ARGS="${ARGS} --reload"
fi

SSL_KEY="/app/ssl/key.pem"
SSL_CERT="/app/ssl/cert.pem"

if [ -f "${SSL_KEY}" ] && [ -f "${SSL_CERT}" ]; then
    echo "SSL-certificates found. Starting with HTTPS."
    ARGS="${ARGS} --ssl-keyfile ${SSL_KEY} --ssl-certfile ${SSL_CERT}"
else
    echo "No SSL-certificates found. Starting without HTTPS."
fi

DISPLAY_HOST="${TAILSCALE_IP:-${HOST}}"
echo "Backend starting on ${DISPLAY_HOST}:${PORT}"
exec uvicorn ${ARGS}
