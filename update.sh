#!/bin/bash

set -e

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

# CLEANUP=1 ./update.sh prunes the Docker build cache once the stack is back
# up. Without it nothing is pruned. The value is handed to setup.sh explicitly
# below rather than left to inheritance, so the contract is visible in the file
# that documents it.

echo ""
echo "========================================"
echo "  CapyBarca Update"
echo "========================================"
echo ""

if ! command -v git &> /dev/null; then
    echo -e "${RED}[ERROR] git is not installed.${NC}"
    exit 1
fi

# A directory without a git repository cannot be updated. This is what an
# extracted "Download ZIP" archive looks like: the files are present, the
# history is not, and every git command below would fail for that one reason.
if ! git rev-parse --git-dir &> /dev/null; then
    echo -e "${RED}[ERROR] This directory is not a git clone.${NC}"
    echo ""
    echo "update.sh pulls the newest version from the repository, which requires"
    echo "a clone rather than an extracted archive. Either clone the repository"
    echo "and carry .env, ssl/ and static/uploads/ across, or replace the files"
    echo "by hand and run ./setup.sh directly."
    exit 1
fi

BRANCH=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "main")

# --ff-only rather than a bare pull. This checkout is meant to follow the
# remote and never to carry commits of its own, so a fast-forward is the only
# correct outcome. A bare pull would either stop to ask for a merge strategy
# or create a merge commit that can never be pushed from here and would break
# every later update in the same way.
echo "Fetching latest version..."
if ! git pull --ff-only; then
    echo ""
    echo -e "${RED}[ERROR] git pull failed. The reason is in the git output above.${NC}"
    echo ""
    echo -e "${CYAN}Diverged branches or a rejected fast-forward:${NC}"
    echo "  This checkout holds commits the remote does not have, or the remote"
    echo "  history was rewritten. Look at what is local-only first:"
    echo ""
    echo "    git fetch origin"
    echo "    git log --oneline --graph --decorate --all -20"
    echo "    git diff HEAD origin/${BRANCH} --stat"
    echo ""
    echo "  If nothing local-only is worth keeping, follow the remote again:"
    echo ""
    echo "    git reset --hard origin/${BRANCH}"
    echo ""
    echo -e "${YELLOW}  A hard reset leaves ignored and untracked files alone, so .env, ssl/${NC}"
    echo -e "${YELLOW}  and static/uploads/ are outside its reach. Run ./backup.sh first${NC}"
    echo -e "${YELLOW}  regardless.${NC}"
    echo ""
    echo -e "${CYAN}Authentication or network failure:${NC}"
    echo "  Check which remote is configured and whether it answers:"
    echo ""
    echo "    git remote -v"
    echo "    git ls-remote origin"
    exit 1
fi

echo ""
chmod +x setup.sh
echo -e "${GREEN}[OK] setup.sh is executable.${NC}"

echo ""
CAPYBARCA_UPDATE=1 CLEANUP="${CLEANUP:-}" ./setup.sh
