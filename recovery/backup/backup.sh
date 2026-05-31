#!/bin/bash
# =============================================================================
# backup.sh
# Runs on the backup machine (not on the CapyBarca server itself).
# Connects via SSH/Tailscale and backs up CapyBarca as a portable .capy file.
#
# Setup:
#   1. Download this file via the CapyBarca UI (Settings → Backup)
#   2. Fill in the four configuration variables below
#   3. chmod +x backup.sh && ./backup.sh
#
# Contents of the .capy file (internally a tar.gz):
#   version.txt    - CapyBarca version at time of backup
#   meta.json      - timestamp, version, origin
#   db.sql.gz      - pg_dump of the CapyBarca database
#   uploads.tar.gz - static/uploads/ in full
#   env            - .env file (credentials, SECRET_KEY)
#
# Streaming approach: no temporary files are created on the server.
# All data streams directly over SSH to this machine.
#
# Can also be called as a subroutine with a custom output directory:
#   ./backup.sh /path/to/output/dir
# =============================================================================

set -euo pipefail

# ─── Configuration ─── Fill in before use ────────────────────────────────────

REMOTE_HOST="YOUR_TAILSCALE_HOSTNAME_HERE"
REMOTE_USER="YOUR_SSH_USERNAME_HERE"
CAPYBARCA_DIR="/path/to/capybarca"       # Absolute path to CapyBarca on the server

OUTPUT_DIR="${1:-/path/to/local/backup}" # Where .capy files are stored locally

# ─── Initialization ───────────────────────────────────────────────────────────

DATE=$(date +"%Y-%m-%d_%H-%M")
CAPY_NAME="backup_${DATE}.capy"
LOCAL_TMP=$(mktemp -d)

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }
die() { log "ERROR: $*"; exit 1; }

trap 'rm -rf "${LOCAL_TMP}"' EXIT

log "============================================================"
log "  CapyBarca backup started: ${DATE}"
log "============================================================"

# ─── Pre-flight checks ────────────────────────────────────────────────────────

mkdir -p "${OUTPUT_DIR}"

log "Checking connection to ${REMOTE_HOST}..."
ssh -o ConnectTimeout=10 -o BatchMode=yes \
    "${REMOTE_USER}@${REMOTE_HOST}" "echo ok" &>/dev/null \
    || die "Server unreachable. Is Tailscale running?"

ssh "${REMOTE_USER}@${REMOTE_HOST}" \
    "[ -f '${CAPYBARCA_DIR}/docker-compose.yml' ]" \
    || die "CapyBarca not found at ${CAPYBARCA_DIR}."

# ─── Read credentials from .env ───────────────────────────────────────────────

log "Reading configuration..."

read_env() {
    ssh "${REMOTE_USER}@${REMOTE_HOST}" \
        "grep -m1 '^${1}=' '${CAPYBARCA_DIR}/.env' 2>/dev/null | cut -d'=' -f2-"
}

PG_USER=$(read_env POSTGRES_USER)
PG_DB=$(read_env POSTGRES_DB)

[ -n "${PG_USER}" ] || die "POSTGRES_USER not found in .env."
[ -n "${PG_DB}" ]   || die "POSTGRES_DB not found in .env."

log "  Database: ${PG_DB} (user: ${PG_USER})"

# ─── version.txt ──────────────────────────────────────────────────────────────

CB_VERSION=$(ssh "${REMOTE_USER}@${REMOTE_HOST}" \
    "grep -o 'version=\"[^\"]*\"' '${CAPYBARCA_DIR}/backend/app/main.py' 2>/dev/null \
     | head -1 | grep -oE '[0-9]+\.[0-9]+\.[0-9]+'" \
    || echo "unknown")

echo "CapyBarca Version: ${CB_VERSION}" > "${LOCAL_TMP}/version.txt"
log "  Version: ${CB_VERSION}"

# ─── Step 1: PostgreSQL dump (streamed) ───────────────────────────────────────

log "Step 1/4: Backing up database (${PG_DB})..."

ssh "${REMOTE_USER}@${REMOTE_HOST}" \
    "cd '${CAPYBARCA_DIR}' && docker compose exec -T db pg_dump -U '${PG_USER}' '${PG_DB}' | gzip" \
    > "${LOCAL_TMP}/db.sql.gz"

log "  DB dump: $(du -sh "${LOCAL_TMP}/db.sql.gz" | cut -f1)"

# ─── Step 2: Uploads (streamed) ───────────────────────────────────────────────

log "Step 2/4: Backing up uploads..."

ssh "${REMOTE_USER}@${REMOTE_HOST}" \
    "tar czf - -C '${CAPYBARCA_DIR}/static/uploads' ." \
    > "${LOCAL_TMP}/uploads.tar.gz"

log "  Uploads: $(du -sh "${LOCAL_TMP}/uploads.tar.gz" | cut -f1)"

# ─── Step 3: .env and metadata ────────────────────────────────────────────────

log "Step 3/4: Backing up .env and metadata..."

scp -q "${REMOTE_USER}@${REMOTE_HOST}:${CAPYBARCA_DIR}/.env" "${LOCAL_TMP}/env"

cat > "${LOCAL_TMP}/meta.json" << EOF
{
  "created": "${DATE}",
  "capybarca_version": "${CB_VERSION}",
  "remote_host": "${REMOTE_HOST}",
  "capybarca_dir": "${CAPYBARCA_DIR}",
  "pg_database": "${PG_DB}"
}
EOF

# ─── Step 4: Package .capy ────────────────────────────────────────────────────

log "Step 4/4: Packaging ${CAPY_NAME}..."

tar czf "${OUTPUT_DIR}/${CAPY_NAME}" \
    -C "${LOCAL_TMP}" \
    version.txt meta.json db.sql.gz uploads.tar.gz env

CAPY_SIZE=$(du -sh "${OUTPUT_DIR}/${CAPY_NAME}" | cut -f1)
log "  Done: ${OUTPUT_DIR}/${CAPY_NAME} (${CAPY_SIZE})"

# ─── Write log on server (keep last 10) ───────────────────────────────────────

LOG_DIR="${CAPYBARCA_DIR}/recovery/backup/log"

ssh "${REMOTE_USER}@${REMOTE_HOST}" "mkdir -p '${LOG_DIR}'"

ssh "${REMOTE_USER}@${REMOTE_HOST}" "cat > '${LOG_DIR}/backup_${DATE}.log'" << EOF
Created:   ${DATE}
Version:   ${CB_VERSION}
File:      ${OUTPUT_DIR}/${CAPY_NAME}
Size:      ${CAPY_SIZE}
Status:    OK
EOF

ssh "${REMOTE_USER}@${REMOTE_HOST}" \
    "ls -t '${LOG_DIR}'/*.log 2>/dev/null | tail -n +11 | xargs rm -f 2>/dev/null || true"

log "Log written: ${LOG_DIR}/backup_${DATE}.log"
log "============================================================"
log "  Backup complete: ${CAPY_NAME} (${CAPY_SIZE})"
log "============================================================"
