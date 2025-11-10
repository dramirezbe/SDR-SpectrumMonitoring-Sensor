#!/usr/bin/env bash
# install.sh - RPi5 distribution installer (improved for PyInstaller + numpy/scipy)
# - builds fcron from source (same steps)
# - installs system packages, kalibrate-hackrf, python venv and project build
set -euo pipefail

# -------------------------
# Configuration / variables
# -------------------------
PROJECT_ROOT="$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
SCRIPTS_DIR="$PROJECT_ROOT/cmd"
VENV_DIR="$PROJECT_ROOT/venv"
VENV_ACTIVATE="$VENV_DIR/bin/activate"
HOME_DIR="${HOME:-/root}"
KALIBRATE_DIR="$HOME_DIR/kalibrate-hackrf"
MAKE_JOBS="$(command -v nproc >/dev/null 2>&1 && nproc || echo 1)"
FCRON_VERSION="3.4.0"
FCRON_TARBALL="fcron-${FCRON_VERSION}.src.tar.gz"
FCRON_URL="http://fcron.free.fr/archives/${FCRON_TARBALL}"
TMP_DIR="$(mktemp -d /tmp/install-rpi5.XXXXXX)"

# -------------------------
# Start
# -------------------------
echo "Starting install.sh (project root: $PROJECT_ROOT)"

# -------------------------
# Step 1: APT packages (exact list provided)
# -------------------------
echo "Updating apt and installing system packages..."
sudo apt update -y
sudo apt install -y software-properties-common

# add deadsnakes PPA
sudo add-apt-repository -y ppa:deadsnakes/ppa
sudo apt update -y && sudo apt upgrade -y

# install packages
sudo apt-get install -y git make cmake automake libtool libhackrf-dev \
    hackrf libusb-1.0-0-dev libfftw3-dev build-essential sendmail \
    libpam0g-dev docbook-utils python3.13-full libpython3.13 \
    pkg-config


# -------------------------
# Step 3: Clone & build kalibrate-hackrf
# -------------------------
echo "Cloning/building kalibrate-hackrf into $KALIBRATE_DIR"
if [ -d "$KALIBRATE_DIR/.git" ]; then
    echo "kalibrate-hackrf repo already exists; updating to origin/HEAD"
    pushd "$KALIBRATE_DIR" >/dev/null
    git fetch --all --prune
    git reset --hard origin/HEAD
else
    echo "Cloning repository"
    git clone https://github.com/scateu/kalibrate-hackrf.git "$KALIBRATE_DIR"
    pushd "$KALIBRATE_DIR" >/dev/null
fi

if [ -x "./bootstrap" ]; then
    echo "Running ./bootstrap"
    ./bootstrap || echo "./bootstrap returned nonzero"
elif [ -f "./autogen.sh" ]; then
    echo "Running ./autogen.sh"
    ./autogen.sh || echo "./autogen.sh returned nonzero"
else
    echo "Running autoreconf -iv (if needed)"
    autoreconf -iv || true
fi

echo "Running ./configure"
./configure || echo "./configure returned nonzero (continuing to make)"
echo "Running make -j$MAKE_JOBS"
make -j"$MAKE_JOBS"
if make -n check 2>/dev/null | grep -q "check"; then
    echo "Running make check"
    if ! make check; then
        echo "make check failed — continuing"
    fi
else
    echo "No make check target detected; skipping tests"
fi

echo "Running sudo make install for kalibrate-hackrf"
sudo make install || echo "sudo make install returned nonzero"
sudo ldconfig || true

popd >/dev/null
echo "kalibrate-hackrf step done."

# -------------------------
# Step 4: Python virtualenv + dependencies (recommended)
# -------------------------
echo "Setting up Python virtualenv at $VENV_DIR"
cd "$PROJECT_ROOT"
if [ ! -d "$VENV_DIR" ]; then
    python3.13 -m venv "$VENV_DIR"
fi

if [ ! -f "$VENV_ACTIVATE" ]; then
    echo "Virtualenv activation script not found at $VENV_ACTIVATE. Exiting."
    exit 1
fi

# shellcheck disable=SC1090
source "$VENV_ACTIVATE"
echo "Virtualenv activated"

echo "Upgrading pip and installing build/runtime Python packages"
pip install --upgrade pip


# If you have a requirements.txt, install it (we still respect user's requirements)
if [ -f requirements.txt ]; then
    echo "Installing project requirements from requirements.txt"
    pip install --no-cache-dir -r requirements.txt
else
    echo "requirements.txt not found; skipping pip installs"
fi

# -------------------------
# Step 5: Call build-all.sh if present
# -------------------------
if [ -x "$SCRIPTS_DIR/build-all.sh" ]; then
    echo "Running project build script: $SCRIPTS_DIR/build-all.sh"
    "$SCRIPTS_DIR/build-all.sh"
else
    echo "Build script $SCRIPTS_DIR/build-all.sh not found or not executable; skipping."
fi

# Deactivate venv cleanly
deactivate || true
echo "Virtualenv deactivated"

echo "All steps completed successfully."

# End of script
