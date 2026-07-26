#!/bin/bash

set -e

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

echo ""
echo "========================================"
echo "  CapyBarca Setup"
echo "========================================"
echo ""

# ─── Mode detection ───────────────────────────────────────────────────────────
#
# Recovery mode is activated by:
#   ./setup.sh -recovery          (manual or via restore.sh)
#   CAPYBARCA_RECOVERY=1 ./setup.sh  (programmatic)
#
# In recovery mode the GitHub SSH setup and .env interaction are skipped.
# Instead the database is restored from recovery/import/db.sql.gz.

if [[ "${1:-}" == "-recovery" ]]; then
    CAPYBARCA_RECOVERY=1
fi
CAPYBARCA_RECOVERY="${CAPYBARCA_RECOVERY:-0}"

if [ "${CAPYBARCA_RECOVERY}" = "1" ]; then
    echo -e "${CYAN}  Mode: Recovery${NC}"
    echo ""
fi

# ─── Prerequisites ────────────────────────────────────────────────────────────

echo "Checking prerequisites..."

get_install_hint() {
    if command -v dnf &> /dev/null; then
        echo "sudo dnf install docker docker-compose"
    elif command -v apt &> /dev/null; then
        echo "sudo apt install docker.io docker-compose"
    elif command -v pacman &> /dev/null; then
        echo "sudo pacman -S docker docker-compose"
    elif command -v zypper &> /dev/null; then
        echo "sudo zypper install docker docker-compose"
    else
        echo "https://docs.docker.com/engine/install/"
    fi
}

if ! command -v docker &> /dev/null; then
    echo -e "${RED}[ERROR] Docker is not installed.${NC}"
    echo "Please install with: $(get_install_hint)"
    echo "Then run setup.sh again."
    exit 1
fi

if ! docker info &> /dev/null; then
    echo -e "${RED}[ERROR] Docker daemon is not running.${NC}"
    if command -v systemctl &> /dev/null; then
        echo "Start with: sudo systemctl start docker"
    else
        echo "Please start the Docker daemon manually."
    fi
    exit 1
fi

echo -e "${GREEN}[OK] Docker found.${NC}"

# ─── Stop running containers ──────────────────────────────────────────────────

echo ""
echo "Stopping running CapyBarca containers..."
docker compose down 2>/dev/null || true


# ─── GitHub SSH ───────────────────────────────────────────────────────────────
# (skipped in recovery mode)

if [ "${CAPYBARCA_RECOVERY}" != "1" ]; then

echo ""
echo "Checking GitHub SSH authentication..."

REMOTE_URL=$(git remote get-url origin 2>/dev/null || true)
SSH_KEY="$HOME/.ssh/id_ed25519"
SSH_CONFIG="$HOME/.ssh/config"
SSH_HOST_ALIAS="github-capybarca"

# Ensure SSH key exists
if [ ! -f "$SSH_KEY" ]; then
    echo "No SSH key found. Creating new key..."
    mkdir -p "$HOME/.ssh"
    chmod 700 "$HOME/.ssh"
    ssh-keygen -t ed25519 -f "$SSH_KEY" -N "" -C "capybarca@$(hostname)"
    echo -e "${GREEN}[OK] SSH key created.${NC}"
else
    echo -e "${GREEN}[OK] SSH key found: ${SSH_KEY}${NC}"
fi

# Add host alias to ~/.ssh/config if not already present
if ! grep -q "Host ${SSH_HOST_ALIAS}" "$SSH_CONFIG" 2>/dev/null; then
    echo "" >> "$SSH_CONFIG"
    echo "Host ${SSH_HOST_ALIAS}" >> "$SSH_CONFIG"
    echo "    HostName github.com" >> "$SSH_CONFIG"
    echo "    User git" >> "$SSH_CONFIG"
    echo "    IdentityFile ${SSH_KEY}" >> "$SSH_CONFIG"
    chmod 600 "$SSH_CONFIG"
    echo -e "${GREEN}[OK] SSH config entry for ${SSH_HOST_ALIAS} written.${NC}"
else
    echo -e "${GREEN}[OK] SSH config entry for ${SSH_HOST_ALIAS} already exists.${NC}"
fi

# Switch remote URL to github-capybarca alias
REPO_PATH=$(echo "$REMOTE_URL" | sed 's|https://github.com/||;s|git@github.com:||;s|git@github-capybarca:||')
TARGET_URL="git@${SSH_HOST_ALIAS}:${REPO_PATH}"
if [ "$REMOTE_URL" != "$TARGET_URL" ]; then
    git remote set-url origin "$TARGET_URL"
    echo -e "${GREEN}[OK] Remote URL switched to ${TARGET_URL}.${NC}"
else
    echo -e "${GREEN}[OK] Remote URL already correct.${NC}"
fi

# Test GitHub SSH connection
echo ""
echo "Testing SSH connection to GitHub..."
if ssh -T -o StrictHostKeyChecking=accept-new "${SSH_HOST_ALIAS}" 2>&1 | grep -q "successfully authenticated"; then
    echo -e "${GREEN}[OK] SSH connection to GitHub works.${NC}"
else
    echo -e "${YELLOW}SSH connection not confirmed. Is the key added to GitHub?${NC}"
    echo ""
    echo -e "${CYAN}Add this public key to your GitHub account:${NC}"
    echo -e "${CYAN}  https://github.com/settings/ssh/new${NC}"
    echo ""
    cat "${SSH_KEY}.pub"
    echo ""
    read -p "Press Enter once you have added the key to GitHub..."
fi

fi  # end: not recovery mode

# ─── Helper functions ─────────────────────────────────────────────────────────

REQUIRED_VARS=(
    "POSTGRES_USER"
    "POSTGRES_PASSWORD"
    "POSTGRES_DB"
    "PORT_DB"
    "PORT_BACKEND"
    "PORT_FRONTEND"
    "SECRET_KEY"
    "DEBUG"
    "TAILSCALE_IP"
    "TAILSCALE_HOSTNAME"
    "DATABASE_URL"
)

check_port() {
    local PORT=$1
    local NAME=$2
    local FALLBACK=$3
    if ss -tuln | grep -q ":${PORT} "; then
        echo -e "${YELLOW}[WARNING] Port ${PORT} (${NAME}) is already in use.${NC}" >&2
        read -p "Enter a different port for ${NAME} [${FALLBACK}]: " NEW_PORT >&2
        NEW_PORT=${NEW_PORT:-$FALLBACK}
        echo "$NEW_PORT"
    else
        echo "$PORT"
    fi
}

read_env_value() {
    local KEY=$1
    grep "^${KEY}=" .env 2>/dev/null | cut -d '=' -f2-
}

get_missing_vars() {
    local MISSING=()
    for KEY in "${REQUIRED_VARS[@]}"; do
        if ! grep -q "^${KEY}=" .env 2>/dev/null; then
            MISSING+=("$KEY")
        fi
    done
    echo "${MISSING[@]}"
}

write_env() {
    cat > .env << EOF
# PostgreSQL
POSTGRES_USER=${1}
POSTGRES_PASSWORD=${2}
POSTGRES_DB=${3}

# Ports
# PORT_DB is the host-side loopback mapping only (see docker-compose.yml).
PORT_DB=${5}
PORT_BACKEND=${6}
PORT_FRONTEND=${7}

# FastAPI
# Inside the Compose network PostgreSQL always listens on 5432, independently
# of the published host port, so DATABASE_URL must address that port.
DATABASE_URL=postgresql://${1}:${2}@db:5432/${3}
SECRET_KEY=${4}
# Development only. DEBUG=true starts uvicorn with --reload and drops the
# Secure flag from the session cookie. Production installations keep it false.
DEBUG=${11}

# Network
TAILSCALE_IP=${8}
TAILSCALE_HOSTNAME=${9}

# User management
# true = allow self-registration via the login page
ALLOW_NEW_USERS=${10}
EOF
    echo -e "${GREEN}[OK] .env saved.${NC}"
}

ask_var() {
    local KEY=$1
    local CURRENT=$2

    case $KEY in
        POSTGRES_USER)
            read -p "PostgreSQL username [${CURRENT:-capybarca}]: " INPUT
            echo "${INPUT:-${CURRENT:-capybarca}}" ;;
        POSTGRES_PASSWORD)
            read -s -p "PostgreSQL password: " INPUT
            echo "" >&2
            if [ -z "$INPUT" ] && [ -z "$CURRENT" ]; then
                echo -e "${RED}[ERROR] Password must not be empty.${NC}" >&2
                exit 1
            fi
            echo "${INPUT:-$CURRENT}" ;;
        POSTGRES_DB)
            read -p "PostgreSQL database name [${CURRENT:-capybarca_db}]: " INPUT
            echo "${INPUT:-${CURRENT:-capybarca_db}}" ;;
        PORT_DB)
            echo "$(check_port ${CURRENT:-5432} "PostgreSQL" 5433)" ;;
        PORT_BACKEND)
            echo "$(check_port ${CURRENT:-17012} "Backend" 17013)" ;;
        PORT_FRONTEND)
            echo "$(check_port ${CURRENT:-1701} "Frontend" 1702)" ;;
        SECRET_KEY)
            local GENERATED=$(cat /dev/urandom | tr -dc 'a-zA-Z0-9' | fold -w 64 | head -n 1)
            echo -e "${GREEN}[OK] SECRET_KEY generated automatically.${NC}" >&2
            echo "$GENERATED" ;;
        DEBUG)
            echo "false" ;;
        TAILSCALE_IP)
            local DETECTED_IP=""
            if command -v tailscale &> /dev/null && tailscale status &> /dev/null; then
                DETECTED_IP=$(tailscale ip -4 2>/dev/null || true)
            fi
            local DEFAULT_IP="${CURRENT:-$DETECTED_IP}"
            if [ -n "$DETECTED_IP" ] && [ "$DETECTED_IP" != "$CURRENT" ]; then
                echo -e "${GREEN}[OK] Tailscale IP detected automatically: ${DETECTED_IP}${NC}" >&2
            fi
            read -p "Tailscale IP address [${DEFAULT_IP:-100.x.y.z}]: " INPUT >&2
            INPUT="${INPUT:-$DEFAULT_IP}"
            if [ -z "$INPUT" ]; then
                echo -e "${RED}[ERROR] TAILSCALE_IP must not be empty.${NC}" >&2
                exit 1
            fi
            echo "$INPUT" ;;
        TAILSCALE_HOSTNAME)
            local DETECTED_HOST=""
            if command -v tailscale &> /dev/null && tailscale status &> /dev/null; then
                DETECTED_HOST=$(tailscale status --json | python3 -c \
                    "import sys, json; d = json.load(sys.stdin); print(d['Self']['DNSName'].rstrip('.'))" \
                    2>/dev/null || true)
            fi
            local DEFAULT_HOST="${CURRENT:-$DETECTED_HOST}"
            if [ -n "$DETECTED_HOST" ] && [ "$DETECTED_HOST" != "$CURRENT" ]; then
                echo -e "${GREEN}[OK] Tailscale hostname detected automatically: ${DETECTED_HOST}${NC}" >&2
            fi
            read -p "Tailscale hostname (optional, press Enter to skip) [${DEFAULT_HOST:-}]: " INPUT >&2
            echo "${INPUT:-$DEFAULT_HOST}" ;;
        ALLOW_NEW_USERS)
            echo "false" ;;
        DATABASE_URL)
            echo "" ;;
    esac
}

guided_setup() {
    echo ""
    echo -e "${CYAN}Guided setup...${NC}"
    echo ""

    DB_USER=$(ask_var POSTGRES_USER "")
    DB_PASSWORD=$(ask_var POSTGRES_PASSWORD "")
    DB_NAME=$(ask_var POSTGRES_DB "")
    SECRET_KEY=$(ask_var SECRET_KEY "")
    TAILSCALE_IP=$(ask_var TAILSCALE_IP "")
    TAILSCALE_HOSTNAME=$(ask_var TAILSCALE_HOSTNAME "")

    echo ""
    echo "Checking ports..."
    PORT_DB=$(ask_var PORT_DB "")
    PORT_BACKEND=$(ask_var PORT_BACKEND "")
    PORT_FRONTEND=$(ask_var PORT_FRONTEND "")

    ALLOW_NEW_USERS=$(read_env_value ALLOW_NEW_USERS)
    ALLOW_NEW_USERS="${ALLOW_NEW_USERS:-false}"

    # A guided setup always produces a production configuration. DEBUG can be
    # switched on afterwards via "Edit individual fields".
    write_env "$DB_USER" "$DB_PASSWORD" "$DB_NAME" "$SECRET_KEY" "$PORT_DB" "$PORT_BACKEND" "$PORT_FRONTEND" "$TAILSCALE_IP" "$TAILSCALE_HOSTNAME" "$ALLOW_NEW_USERS" "false"
}

patch_missing() {
    local MISSING=("$@")
    echo ""
    echo -e "${YELLOW}Filling in missing fields...${NC}"

    DB_USER=$(read_env_value POSTGRES_USER)
    DB_PASSWORD=$(read_env_value POSTGRES_PASSWORD)
    DB_NAME=$(read_env_value POSTGRES_DB)
    SECRET_KEY=$(read_env_value SECRET_KEY)
    PORT_DB=$(read_env_value PORT_DB)
    PORT_BACKEND=$(read_env_value PORT_BACKEND)
    PORT_FRONTEND=$(read_env_value PORT_FRONTEND)
    TAILSCALE_IP=$(read_env_value TAILSCALE_IP)
    TAILSCALE_HOSTNAME=$(read_env_value TAILSCALE_HOSTNAME)
    ALLOW_NEW_USERS=$(read_env_value ALLOW_NEW_USERS)
    DEBUG_VALUE=$(read_env_value DEBUG)

    for KEY in "${MISSING[@]}"; do
        echo -e "${YELLOW}Setting ${KEY}...${NC}"
        VALUE=$(ask_var "$KEY" "")
        case $KEY in
            POSTGRES_USER)      DB_USER=$VALUE ;;
            POSTGRES_PASSWORD)  DB_PASSWORD=$VALUE ;;
            POSTGRES_DB)        DB_NAME=$VALUE ;;
            SECRET_KEY)         SECRET_KEY=$VALUE ;;
            PORT_DB)            PORT_DB=$VALUE ;;
            PORT_BACKEND)       PORT_BACKEND=$VALUE ;;
            PORT_FRONTEND)      PORT_FRONTEND=$VALUE ;;
            DEBUG)              DEBUG_VALUE=$VALUE ;;
            TAILSCALE_IP)       TAILSCALE_IP=$VALUE ;;
            TAILSCALE_HOSTNAME) TAILSCALE_HOSTNAME=$VALUE ;;
            ALLOW_NEW_USERS)    ALLOW_NEW_USERS=$VALUE ;;
        esac
    done

    ALLOW_NEW_USERS="${ALLOW_NEW_USERS:-false}"
    # Patching only fills gaps; an existing DEBUG value is carried over
    # unchanged so a deliberate development setup is not silently reset.
    DEBUG_VALUE="${DEBUG_VALUE:-false}"
    write_env "$DB_USER" "$DB_PASSWORD" "$DB_NAME" "$SECRET_KEY" "$PORT_DB" "$PORT_BACKEND" "$PORT_FRONTEND" "$TAILSCALE_IP" "$TAILSCALE_HOSTNAME" "$ALLOW_NEW_USERS" "$DEBUG_VALUE"
}

edit_individual() {
    echo ""
    echo -e "${CYAN}Edit individual fields (press Enter to keep current value):${NC}"
    echo ""

    DB_USER=$(ask_var POSTGRES_USER "$(read_env_value POSTGRES_USER)")
    DB_PASSWORD=$(ask_var POSTGRES_PASSWORD "$(read_env_value POSTGRES_PASSWORD)")
    DB_NAME=$(ask_var POSTGRES_DB "$(read_env_value POSTGRES_DB)")
    PORT_DB=$(ask_var PORT_DB "$(read_env_value PORT_DB)")
    PORT_BACKEND=$(ask_var PORT_BACKEND "$(read_env_value PORT_BACKEND)")
    PORT_FRONTEND=$(ask_var PORT_FRONTEND "$(read_env_value PORT_FRONTEND)")

    read -p "Generate a new SECRET_KEY? [y/N]: " REGEN
    if [[ "$REGEN" =~ ^[yY]$ ]]; then
        SECRET_KEY=$(ask_var SECRET_KEY "")
    else
        SECRET_KEY=$(read_env_value SECRET_KEY)
    fi

    local CURRENT_TS=$(read_env_value TAILSCALE_IP)
    read -p "Tailscale IP [${CURRENT_TS}]: " INPUT
    TAILSCALE_IP=${INPUT:-$CURRENT_TS}

    TAILSCALE_HOSTNAME=$(ask_var TAILSCALE_HOSTNAME "$(read_env_value TAILSCALE_HOSTNAME)")

    # DEBUG is security-relevant and therefore never pre-selected: the prompt
    # defaults to "no" even when the current .env has it enabled.
    echo ""
    echo -e "${YELLOW}DEBUG mode is for development only.${NC}"
    echo "  It starts uvicorn with --reload and issues the session cookie"
    echo "  without the Secure flag. Current value: $(read_env_value DEBUG)"
    read -p "Enable DEBUG mode? [y/N]: " ENABLE_DEBUG
    if [[ "$ENABLE_DEBUG" =~ ^[yY]$ ]]; then
        DEBUG_VALUE="true"
    else
        DEBUG_VALUE="false"
    fi

    ALLOW_NEW_USERS=$(read_env_value ALLOW_NEW_USERS)
    ALLOW_NEW_USERS="${ALLOW_NEW_USERS:-false}"
    write_env "$DB_USER" "$DB_PASSWORD" "$DB_NAME" "$SECRET_KEY" "$PORT_DB" "$PORT_BACKEND" "$PORT_FRONTEND" "$TAILSCALE_IP" "$TAILSCALE_HOSTNAME" "$ALLOW_NEW_USERS" "$DEBUG_VALUE"
}

# ─── .env management ──────────────────────────────────────────────────────────

echo ""
echo "Checking configuration..."

if [ "${CAPYBARCA_RECOVERY}" = "1" ]; then
    # In recovery mode .env has already been placed by restore.sh.
    if [ ! -f ".env" ]; then
        echo -e "${RED}[ERROR] No .env found.${NC}"
        echo "restore.sh must be run before setup.sh -recovery."
        exit 1
    fi
    echo -e "${GREEN}[OK] .env restored from backup.${NC}"
else
    if [ ! -f ".env" ]; then
        echo -e "${YELLOW}No .env found.${NC}"
        guided_setup
    else
        echo -e "${GREEN}[OK] .env found.${NC}"
        MISSING=($(get_missing_vars))

        if [ ${#MISSING[@]} -gt 0 ]; then
            echo ""
            echo -e "${YELLOW}[WARNING] The following fields are missing:${NC}"
            for KEY in "${MISSING[@]}"; do
                echo "  - $KEY"
            done
            echo ""
            echo "What would you like to do?"
            echo "  1) Fill in missing fields only"
            echo "  2) Full reconfiguration"
            read -p "Choice [1]: " CHOICE
            CHOICE=${CHOICE:-1}
            if [ "$CHOICE" = "2" ]; then
                guided_setup
            else
                patch_missing "${MISSING[@]}"
            fi
        else
            echo -e "${GREEN}[OK] All required fields present.${NC}"
            if [ "${CAPYBARCA_UPDATE:-}" = "1" ]; then
                echo -e "${GREEN}[OK] .env kept in update mode.${NC}"
            else
                echo ""
                echo "What would you like to do?"
                echo "  1) Keep .env"
                echo "  2) Edit individual fields"
                echo "  3) Full reconfiguration"
                read -p "Choice [1]: " CHOICE
                CHOICE=${CHOICE:-1}
                case $CHOICE in
                    2) edit_individual ;;
                    3) guided_setup ;;
                    *) echo -e "${GREEN}[OK] .env kept.${NC}" ;;
                esac
            fi
        fi
    fi
fi

# ─── DEBUG guard ─────────────────────────────────────────────────────────────
#
# Earlier installer versions wrote DEBUG=true into every .env. Keeping an .env
# untouched therefore carries that value forward, so the state is reported
# explicitly instead of silently.

if [ "$(read_env_value DEBUG)" = "true" ]; then
    echo ""
    echo -e "${YELLOW}[WARNING] DEBUG=true is set in .env.${NC}"
    echo "  The session cookie will be issued without the Secure flag and"
    echo "  uvicorn will run with --reload. For a production instance, rerun"
    echo "  setup.sh and choose 'Edit individual fields' to disable DEBUG."
fi

# ─── Recovery: re-detect Tailscale values ────────────────────────────────────
#
# The .env from the backup belongs to the source machine. TAILSCALE_IP and
# TAILSCALE_HOSTNAME are machine-specific and must be updated when restoring
# on a different server. All other .env values (credentials, SECRET_KEY,
# ports) are intentionally kept from the backup.

if [ "${CAPYBARCA_RECOVERY}" = "1" ]; then
    echo ""
    echo "Updating Tailscale network values for this machine..."

    if command -v tailscale &> /dev/null && tailscale status &> /dev/null 2>&1; then
        DETECTED_IP=$(tailscale ip -4 2>/dev/null || true)
        DETECTED_HOST=$(tailscale status --json 2>/dev/null | python3 -c \
            "import sys, json; d = json.load(sys.stdin); print(d['Self']['DNSName'].rstrip('.'))" \
            2>/dev/null || true)

        BACKUP_IP=$(read_env_value TAILSCALE_IP)
        BACKUP_HOST=$(read_env_value TAILSCALE_HOSTNAME)
        PATCHED=false

        if [ -n "${DETECTED_IP}" ] && [ "${DETECTED_IP}" != "${BACKUP_IP}" ]; then
            sed -i "s|^TAILSCALE_IP=.*|TAILSCALE_IP=${DETECTED_IP}|" .env
            echo -e "${GREEN}[OK] TAILSCALE_IP:       ${BACKUP_IP} → ${DETECTED_IP}${NC}"
            PATCHED=true
        fi

        if [ -n "${DETECTED_HOST}" ] && [ "${DETECTED_HOST}" != "${BACKUP_HOST}" ]; then
            sed -i "s|^TAILSCALE_HOSTNAME=.*|TAILSCALE_HOSTNAME=${DETECTED_HOST}|" .env
            echo -e "${GREEN}[OK] TAILSCALE_HOSTNAME: ${BACKUP_HOST} → ${DETECTED_HOST}${NC}"
            PATCHED=true
        fi

        if [ "${PATCHED}" = "false" ]; then
            echo -e "${GREEN}[OK] Tailscale values match this machine — no changes needed.${NC}"
        fi
    else
        echo -e "${YELLOW}[WARNING] Tailscale not connected. Keeping network values from backup:${NC}"
        echo "  TAILSCALE_IP:       $(read_env_value TAILSCALE_IP)"
        echo "  TAILSCALE_HOSTNAME: $(read_env_value TAILSCALE_HOSTNAME)"
        echo "  If this is a different machine, run ./setup.sh again after connecting Tailscale."
    fi
fi

# Load ports and hostname for later use
PORT_BACKEND=$(read_env_value PORT_BACKEND)
PORT_FRONTEND=$(read_env_value PORT_FRONTEND)
TAILSCALE_HOSTNAME=$(read_env_value TAILSCALE_HOSTNAME)

# ─── SSL certificates ─────────────────────────────────────────────────────────

echo ""
echo "Checking SSL certificates..."

if [ -f "ssl/cert.pem" ] && [ -f "ssl/key.pem" ]; then
    echo -e "${GREEN}[OK] SSL certificates found.${NC}"
else
    echo -e "${YELLOW}No SSL certificates found.${NC}"
    echo ""

    if ! command -v tailscale &> /dev/null; then
        echo -e "${RED}[ERROR] tailscale is not installed.${NC}"
        echo "Please install Tailscale: https://tailscale.com/download"
        exit 1
    fi

    if ! tailscale status &> /dev/null; then
        echo -e "${RED}[ERROR] Tailscale is not connected.${NC}"
        echo "Please connect with 'tailscale up' and run setup.sh again."
        exit 1
    fi

    echo "CapyBarca requires HTTPS certificates."
    echo "These can be obtained automatically via Tailscale."
    echo ""
    read -p "Is HTTPS certificate issuance enabled in your Tailscale profile? [y/N]: " TS_CERT_ENABLED
    TS_CERT_ENABLED=${TS_CERT_ENABLED:-N}

    if [[ ! "$TS_CERT_ENABLED" =~ ^[yY]$ ]]; then
        echo ""
        echo -e "${CYAN}How to enable HTTPS certificates in Tailscale:${NC}"
        echo ""
        echo "  1. Open https://login.tailscale.com/admin/dns"
        echo "  2. Scroll to 'HTTPS Certificates'"
        echo "  3. Click 'Enable HTTPS'"
        echo "  4. Make sure MagicDNS is enabled"
        echo ""
        echo "Then run setup.sh again."
        exit 0
    fi

    TS_HOSTNAME=$(tailscale status --json | python3 -c \
        "import sys, json; d = json.load(sys.stdin); print(d['Self']['DNSName'].rstrip('.'))" \
        2>/dev/null)

    if [ -z "$TS_HOSTNAME" ]; then
        echo -e "${RED}[ERROR] Could not determine Tailscale hostname.${NC}"
        echo "Please check manually: tailscale status"
        exit 1
    fi

    echo "Creating certificate for: ${TS_HOSTNAME}"
    mkdir -p ssl

    if tailscale cert --cert-file ssl/cert.pem --key-file ssl/key.pem "$TS_HOSTNAME" 2>/dev/null; then
        echo -e "${GREEN}[OK] SSL certificate created successfully.${NC}"
    else
        echo -e "${YELLOW}[WARNING] Insufficient permissions, trying with sudo...${NC}"
        if sudo tailscale cert --cert-file ssl/cert.pem --key-file ssl/key.pem "$TS_HOSTNAME"; then
            echo -e "${GREEN}[OK] SSL certificate created successfully.${NC}"
        else
            echo -e "${RED}[ERROR] Certificate creation failed.${NC}"
            echo ""
            echo "Possible causes:"
            echo "  - HTTPS is not enabled in your Tailscale profile"
            echo "    Check at: https://login.tailscale.com/admin/dns"
            echo "  - MagicDNS is not enabled"
            exit 1
        fi
    fi
fi

# ─── Docker ───────────────────────────────────────────────────────────────────

echo ""
echo "Building containers (no cache)..."
docker compose build --no-cache

if [ "${CAPYBARCA_RECOVERY}" = "1" ]; then

    # ── Recovery: start DB first, restore dump, then start the rest ───────────

    echo ""
    echo "Starting database..."
    docker compose up -d db

    echo ""
    echo "Waiting for database..."
    PG_USER=$(read_env_value POSTGRES_USER)
    PG_DB=$(read_env_value POSTGRES_DB)
    for i in {1..30}; do
        if docker compose exec -T db pg_isready -U "${PG_USER}" -d "${PG_DB}" &>/dev/null; then
            echo -e "${GREEN}[OK] Database ready.${NC}"
            break
        fi
        if [ $i -eq 30 ]; then
            echo -e "${RED}[ERROR] Database not responding.${NC}"
            docker compose logs db
            exit 1
        fi
        sleep 2
    done

    echo ""
    echo "Restoring database from backup..."
    RECOVERY_DUMP="recovery/import/db.sql.gz"
    if [ ! -f "${RECOVERY_DUMP}" ]; then
        echo -e "${RED}[ERROR] ${RECOVERY_DUMP} not found.${NC}"
        echo "restore.sh must copy the file there before calling setup.sh -recovery."
        exit 1
    fi
    zcat "${RECOVERY_DUMP}" \
        | docker compose exec -T db psql -U "${PG_USER}" -d "${PG_DB}" -q
    echo -e "${GREEN}[OK] Database restored.${NC}"

    rm -f "${RECOVERY_DUMP}"
    echo -e "${GREEN}[OK] ${RECOVERY_DUMP} removed.${NC}"

    echo ""
    echo "Starting all containers..."
    docker compose up -d

else

    # ── Normal start ──────────────────────────────────────────────────────────

    echo ""
    echo "Starting containers..."
    docker compose up -d

fi

# ─── Backend health check ─────────────────────────────────────────────────────

echo ""
echo "Waiting for backend..."
for i in {1..30}; do
    if curl -sk https://localhost:${PORT_FRONTEND}/api/health > /dev/null; then
        echo -e "${GREEN}[OK] Backend is ready.${NC}"
        break
    fi
    if [ $i -eq 30 ]; then
        echo -e "${RED}[ERROR] Backend is not responding.${NC}"
        docker compose logs backend
        exit 1
    fi
    sleep 1
done

# ─── Database migrations ──────────────────────────────────────────────────────

echo ""
echo "Running database migrations..."
if docker compose exec backend alembic upgrade head; then
    echo -e "${GREEN}[OK] Migrations applied.${NC}"
else
    echo -e "${RED}[ERROR] Migrations failed.${NC}"
    docker compose logs backend
    exit 1
fi

# ─── Backend tests ────────────────────────────────────────────────────────────

echo ""
echo "Running backend tests..."

# The suite is defined in backend/test_suite.txt so that this installer and the
# GitHub Actions workflow always execute exactly the same set of files.
TEST_SUITE_FILE="backend/test_suite.txt"

if [ ! -f "${TEST_SUITE_FILE}" ]; then
    echo -e "${RED}[ERROR] ${TEST_SUITE_FILE} not found.${NC}"
    exit 1
fi

mapfile -t BACKEND_TESTS < <(sed -e 's/#.*//' -e 's/[[:space:]]//g' "${TEST_SUITE_FILE}" | grep -v '^$')

if [ ${#BACKEND_TESTS[@]} -eq 0 ]; then
    echo -e "${RED}[ERROR] ${TEST_SUITE_FILE} lists no test files.${NC}"
    exit 1
fi

echo "Suite: ${#BACKEND_TESTS[@]} test files from ${TEST_SUITE_FILE}"

if docker compose exec backend pytest "${BACKEND_TESTS[@]}" -v; then
    echo -e "${GREEN}[OK] Backend tests passed.${NC}"
else
    echo ""
    echo -e "${RED}[ERROR] Backend tests failed.${NC}"
    exit 1
fi

echo ""
echo -e "${GREEN}[OK] Frontend tests ran during image build.${NC}"

# ─── Done ─────────────────────────────────────────────────────────────────────

echo ""
echo -e "${GREEN}========================================"
echo "  All tests passed. CapyBarca is running."
echo -e "========================================${NC}"
echo ""
echo "  Local:    https://localhost:${PORT_FRONTEND}"
echo "  External: https://${TAILSCALE_HOSTNAME}:${PORT_FRONTEND}"
echo "  API Docs: Docker-internal only via ${PORT_BACKEND}/docs"
echo ""
echo "Cleaning up build cache..."
echo ""
docker builder prune -f
echo ""
echo "Streaming logs (Ctrl+C to stop)..."
echo ""
docker compose logs -f
