#!/bin/bash

# =========================================================
#  SYSTEM INSTALLATION SCRIPT
# =========================================================

# Exit immediately if a command exits with a non-zero status
set -e

# ---------------------------------------------------------
# Visual Configuration & Helpers
# ---------------------------------------------------------
RED='\033[0;31m'
GREEN='\033[1;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# ---------------------------------------------------------
# User Detection (Crucial for Permissions)
# ---------------------------------------------------------
# We need to know who ran sudo to give permissions back to them later.
if [ -n "$SUDO_USER" ]; then
    REAL_USER="$SUDO_USER"
    REAL_GROUP=$(id -gn "$SUDO_USER")
else
    # Fallback if run directly as root (not recommended based on your request)
    REAL_USER="$USER"
    REAL_GROUP=$(id -gn "$USER")
fi

# Check for sudo/root execution
if [ "$EUID" -ne 0 ]; then
  echo -e "${RED}Error: This script must be run as root.${NC}"
  echo -e "${YELLOW}Usage: sudo ./install.sh${NC}"
  exit 1
fi

# Get the absolute path of the directory where this script is located
PROJECT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
BUILD_DIR=$(mktemp -d)

# Helper function for printing status
log() {
    echo -e "\n${GREEN}[INSTALL]${NC} $1"
}

log_sub() {
    echo -e "   ${CYAN}->${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARNING] $1${NC}"
}

# Cleanup function to remove temp build dir on exit
cleanup() {
    if [ -d "$BUILD_DIR" ]; then
        rm -rf "$BUILD_DIR"
    fi
}
trap cleanup EXIT

# ---------------------------------------------------------
# Start
# ---------------------------------------------------------
echo -e "${CYAN}==============================================${NC}"
echo -e "${CYAN}   STARTING INSTALLATION PROCESS              ${NC}"
echo -e "${CYAN}==============================================${NC}"
log "Project Directory: ${YELLOW}$PROJECT_DIR${NC}"
log "Target User for Permissions: ${YELLOW}$REAL_USER${NC}"

# ---------------------------------------------------------
# 1. Install Build Dependencies
# ---------------------------------------------------------
log "Step 1/6: Installing system dependencies via APT..."

log_sub "Updating package lists..."
apt-get update -qq

log_sub "Installing libraries..."
apt-get install -y libzmq3-dev libcjson-dev libcurl4-openssl-dev python3-venv \
    autoconf automake libtool pkg-config git autoconf-archive libtool libusb-1.0-0-dev libfftw3-dev

# ---------------------------------------------------------
# 2. Install libgpiod v2 from source
# ---------------------------------------------------------
log "Step 2/6: Building and installing libgpiod v2..."

cd "$BUILD_DIR"
log_sub "Cloning libgpiod into temporary directory..."
git clone https://git.kernel.org/pub/scm/libs/libgpiod/libgpiod.git --quiet
cd libgpiod

log_sub "Configuring build..."
./autogen.sh > /dev/null
./configure > /dev/null

log_sub "Compiling (make)..."
make > /dev/null

log_sub "Installing (make install)..."
make install > /dev/null
ldconfig

log_sub "Forcing NTP auto-sync..."
timedatectl set-ntp true

# ---------------------------------------------------------
# 3. Install kalibrate-hackrf from source
# ---------------------------------------------------------
log "Step 3/6: Building and installing kalibrate-hackrf..."

cd "$BUILD_DIR"
log_sub "Cloning kalibrate-hackrf..."
git clone https://github.com/scateu/kalibrate-hackrf.git --quiet
cd kalibrate-hackrf

log_sub "Bootstrapping..."
./bootstrap > /dev/null
log_sub "Configuring..."
./configure > /dev/null
log_sub "Compiling..."
make > /dev/null
log_sub "Installing..."
make install > /dev/null

# ---------------------------------------------------------
# 4. Install venv and project
# ---------------------------------------------------------
log "Step 4/6: Setting up Python environment and compiling project C code..."

if [ ! -d "$PROJECT_DIR" ]; then
    echo -e "${RED}Error: Project directory $PROJECT_DIR not found!${NC}"
    exit 1
fi

cd "$PROJECT_DIR"

if [ ! -d "venv" ]; then
    log_sub "Creating Python virtual environment (venv)..."
    python3 -m venv venv
else
    log_sub "Virtual environment already exists."
fi

log_sub "Activating venv and installing requirements..."
source venv/bin/activate
pip install --upgrade pip --quiet

if [ -f "requirements.txt" ]; then
    pip install -r requirements.txt
else
    log_warn "requirements.txt not found in $PROJECT_DIR."
fi

if [ -f "build.sh" ]; then
    log_sub "Executing local build.sh..."
    chmod +x build.sh
    ./build.sh
else
    echo -e "${RED}Error: build.sh not found in $PROJECT_DIR${NC}"
    deactivate
    exit 1
fi

deactivate

# Set executable permissions for apps (temporarily as root)
[ -f "$PROJECT_DIR/rf_app" ] && chmod +x "$PROJECT_DIR/rf_app"
[ -f "$PROJECT_DIR/ltegps_app" ] && chmod +x "$PROJECT_DIR/ltegps_app"

# ---------------------------------------------------------
# 5. Install Daemons (Systemd)
# ---------------------------------------------------------
log "Step 5/6: Installing Systemd Services & Timers..."

DAEMONS_DIR="$PROJECT_DIR/daemons"

if [ -d "$DAEMONS_DIR" ]; then
    cd "$DAEMONS_DIR"
    
    # Enable nullglob to safely loop over files
    shopt -s nullglob
    FILES=(*.service *.timer)
    shopt -u nullglob

    if [ ${#FILES[@]} -eq 0 ]; then
        log_warn "No .service or .timer files found in daemons directory!"
    else
        for f in "${FILES[@]}"; do
            log_sub "Copying $f to /etc/systemd/system/..."
            cp "$f" /etc/systemd/system/
        done

        log_sub "Reloading systemd daemon..."
        systemctl daemon-reload

        for f in "${FILES[@]}"; do
            log_sub "Enabling and Restarting $f..."
            systemctl enable "$f"
            systemctl restart "$f"
        done
    fi
else
    echo -e "${RED}Error: 'daemons' directory not found inside $PROJECT_DIR${NC}"
    exit 1
fi

# ---------------------------------------------------------
# 6. Final Permissions (Crucial Step)
# ---------------------------------------------------------
log "Step 6/6: Applying Final Permissions for user '$REAL_USER'..."

# A. Handle Project Directory
log_sub "Granting recursive ownership of $PROJECT_DIR to $REAL_USER..."
chown -R "$REAL_USER":"$REAL_GROUP" "$PROJECT_DIR"
# Grant Read/Write/Execute to Owner, Read/Execute to Group/Others
chmod -R 755 "$PROJECT_DIR" 

# B. Handle /dev/shm/persistent.json
SHM_FILE="/dev/shm/persistent.json"

log_sub "Configuring $SHM_FILE..."

if [ ! -f "$SHM_FILE" ]; then
    log_sub "File does not exist. Creating it..."
    touch "$SHM_FILE"
    # Optional: Initialize with empty JSON object so apps don't crash on read
    echo "{}" > "$SHM_FILE"
fi

log_sub "Granting ownership of $SHM_FILE to $REAL_USER..."
chown "$REAL_USER":"$REAL_GROUP" "$SHM_FILE"
chmod 666 "$SHM_FILE" # Read/Write for everyone (common for SHM), or use 600 for strictly user only

# ---------------------------------------------------------
# Completion
# ---------------------------------------------------------
echo -e "\n${CYAN}==============================================${NC}"
echo -e "${GREEN}   INSTALLATION COMPLETE SUCCESSFULLY         ${NC}"
echo -e "${CYAN}==============================================${NC}"
log "Services started. Permissions for user '$REAL_USER' applied."