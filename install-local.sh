#!/bin/bash

# install-local.sh
# Instalación local mínima para desarrollo/debug:
# - Instala dependencias del sistema (APT)
# - Recrea venv con acceso a paquetes del sistema
# - Instala dependencias Python del proyecto
#
# NO hace:
# - gestión de servicios
# - instalación de daemon/systemd
# - reboot
# - init_sys.py

set -euo pipefail

GREEN='\033[1;32m'; CYAN='\033[0;36m'; RED='\033[0;31m'; NC='\033[0m'

log() { echo -e "\n${GREEN}[INSTALL-LOCAL]${NC} $1"; }
log_sub() { echo -e "   ${CYAN}->${NC} $1"; }

if [ "${EUID}" -ne 0 ]; then
  echo -e "${RED}Error: Ejecutar como root (requiere apt-get).${NC}"
  exit 1
fi

PROJECT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "${PROJECT_DIR}"

log "Step 1/3: Instalando dependencias APT necesarias"
apt-get update -qq
apt-get install -y --reinstall ca-certificates > /dev/null
update-ca-certificates --fresh > /dev/null

apt-get install -y \
  git cmake make \
  libzmq3-dev libcjson-dev libcurl4-openssl-dev \
  python3-venv python3-dev \
  autoconf automake libtool pkg-config autoconf-archive \
  libusb-1.0-0-dev libfftw3-dev libopus-dev libcairo2-dev gyp \
  python3-gi python3-gst-1.0 python3-dotenv python3-zmq python3-websockets \
  gobject-introspection \
  gir1.2-gstreamer-1.0 gir1.2-gst-plugins-base-1.0 gir1.2-gst-plugins-bad-1.0 \
  gstreamer1.0-tools gstreamer1.0-plugins-base gstreamer1.0-plugins-good \
  gstreamer1.0-plugins-bad gstreamer1.0-plugins-ugly gstreamer1.0-libav \
  gstreamer1.0-nice libnice10 \
  python3-pyqt5 python3-pyqtgraph python3-scipy > /dev/null

log "Step 2/3: Recreando entorno virtual con paquetes del sistema"
if [ -d "venv" ]; then
  log_sub "Borrando venv anterior"
  rm -rf venv
fi

python3 -m venv --system-site-packages venv
# shellcheck disable=SC1091
source venv/bin/activate

log "Step 3/3: Instalando paquetes Python del proyecto"
pip install --upgrade pip certifi --quiet
if [ -f "requirements.txt" ]; then
  pip install -r requirements.txt --quiet
fi

deactivate

log "Completado. Sin reboot, sin daemons, sin systemd."
log_sub "Siguiente paso: source venv/bin/activate"
