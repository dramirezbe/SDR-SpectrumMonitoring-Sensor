#!/bin/bash

# ==============================================================================
# ANE2 Realtime - Installation Script
# Target: Raspberry Pi 5 (Debian/Bookworm)
# ==============================================================================

set -e  # Exit immediately if a command exits with a non-zero status

# --- Configuration ---
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${PROJECT_ROOT}/venv"
BINARY_PATH="${PROJECT_ROOT}/main"
DAEMON_DIR="${PROJECT_ROOT}/daemons"

# --- Colors for Output ---
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# --- Helper Functions ---
log_info() {
    echo -e "${YELLOW}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[OK]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

check_root() {
    if [ "$EUID" -ne 0 ]; then
        log_error "Please run this script as root (sudo ./install.sh)"
        exit 1
    fi
}

# --- Main Execution ---

main() {
    echo -e "==================================================="
    echo -e " Starting Installation for ANE2 Realtime System"
    echo -e "==================================================="

    # 1. Verify Permissions
    check_root

    # 2. Update and Install System Dependencies
    log_info "Updating package lists and installing system dependencies..."
    apt-get update -q
    apt-get install -y libzmq3-dev libcjson-dev libcurl4-openssl-dev python3-venv

    # 3. Setup Python Environment
    log_info "Setting up Python virtual environment..."
    if [ ! -d "$VENV_DIR" ]; then
        python3 -m venv "$VENV_DIR"
        log_success "Created virtual environment at $VENV_DIR"
    else
        log_info "Virtual environment already exists, skipping creation."
    fi

    log_info "Installing Python requirements..."
    # We call the venv pip directly to ensure packages go to the right place
    "${VENV_DIR}/bin/pip" install --upgrade pip
    "${VENV_DIR}/bin/pip" install -r "${PROJECT_ROOT}/requirements.txt"
    log_success "Python dependencies installed."

    # 4. Compile C Code
    log_info "Building C binaries..."
    if [ -f "${PROJECT_ROOT}/build.sh" ]; then
        chmod +x "${PROJECT_ROOT}/build.sh"
        
        # Execute build script
        # We assume build.sh handles its own directory context or is robust
        (cd "${PROJECT_ROOT}" && ./build.sh)
        
        # Ensure the resulting binary is executable
        if [ -f "$BINARY_PATH" ]; then
            chmod +x "$BINARY_PATH"
            log_success "Binary built and permissions set: $BINARY_PATH"
        else
            log_error "Binary not found at expected path: $BINARY_PATH"
            exit 1
        fi
    else
        log_error "build.sh not found in project root."
        exit 1
    fi

    # 5. Install Systemd Daemons
    log_info "Installing systemd services..."
    
    SERVICE_1="orchestrator-realtime.service"
    SERVICE_2="client-psd-gps.service"

    if [ -d "$DAEMON_DIR" ]; then
        cp "${DAEMON_DIR}/${SERVICE_1}" /etc/systemd/system/
        cp "${DAEMON_DIR}/${SERVICE_2}" /etc/systemd/system/
        log_success "Service files copied to /etc/systemd/system/"
    else
        log_error "Daemons directory not found at $DAEMON_DIR"
        exit 1
    fi

    # 6. Enable and Start Services
    log_info "Reloading systemd daemon..."
    systemctl daemon-reload

    log_info "Enabling and starting services..."
    
    # Enable
    systemctl enable "$SERVICE_1"
    systemctl enable "$SERVICE_2"

    # Start (Restarting ensures they pick up changes if already running)
    systemctl restart "$SERVICE_1"
    systemctl restart "$SERVICE_2"

    log_success "Services started."

    echo -e "==================================================="
    echo -e "${GREEN} Installation Complete! ${NC}"
    echo -e "==================================================="
    echo -e "Check status with:"
    echo -e "  sudo systemctl status $SERVICE_1"
    echo -e "  sudo systemctl status $SERVICE_2"
}

# Run Main
main