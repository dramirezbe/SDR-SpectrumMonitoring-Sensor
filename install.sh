#!/bin/bash

# =========================================================
#  SYSTEM INSTALLATION SCRIPT
# =========================================================

set -e

# ---------------------------------------------------------
# Visual Configuration
# ---------------------------------------------------------
RED='\033[0;31m'
GREEN='\033[1;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

TARGET_USER="anepi"
TARGET_GROUP="anepi"

if [ "$EUID" -ne 0 ]; then
  echo -e "${RED}Error: This script must be run as root.${NC}"
  exit 1
fi

PROJECT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
BUILD_DIR=$(mktemp -d)

log() { echo -e "\n${GREEN}[INSTALL]${NC} $1"; }
log_sub() { echo -e "   ${CYAN}->${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARNING] $1${NC}"; }

cleanup() { [ -d "$BUILD_DIR" ] && rm -rf "$BUILD_DIR"; }
trap cleanup EXIT

# ---------------------------------------------------------
# 1-3. Dependencies & Builds
# ---------------------------------------------------------
log "Step 1/6: Installing system dependencies..."
apt-get update -qq
apt-get install -y git cmake make libzmq3-dev libcjson-dev libcurl4-openssl-dev python3-venv \
    autoconf automake libtool pkg-config git autoconf-archive libtool libusb-1.0-0-dev libfftw3-dev \
    python3-gi python3-gst-1.0 python3-dotenv python3-zmq python3-websockets gobject-introspection gir1.2-gstreamer-1.0 \
    gir1.2-gst-plugins-base-1.0 gir1.2-gst-plugins-bad-1.0 gstreamer1.0-tools \
    gstreamer1.0-plugins-base gstreamer1.0-plugins-good gstreamer1.0-plugins-bad \
    gstreamer1.0-plugins-ugly gstreamer1.0-libav gstreamer1.0-nice libnice10 libopus-dev

log "Step 2/6: Building libgpiod v2..."
cd "$BUILD_DIR"
git clone https://git.kernel.org/pub/scm/libs/libgpiod/libgpiod.git --quiet
cd libgpiod && ./autogen.sh > /dev/null && ./configure > /dev/null && make > /dev/null && make install > /dev/null
ldconfig
timedatectl set-ntp true

log "Step 3/6: Building kalibrate-hackrf..."
cd "$BUILD_DIR"
git clone https://github.com/scateu/kalibrate-hackrf.git --quiet
cd kalibrate-hackrf && ./bootstrap > /dev/null && ./configure > /dev/null && make > /dev/null && make install > /dev/null

# ---------------------------------------------------------
# 4. Environment & Init
# ---------------------------------------------------------
log "Step 4/6: Setting up Python environment..."
cd "$PROJECT_DIR"
[ ! -d "venv" ] && python3 -m venv --system-site-packages venv 
source venv/bin/activate
pip install --upgrade pip --quiet
[ -f "requirements.txt" ] && pip install -r requirements.txt
[ -f "build.sh" ] && { chmod +x build.sh; ./build.sh; }
deactivate

log_sub "Applying directory permissions to $TARGET_USER..."
chown -R "$TARGET_USER":"$TARGET_GROUP" "$PROJECT_DIR"
chmod -R 755 "$PROJECT_DIR"

if [ -f "init_sys.py" ]; then
    log "Running System Initialization as $TARGET_USER..."
    sudo -u "$TARGET_USER" "$PROJECT_DIR/venv/bin/python3" "$PROJECT_DIR/init_sys.py"
fi

# ---------------------------------------------------------
# 5. Shared Memory Setup (IMPORTANT: MUST BE BEFORE STEP 6)
# ---------------------------------------------------------
log "Step 5/6: Configuring persistent shared memory..."
SHM_FILE="/dev/shm/persistent.json"
if [ ! -f "$SHM_FILE" ]; then echo "{}" > "$SHM_FILE"; fi
chown "$TARGET_USER":"$TARGET_GROUP" "$SHM_FILE"
chmod 666 "$SHM_FILE"

# ---------------------------------------------------------
# 6. Install Daemons
# ---------------------------------------------------------
log "Step 6/6: Installing Systemd Services & Timers..."

# Cleanup Legacy
LEGACY_SVCS=("monraf-client.service" "orchestrator-realtime.service" "monraf-main.service" "client-psd-gps.service")
for svc in "${LEGACY_SVCS[@]}"; do
    if systemctl list-unit-files "$svc" >/dev/null 2>&1; then
        systemctl stop "$svc" 2>/dev/null || true
        systemctl disable "$svc" 2>/dev/null || true
        rm -f "/etc/systemd/system/$svc"
    fi
done

[ -f "$PROJECT_DIR/rf_app" ] && chmod +x "$PROJECT_DIR/rf_app"
[ -f "$PROJECT_DIR/ltegps_app" ] && chmod +x "$PROJECT_DIR/ltegps_app"

DAEMONS_DIR="$PROJECT_DIR/daemons"
if [ -d "$DAEMONS_DIR" ]; then
    cd "$DAEMONS_DIR"
    shopt -s nullglob
    FILES=(*.service *.timer)
    shopt -u nullglob

    for f in "${FILES[@]}"; do
        log_sub "Deploying and enabling $f..."
        cp "$f" /etc/systemd/system/
        systemctl daemon-reload
        systemctl enable "$f"
        systemctl restart "$f"
    done
fi

echo -e "\n${GREEN}   INSTALLATION COMPLETE SUCCESSFULLY         ${NC}"