#!/bin/bash

# Exit immediately if a command exits with a non-zero status
set -e

# Variable for the project directory
PROJECT_DIR="/home/anepi/SDR-SpectrumMonitoring-Sensor"

# Helper function for printing status
log() {
    echo -e "\n\033[1;32m[INSTALL] $1\033[0m"
}

log "Starting installation..."

# ---------------------------------------------------------
# 1. Install Build Dependencies
# ---------------------------------------------------------
log "Installing system dependencies..."
sudo apt update
sudo apt install -y libzmq3-dev libcjson-dev libcurl4-openssl-dev python3-venv \
    autoconf automake libtool pkg-config git autoconf-archive

# ---------------------------------------------------------
# 2. Install libgpiod v2 from source
# ---------------------------------------------------------
log "Building and installing libgpiod v2..."
cd ~

# Check if directory exists to avoid git errors
if [ -d "libgpiod" ]; then
    echo "libgpiod directory exists, pulling latest changes..."
    cd libgpiod
    git pull
else
    git clone https://git.kernel.org/pub/scm/libs/libgpiod/libgpiod.git
    cd libgpiod
fi

./autogen.sh
./configure
make
sudo make install
sudo ldconfig  # Refresh shared library cache

# ---------------------------------------------------------
# 3. Install venv and project
# ---------------------------------------------------------
log "Setting up Python environment and compiling project C code..."

if [ ! -d "$PROJECT_DIR" ]; then
    echo "Error: Project directory $PROJECT_DIR not found!"
    exit 1
fi

cd "$PROJECT_DIR"

# Create venv if it doesn't exist
if [ ! -d "venv" ]; then
    python3 -m venv venv
fi

# Activate venv, install requirements, and deactivate
source venv/bin/activate
pip install --upgrade pip
if [ -f "requirements.txt" ]; then
    pip install -r requirements.txt
else
    echo "Warning: requirements.txt not found."
fi
deactivate

# Run build script
if [ -f "build.sh" ]; then
    chmod +x build.sh
    ./build.sh
else
    echo "Error: build.sh not found in $PROJECT_DIR"
    exit 1
fi

# Set executable permissions for apps
chmod +x "$PROJECT_DIR/rf_app"
chmod +x "$PROJECT_DIR/ltegps_app"

# ---------------------------------------------------------
# 4. Install Daemons (Systemd)
# ---------------------------------------------------------
log "Installing and enabling systemd services..."

# Navigate to daemons folder
if [ -d "$PROJECT_DIR/daemons" ]; then
    cd "$PROJECT_DIR/daemons"
    
    # Copy service files
    sudo cp lte-gps-client.service /etc/systemd/system/
    sudo cp rf-client.service /etc/systemd/system/
    sudo cp orchestrator.service /etc/systemd/system/

    # Reload systemd
    sudo systemctl daemon-reload

    # Enable services
    sudo systemctl enable lte-gps-client.service
    sudo systemctl enable rf-client.service
    sudo systemctl enable orchestrator.service

    # Start services
    sudo systemctl start lte-gps-client.service
    sudo systemctl start rf-client.service
    sudo systemctl start orchestrator.service
else
    echo "Error: 'daemons' directory not found inside $PROJECT_DIR"
    exit 1
fi

log "Installation complete! All services have been started."