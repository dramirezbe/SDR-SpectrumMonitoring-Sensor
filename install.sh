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
# User Detection
# ---------------------------------------------------------
# We prioritize 'anepi' as the target user based on your requirements
TARGET_USER="anepi"
TARGET_GROUP="anepi"

if [ "$EUID" -ne 0 ]; then
  echo -e "${RED}Error: This script must be run as root.${NC}"
  echo -e "${YELLOW}Usage: sudo ./install.sh${NC}"
  exit 1
fi

# Ensure the target user exists
if ! id "$TARGET_USER" &>/dev/null; then
    echo -e "${RED}Error: User '$TARGET_USER' not found.${NC}"
    exit 1
fi

PROJECT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
BUILD_DIR=$(mktemp -d)

log() { echo -e "\n${GREEN}[INSTALL]${NC} $1"; }
log_sub() { echo -e "   ${CYAN}->${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARNING] $1${NC}"; }

cleanup() {
    if [ -d "$BUILD_DIR" ]; then rm -rf "$BUILD_DIR"; fi
}
trap cleanup EXIT

# ---------------------------------------------------------
# Start
# ---------------------------------------------------------
echo -e "${CYAN}==============================================${NC}"
echo -e "${CYAN}   STARTING INSTALLATION PROCESS              ${NC}"
echo -e "${CYAN}==============================================${NC}"

# 1. Install Build Dependencies
log "Step 1/6: Installing system dependencies via APT..."
apt-get update -qq
apt-get install -y git cmake make libzmq3-dev libcjson-dev libcurl4-openssl-dev python3-venv \
    autoconf automake libtool pkg-config git autoconf-archive libtool libusb-1.0-0-dev libfftw3-dev \
    python3-gi python3-gst-1.0 gobject-introspection gir1.2-gstreamer-1.0 \
    gir1.2-gst-plugins-base-1.0 gir1.2-gst-plugins-bad-1.0 gstreamer1.0-tools \
    gstreamer1.0-plugins-base gstreamer1.0-plugins-good gstreamer1.0-plugins-bad \
    gstreamer1.0-plugins-ugly gstreamer1.0-libav gstreamer1.0-nice libnice10 libopus-dev

# 2. Install libgpiod v2 from source
log "Step 2/6: Building and installing libgpiod v2..."
cd "$BUILD_DIR"
git clone https://git.kernel.org/pub/scm/libs/libgpiod/libgpiod.git --quiet
cd libgpiod
./autogen.sh > /dev/null
./configure > /dev/null
make > /dev/null
make install > /dev/null
ldconfig
timedatectl set-ntp true

# 3. Install kalibrate-hackrf from source
log "Step 3/6: Building and installing kalibrate-hackrf..."
cd "$BUILD_DIR"
git clone https://github.com/scateu/kalibrate-hackrf.git --quiet
cd kalibrate-hackrf
./bootstrap > /dev/null
./configure > /dev/null
make > /dev/null
make install > /dev/null

# ---------------------------------------------------------
# 4. Environment Setup & Init System
# ---------------------------------------------------------
log "Step 4/6: Setting up Python environment..."

cd "$PROJECT_DIR"

if [ ! -d "venv" ]; then
    log_sub "Creating Python virtual environment (venv)..."
    python3 -m venv venv
fi

log_sub "Installing requirements..."
source venv/bin/activate
pip install --upgrade pip --quiet
[ -f "requirements.txt" ] && pip install -r requirements.txt
[ -f "build.sh" ] && { chmod +x build.sh; ./build.sh; }
deactivate

# CRITICAL: Adjust permissions BEFORE running init_sys.py
# This allows 'anepi' to create the 'daemons/' directory
log_sub "Applying directory permissions to $TARGET_USER..."
chown -R "$TARGET_USER":"$TARGET_GROUP" "$PROJECT_DIR"
chmod -R 755 "$PROJECT_DIR"

# RUN SYSTEM INITIALIZATION AS 'anepi'
if [ -f "init_sys.py" ]; then
    log "Running System Initialization (init_sys.py) as $TARGET_USER..."
    sudo -u "$TARGET_USER" "$PROJECT_DIR/venv/bin/python3" "$PROJECT_DIR/init_sys.py"
    log_sub "Initialization complete."
else
    log_warn "init_sys.py not found in $PROJECT_DIR, skipping..."
fi

# ---------------------------------------------------------
# 4.5. Cleanup Legacy Services
# ---------------------------------------------------------
log "Step 4.5/6: Erasing legacy daemons..."
LEGACY_SVCS=("monraf-client.service" "orchestrator-realtime.service" "monraf-main.service" "client-psd-gps.service")

for svc in "${LEGACY_SVCS[@]}"; do
    if systemctl list-unit-files "$svc" >/dev/null 2>&1; then
        log_sub "Stopping and removing $svc..."
        systemctl stop "$svc" 2>/dev/null || true
        systemctl disable "$svc" 2>/dev/null || true
        rm -f "/etc/systemd/system/$svc"
        rm -f "/etc/systemd/system/multi-user.target.wants/$svc"
    fi
done

systemctl daemon-reload
systemctl reset-failed

# Set executable permissions for binary apps
[ -f "$PROJECT_DIR/rf_app" ] && chmod +x "$PROJECT_DIR/rf_app"
[ -f "$PROJECT_DIR/ltegps_app" ] && chmod +x "$PROJECT_DIR/ltegps_app"

# ---------------------------------------------------------
# 5. Install Daemons (Systemd) - Must be root
# ---------------------------------------------------------
log "Step 5/6: Installing Systemd Services & Timers..."
DAEMONS_DIR="$PROJECT_DIR/daemons"

if [ -d "$DAEMONS_DIR" ]; then
    cd "$DAEMONS_DIR"
    shopt -s nullglob
    FILES=(*.service *.timer)
    shopt -u nullglob

    for f in "${FILES[@]}"; do
        log_sub "Deploying $f..."
        cp "$f" /etc/systemd/system/
        systemctl daemon-reload
        systemctl enable "$f"
        systemctl restart "$f"
    done
fi

# ---------------------------------------------------------
# 6. Shared Memory Configuration (anepi & root)
# ---------------------------------------------------------
log "Step 6/6: Configuring persistent shared memory..."

SHM_FILE="/dev/shm/persistent.json"
if [ ! -f "$SHM_FILE" ]; then
    echo "{}" > "$SHM_FILE"
fi

# Ownership to anepi, but 666 allows root and others to read/write
chown "$TARGET_USER":"$TARGET_GROUP" "$SHM_FILE"
chmod 666 "$SHM_FILE"
log_sub "Shared memory file $SHM_FILE is now R/W for anepi and root."

echo -e "\n${GREEN}   INSTALLATION COMPLETE SUCCESSFULLY         ${NC}"