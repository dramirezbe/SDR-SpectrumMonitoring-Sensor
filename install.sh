#!/bin/bash
set -e

# Colores y Configuración
GREEN='\033[1;32m'; CYAN='\033[0;36m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
TARGET_USER="anepi"; TARGET_GROUP="anepi"

if [ "$EUID" -ne 0 ]; then
  echo -e "${RED}Error: Ejecutar como root.${NC}"; exit 1
fi

PROJECT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
BUILD_DIR=$(mktemp -d)
trap 'rm -rf "$BUILD_DIR"' EXIT

log() { echo -e "\n${GREEN}[INSTALL]${NC} $1"; }
log_sub() { echo -e "   ${CYAN}->${NC} $1"; }
log_warn() { echo -e "   ${YELLOW}[SKIP]${NC} $1"; }

# ---------------------------------------------------------
# 1. Gestión de Servicios (Solo -ane2 | LTE Safe)
# ---------------------------------------------------------
log "Step 1/7: Deteniendo servicios activos (-ane2)..."
SERVICES=$(systemctl list-units --type=service --state=active --full --no-legend | grep "\-ane2\.service" | grep -v "ltegps-ane2\.service" | awk '{print $1}') || true

if [ -n "$SERVICES" ]; then
    for svc in $SERVICES; do
        log_sub "Deteniendo $svc..."
        systemctl stop "$svc"
    done
fi

# ---------------------------------------------------------
# 2. Dependencias Completas
# ---------------------------------------------------------
log "Step 2/7: Instalando todas las dependencias..."
apt-get update -qq
apt-get install -y --reinstall ca-certificates > /dev/null
update-ca-certificates --fresh > /dev/null

# Lista completa recuperada del script viejo
apt-get install -y git cmake make libzmq3-dev libcjson-dev libcurl4-openssl-dev python3-venv \
    autoconf automake libtool pkg-config autoconf-archive libusb-1.0-0-dev libfftw3-dev \
    python3-gi python3-gst-1.0 python3-dotenv python3-zmq python3-websockets gobject-introspection \
    gir1.2-gstreamer-1.0 gir1.2-gst-plugins-base-1.0 gir1.2-gst-plugins-bad-1.0 gstreamer1.0-tools \
    gstreamer1.0-plugins-base gstreamer1.0-plugins-good gstreamer1.0-plugins-bad \
    gstreamer1.0-plugins-ugly gstreamer1.0-libav gstreamer1.0-nice libnice10 libopus-dev \
    libcairo2-dev python3-dev gyp > /dev/null

timedatectl set-ntp true

# ---------------------------------------------------------
# 3. Hardware Builds (Conditional)
# ---------------------------------------------------------
log "Step 3/7: Verificando librerías de hardware..."
if ! command -v gpiodetect >/dev/null 2>&1; then
    log_sub "Compilando libgpiod v2..."
    cd "$BUILD_DIR" && git clone https://git.kernel.org/pub/scm/libs/libgpiod/libgpiod.git --quiet
    cd libgpiod && ./autogen.sh >/dev/null && ./configure >/dev/null && make >/dev/null && make install >/dev/null && ldconfig
else
    log_warn "libgpiod detectado."
fi

if ! command -v kal >/dev/null 2>&1; then
    log_sub "Compilando kalibrate-hackrf..."
    cd "$BUILD_DIR" && git clone https://github.com/scateu/kalibrate-hackrf.git --quiet
    cd kalibrate-hackrf && ./bootstrap >/dev/null && ./configure >/dev/null && make >/dev/null && make install >/dev/null
else
    log_warn "kalibrate-hackrf detectado."
fi

# ---------------------------------------------------------
# 4. Python, Binarios y Permisos
# ---------------------------------------------------------
log "Step 4/7: Entorno Python y Binarios..."
cd "$PROJECT_DIR"
rm -rf "venv"
python3 -m venv --system-site-packages venv 
source venv/bin/activate
pip install --upgrade pip certifi --quiet
[ -f "requirements.txt" ] && pip install -r requirements.txt --quiet
[ -f "build.sh" ] && { chmod +x build.sh; ./build.sh; }
log "deactivate services"
[ -f "deactivate_service.sh" ] && { chmod +x deactivate_service.sh; ./deactivate_service.sh; }
deactivate

# Asegurar ejecución de binarios C
[ -f "rf_app" ] && chmod +x "rf_app"
[ -f "ltegps_app" ] && chmod +x "ltegps_app"

chown -R "$TARGET_USER":"$TARGET_GROUP" "$PROJECT_DIR"
chmod -R 755 "$PROJECT_DIR"

#verificar permisos de /tmp y borrar archivos de lock
chmod 1777 /tmp
rm -f /tmp/*.lock

# ---------------------------------------------------------
# 5. Shared Memory (Recuperado)
# ---------------------------------------------------------
log "Step 5/7: Configurando memoria compartida..."
SHM_FILE="/dev/shm/persistent.json"
if [ ! -f "$SHM_FILE" ]; then echo "{}" > "$SHM_FILE"; fi
chown "$TARGET_USER":"$TARGET_GROUP" "$SHM_FILE"
chmod 666 "$SHM_FILE"

# ---------------------------------------------------------
# 6. Inicialización y Systemd
# ---------------------------------------------------------
log "Step 6/7: Registrando servicios..."
[ -f "init_sys.py" ] && sudo -u "$TARGET_USER" "$PROJECT_DIR/venv/bin/python3" "$PROJECT_DIR/init_sys.py"

DAEMONS_DIR="$PROJECT_DIR/daemons"
if [ -d "$DAEMONS_DIR" ]; then
    rm -f /etc/systemd/system/*-ane2.service
    rm -f /etc/systemd/system/*-ane2.timer
    cd "$DAEMONS_DIR"
    for f in *.service *.timer; do
        [ -e "$f" ] || continue
        cp "$f" /etc/systemd/system/
        systemctl enable "$f" > /dev/null 2>&1
        log_sub "$f habilitado."
    done
    systemctl daemon-reload
fi

log "Step 7/7: Finalizando..."
echo -e "\n${GREEN}>>> INSTALACIÓN COMPLETADA <<<${NC}"
sleep 2
reboot