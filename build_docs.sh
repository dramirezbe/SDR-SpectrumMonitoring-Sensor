#!/bin/bash

# Exit immediately if a command exits with a non-zero status
set -e

# --- Color Definitions ---
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# --- Configuration ---
VENV_PATH="doc-venv"
DOCS_DIR="docs"

echo -e "${BLUE}🚀 Starting Documentation Rebuild...${NC}"

# 1. Activate Virtual Environment
if [ -d "$VENV_PATH" ]; then
    echo -e "${YELLOW}🐍 Activating virtual environment...${NC}"
    source "$VENV_PATH/bin/activate"
else
    echo -e "${RED}❌ Error: Virtual environment '$VENV_PATH' not found.${NC}"
    exit 1
fi

# 2. Navigate to Docs Directory
if [ -d "$DOCS_DIR" ]; then
    cd "$DOCS_DIR"
else
    echo -e "${RED}❌ Error: Directory '$DOCS_DIR' not found.${NC}"
    deactivate
    exit 1
fi

# 3. Clean Old Builds
echo -e "${YELLOW}🧹 Cleaning old build artifacts (_build and xml)...${NC}"
rm -rf _build xml

# 4. Generate HTML
echo -e "${BLUE}🏗️  Running 'make html'...${NC}"
if make html; then
    echo -e "${GREEN}✅ Build Successful!${NC}"
else
    echo -e "${RED}💥 Error: 'make html' failed.${NC}"
    deactivate
    exit 1
fi

# 5. Cleanup
echo -e "${YELLOW}🔌 Deactivating environment...${NC}"
deactivate

echo -e "${GREEN}✨ Done! Your documentation is ready in: ${BLUE}$DOCS_DIR/_build/html${NC}"