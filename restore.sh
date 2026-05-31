#!/bin/bash
# =============================================================================
# restore.sh
# Runs LOCALLY on the CapyBarca server (or a new server).
# Restores CapyBarca from a .capy file placed in recovery/import/.
#
# Usage:
#   Copy a .capy file into recovery/import/, then run ./restore.sh.
#   The newest .capy in recovery/import/ is selected automatically.
#
# What is restored:
#   .env, PostgreSQL database, static/uploads/
#
# What is NOT restored:
#   SSL certificates — setup.sh checks for them and creates them if missing.
# =============================================================================

set -e

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

CAPYBARCA_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IMPORT_DIR="${CAPYBARCA_DIR}/recovery/import"

ok()   { echo -e "${GREEN}[OK]${NC} $*"; }
warn() { echo -e "${YELLOW}[WARNING]${NC} $*"; }
die()  { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }

TMP_DIR=$(mktemp -d)
trap 'rm -rf "${TMP_DIR}"' EXIT

echo ""
echo "========================================"
echo "  CapyBarca Restore"
echo "========================================"
echo ""

# ─── Find .capy file ──────────────────────────────────────────────────────────

mkdir -p "${IMPORT_DIR}"

CAPY_FILE=$(ls -t "${IMPORT_DIR}"/*.capy 2>/dev/null | head -1 || true)
[ -n "${CAPY_FILE}" ] \
    || die "No .capy file found in ${IMPORT_DIR}."

echo -e "${CYAN}Backup file:${NC}  ${CAPY_FILE}"
echo -e "${CYAN}Size:${NC}         $(du -sh "${CAPY_FILE}" | cut -f1)"
echo ""

# ─── Extract and validate ─────────────────────────────────────────────────────

echo "Extracting and validating backup..."
tar xzf "${CAPY_FILE}" -C "${TMP_DIR}" \
    || die "Could not extract backup file (corrupted?)."

for REQUIRED in meta.json db.sql.gz uploads.tar.gz env version.txt; do
    [ -f "${TMP_DIR}/${REQUIRED}" ] \
        || die "Backup incomplete: '${REQUIRED}' is missing."
done
ok "Backup integrity verified."

# ─── Version check ────────────────────────────────────────────────────────────

BACKUP_VERSION=$(grep -oE '[0-9]+\.[0-9]+\.[0-9]+' "${TMP_DIR}/version.txt" | head -1 || true)
CURRENT_VERSION=$(grep -o 'version="[^"]*"' \
    "${CAPYBARCA_DIR}/backend/app/main.py" 2>/dev/null \
    | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1 || true)

echo ""
echo "Versions:"
echo "  Backup:  ${BACKUP_VERSION:-unknown}"
echo "  System:  ${CURRENT_VERSION:-unknown}"

if [ -n "${BACKUP_VERSION}" ] && [ -n "${CURRENT_VERSION}" ] \
    && [ "${BACKUP_VERSION}" != "${CURRENT_VERSION}" ]; then
    echo ""
    warn "Version mismatch detected."
    warn "The backup was created with a different version than the current system."
    warn "Database migrations may fail."
    warn "Proceed at your own risk — success is not guaranteed."
    echo ""
    read -rp "Continue anyway? Type 'yes' to confirm: " VERSION_CONFIRM
    [ "${VERSION_CONFIRM}" = "yes" ] || { echo "Aborted."; exit 0; }
else
    ok "Versions match."
fi

# ─── Show backup metadata ─────────────────────────────────────────────────────

echo ""
echo "Backup information:"
echo "─────────────────────────────────────────"
cat "${TMP_DIR}/meta.json"
echo ""
echo "─────────────────────────────────────────"
echo ""

# ─── Confirmation ─────────────────────────────────────────────────────────────

warn "WARNING: This action will overwrite all current data:"
warn "  - All Docker volumes will be removed (down -v)"
warn "  - .env will be replaced"
warn "  - static/uploads/ will be completely replaced"
warn "  - Database will be restored from backup"
echo ""
read -rp "Continue? Type 'yes' to confirm: " CONFIRM
[ "${CONFIRM}" = "yes" ] || { echo "Aborted."; exit 0; }
echo ""

# ─── Check prerequisites ──────────────────────────────────────────────────────

command -v docker &>/dev/null || die "Docker not found."
docker info &>/dev/null       || die "Docker daemon is not running."
cd "${CAPYBARCA_DIR}"
[ -f "docker-compose.yml" ]   || die "docker-compose.yml not found in ${CAPYBARCA_DIR}."

# ─── Tear down stack including volumes ───────────────────────────────────────

echo "Stopping stack and removing volumes..."
docker compose down -v 2>/dev/null || true
ok "Stack stopped, volumes removed."

# ─── Distribute files ─────────────────────────────────────────────────────────

echo ""
echo "Restoring .env..."
cp "${TMP_DIR}/env" "${CAPYBARCA_DIR}/.env"
ok ".env placed."

echo ""
echo "Restoring static/uploads/..."
UPLOADS_DIR="${CAPYBARCA_DIR}/static/uploads"
mkdir -p "${UPLOADS_DIR}"
sudo rm -rf "${UPLOADS_DIR:?}"/*
sudo tar xzf "${TMP_DIR}/uploads.tar.gz" -C "${UPLOADS_DIR}"
ok "Uploads extracted."

echo ""
echo "Placing database dump..."
cp "${TMP_DIR}/db.sql.gz" "${IMPORT_DIR}/db.sql.gz"
ok "db.sql.gz copied to recovery/import/."

# ─── Hand off to setup.sh -recovery ──────────────────────────────────────────

echo ""
echo "Starting setup.sh in recovery mode..."
echo ""
chmod +x "${CAPYBARCA_DIR}/setup.sh"
exec "${CAPYBARCA_DIR}/setup.sh" -recovery
