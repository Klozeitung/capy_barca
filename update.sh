#!/bin/bash

set -e

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
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
    echo "Possible cause: another SSH configuration in ~/.ssh/config"
    echo "is overriding the CapyBarca key for github.com."
    echo ""
    echo -e "${CYAN}Check your SSH configuration:${NC}"
    echo "  cat ~/.ssh/config"
    echo ""
    echo -e "${CYAN}The CapyBarca entry should look like this:${NC}"
    echo "  Host github-capybarca"
    echo "      HostName github.com"
    echo "      User git"
    echo "      IdentityFile ~/.ssh/id_ed25519"
    echo ""
    echo "And the remote URL should use this alias:"
    echo "  git remote get-url origin"
    echo "  -> git@github-capybarca:Klozeitung/CapyBarca.git"
    echo ""
    echo "Running setup.sh again fixes this automatically."
    exit 1
fi

echo ""
chmod +x setup.sh
echo -e "${GREEN}[OK] setup.sh is executable.${NC}"

echo ""
CAPYBARCA_UPDATE=1 ./setup.sh
