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
if [ -n "$SUDO_USER" ]; then
    REAL_USER="$SUDO_USER"
    REAL_GROUP=$(id -gn "$SUDO_USER")
else
    REAL_USER="$USER"
    REAL_GROUP=$(id -gn "$USER")
fi

if [ "$EUID" -ne 0 ]; then
  echo -e "${RED}Error: This script must be run as root.${NC}"
  echo -e "${YELLOW}Usage: sudo ./install.sh${NC}"
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
    python3-gi \
  python3-gst-1.0 \
  gobject-introspection \
  gir1.2-gstreamer-1.0 \
  gir1.2-gst-plugins-base-1.0 \
  gir1.2-gst-plugins-bad-1.0 \
  gstreamer1.0-tools \
  gstreamer1.0-plugins-base \
  gstreamer1.0-plugins-good \
  gstreamer1.0-plugins-bad \
  gstreamer1.0-plugins-ugly \
  gstreamer1.0-libav \
  gstreamer1.0-nice \
  libnice10 \
  libopus-dev

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
# 4. Install venv, project, and Init System
# ---------------------------------------------------------
log "Step 4/6: Setting up Python environment..."

cd "$PROJECT_DIR"

if [ ! -d "venv" ]; then
    log_sub "Creating Python virtual environment (venv)..."
    python3 -m venv venv
fi

log_sub "Activating venv and installing requirements..."
source venv/bin/activate
pip install --upgrade pip --quiet

if [ -f "requirements.txt" ]; then
    pip install -r requirements.txt
fi

if [ -f "build.sh" ]; then
    log_sub "Executing local build.sh..."
    chmod +x build.sh
    ./build.sh
fi

# Deactivate the venv shell session
log_sub "Deactivating virtual environment session..."
deactivate

# RUN SYSTEM INITIALIZATION SCRIPT
if [ -f "init_sys.py" ]; then
    log "Running System Initialization (init_sys.py)..."
    # Use the python executable from the venv to run the script synchronously
    "$PROJECT_DIR/venv/bin/python3" "$PROJECT_DIR/init_sys.py"
    log_sub "Initialization complete."
else
    log_warn "init_sys.py not found in $PROJECT_DIR, skipping..."
fi

# Set executable permissions for binary apps
[ -f "$PROJECT_DIR/rf_app" ] && chmod +x "$PROJECT_DIR/rf_app"
[ -f "$PROJECT_DIR/ltegps_app" ] && chmod +x "$PROJECT_DIR/ltegps_app"

# ---------------------------------------------------------
# 5. Install Daemons (Systemd)
# ---------------------------------------------------------
log "Step 5/6: Installing Systemd Services & Timers..."
DAEMONS_DIR="$PROJECT_DIR/daemons"

if [ -d "$DAEMONS_DIR" ]; then
    cd "$DAEMONS_DIR"
    shopt -s nullglob
    FILES=(*.service *.timer)
    shopt -u nullglob

    for f in "${FILES[@]}"; do
        cp "$f" /etc/systemd/system/
        log "Copying daemons to systemd $f..."
        systemctl daemon-reload
        systemctl enable "$f"
        systemctl restart "$f"
    done
fi

# ---------------------------------------------------------
# 6. Final Permissions
# ---------------------------------------------------------
log "Step 6/6: Applying Final Permissions for user '$REAL_USER'..."
chown -R "$REAL_USER":"$REAL_GROUP" "$PROJECT_DIR"
chmod -R 755 "$PROJECT_DIR" 

SHM_FILE="/dev/shm/persistent.json"
if [ ! -f "$SHM_FILE" ]; then
    echo "{}" > "$SHM_FILE"
fi
chown "$REAL_USER":"$REAL_GROUP" "$SHM_FILE"
chmod 666 "$SHM_FILE"

echo -e "\n${GREEN}   INSTALLATION COMPLETE SUCCESSFULLY         ${NC}"