#!/bin/bash

set -e

GREEN='\033[0;32m'
RED='\033[0;31m'
CYAN='\033[0;36m'
NC='\033[0m'

echo ""
echo "========================================"
echo "  CapyBarca Update"
echo "========================================"
echo ""

if ! command -v git &> /dev/null; then
    echo -e "${RED}[ERROR] git is not installed.${NC}"
    exit 1
fi

echo "Fetching latest version..."
if ! git pull; then
    echo ""
    echo -e "${RED}[ERROR] git pull failed.${NC}"
    echo ""
    echo "Possible causes:"
    echo "  - No internet connection"
    echo "  - The remote repository is unavailable"
    echo "  - Local changes conflict with the update"
    echo ""
    echo -e "${CYAN}Check the remote URL:${NC}"
    echo "  git remote get-url origin"
    echo ""
    echo -e "${CYAN}If there are local conflicts:${NC}"
    echo "  git status"
    echo "  git stash   # stash local changes, then retry"
    exit 1
fi

echo ""
chmod +x setup.sh
echo -e "${GREEN}[OK] setup.sh is executable.${NC}"

echo ""
CAPYBARCA_UPDATE=1 ./setup.sh
