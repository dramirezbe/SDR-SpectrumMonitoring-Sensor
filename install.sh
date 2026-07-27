#!/bin/bash

# install.sh
# Script de instalación para el proyecto ANE2 en Raspberry Pi 5.
#
# Versión endurecida para despliegue remoto sobre LTE:
#   - Construye TODO antes de detener servicios (minimiza tiempo fuera de servicio).
#   - Si algo falla, los servicios detenidos se reinician automáticamente.
#   - Reintentos solo en errores transitorios de red (no en errores deterministas).
#   - venv se reutiliza si requirements.txt no cambió.
#   - Todo queda registrado en Logs/install_*.log
#
# Uso:
#   sudo ./install.sh              # instala y reinicia al finalizar (comportamiento histórico)
#   sudo ./install.sh --no-reboot  # instala, reinicia servicios, pero no reinicia el equipo

set -euo pipefail

# Colores y Configuración
GREEN='\033[1;32m'; CYAN='\033[0;36m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
TARGET_USER="${USER}"; TARGET_GROUP="${USER}"
REBOOT=1
if [ "${1:-}" = "--no-reboot" ]; then
    REBOOT=0
fi

if [ "$EUID" -ne 0 ]; then
  echo -e "${RED}Error: Ejecutar como root.${NC}"; exit 1
fi

PROJECT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
BUILD_DIR=$(mktemp -d)
STOPPED_SERVICES=""
trap 'rm -rf "$BUILD_DIR"' EXIT

mkdir -p "$PROJECT_DIR/Logs"
INSTALL_LOG="$PROJECT_DIR/Logs/install_$(date +%Y%m%d_%H%M%S).log"
exec > >(tee -a "$INSTALL_LOG") 2>&1

log() { echo -e "\n${GREEN}[INSTALL]${NC} $1"; }
log_sub() { echo -e "   ${CYAN}->${NC} $1"; }
log_warn() { echo -e "   ${YELLOW}[SKIP]${NC} $1"; }
log_err() { echo -e "   ${RED}[ERROR]${NC} $1"; }

# Salida fatal controlada: reinicia los servicios que hayamos detenido antes de morir.
fatal() {
    log_err "$1"
    if [ -n "$STOPPED_SERVICES" ]; then
        log_err "Restaurando servicios detenidos antes de abortar..."
        for svc in $STOPPED_SERVICES; do
            systemctl start "$svc" 2>/dev/null || true
        done
    fi
    log_err "Instalación abortada. El sensor mantiene la versión anterior. Log: $INSTALL_LOG"
    exit 1
}

# Reintentos SOLO para fallos transitorios (red). Código de salida 2 = error
# determinista (no tiene sentido reintentar, ej. conflicto de dependencias).
retry_cmd() {
    local desc="$1"
    shift

    local attempt rc=0
    local max_attempts=4
    local delay_s=5

    for attempt in $(seq 1 "$max_attempts"); do
        if "$@"; then
            return 0
        else
            rc=$?
        fi

        if [ "$rc" -eq 2 ]; then
            log_err "$desc: error no recuperable, no se reintenta."
            return 2
        fi
        if [ "$attempt" -lt "$max_attempts" ]; then
            log_sub "$desc falló (intento $attempt/$max_attempts). Reintentando en ${delay_s}s..."
            sleep "$delay_s"
        fi
    done

    return "$rc"
}

# pip: distingue fallos de red (reintentables) de resolución de dependencias (fatales).
pip_install_requirements() {
    local out rc
    if out=$("$VENV_PY" -m pip install -r requirements.txt 2>&1); then
        return 0
    fi
    rc=$?
    echo "$out"
    if echo "$out" | grep -qiE "ResolutionImpossible|conflicting dependencies|No matching distribution|Could not find a version"; then
        return 2
    fi
    return 1
}

# ---------------------------------------------------------
# 1. Dependencias del sistema (SIN detener servicios todavía)
# ---------------------------------------------------------
log "Step 1/7: Instalando dependencias del sistema..."
export DEBIAN_FRONTEND=noninteractive

retry_cmd "apt-get update" \
    apt-get update -qq || fatal "apt-get update falló: no hay red estable. Instalación abortada SIN tocar servicios."

retry_cmd "reinstalar ca-certificates" \
    apt-get install -y --reinstall ca-certificates || log_warn "ca-certificates no reinstalado; se continúa."
update-ca-certificates --fresh > /dev/null || true

retry_cmd "apt-get install dependencias" \
    apt-get install -y git cmake make libzmq3-dev libcjson-dev libcurl4-openssl-dev python3-venv \
    python3-numpy autoconf automake libtool pkg-config autoconf-archive libusb-1.0-0-dev libfftw3-dev \
    python3-gi python3-gst-1.0 python3-dotenv python3-zmq python3-websockets gobject-introspection \
    gir1.2-gstreamer-1.0 gir1.2-gst-plugins-base-1.0 gir1.2-gst-plugins-bad-1.0 gstreamer1.0-tools \
    gstreamer1.0-plugins-base gstreamer1.0-plugins-good gstreamer1.0-plugins-bad \
    gstreamer1.0-plugins-ugly gstreamer1.0-libav gstreamer1.0-nice libnice10 libopus-dev \
    libcairo2-dev python3-dev gyp || fatal "No se pudieron instalar las dependencias apt."

timedatectl set-ntp true || true

# ---------------------------------------------------------
# 2. Hardware Builds (Conditional)
# ---------------------------------------------------------
log "Step 2/7: Verificando librerías de hardware..."
if ! command -v gpiodetect >/dev/null 2>&1; then
    log_sub "Compilando libgpiod v2 (tag v2.1.3)..."
    retry_cmd "clonar libgpiod" \
        git clone --depth 1 --branch v2.1.3 https://git.kernel.org/pub/scm/libs/libgpiod/libgpiod.git "$BUILD_DIR/libgpiod" --quiet \
        || fatal "No se pudo clonar libgpiod."
    cd "$BUILD_DIR/libgpiod"
    ./autogen.sh >/dev/null && ./configure >/dev/null && make >/dev/null && make install >/dev/null && ldconfig \
        || fatal "Falló la compilación de libgpiod."
else
    log_warn "libgpiod detectado."
fi

if ! command -v kal >/dev/null 2>&1; then
    log_sub "Compilando kalibrate-hackrf..."
    retry_cmd "clonar kalibrate-hackrf" \
        git clone --depth 1 https://github.com/scateu/kalibrate-hackrf.git "$BUILD_DIR/kalibrate-hackrf" --quiet \
        || fatal "No se pudo clonar kalibrate-hackrf."
    cd "$BUILD_DIR/kalibrate-hackrf"
    ./bootstrap >/dev/null && ./configure >/dev/null && make >/dev/null && make install >/dev/null \
        || fatal "Falló la compilación de kalibrate-hackrf."
else
    log_warn "kalibrate-hackrf detectado."
fi

# ---------------------------------------------------------
# 3. Entorno Python (idempotente) y compilación de binarios
# ---------------------------------------------------------
log "Step 3/7: Entorno Python y Binarios..."
cd "$PROJECT_DIR"
VENV_PY="$PROJECT_DIR/venv/bin/python3"

# Fuerza el uso del bundle CA del sistema y da margen extra a errores TLS transitorios.
export PIP_DISABLE_PIP_VERSION_CHECK=1
export PIP_DEFAULT_TIMEOUT=60
export PIP_RETRIES=5
export PIP_NO_INPUT=1
export SSL_CERT_FILE="/etc/ssl/certs/ca-certificates.crt"
export REQUESTS_CA_BUNDLE="/etc/ssl/certs/ca-certificates.crt"

REQ_HASH=$(sha256sum requirements.txt | awk '{print $1}')
VENV_STAMP="$PROJECT_DIR/venv/.requirements.sha256"

if [ -x "$VENV_PY" ] && [ -f "$VENV_STAMP" ] && [ "$(cat "$VENV_STAMP")" = "$REQ_HASH" ]; then
    log_warn "venv vigente (requirements.txt sin cambios). Se omite reinstalación."
else
    log_sub "Creando venv e instalando requirements..."
    rm -rf "venv"
    python3 -m venv --system-site-packages venv || fatal "No se pudo crear el venv."

    retry_cmd "Bootstrap de pip/certifi" \
        "$VENV_PY" -m pip install --upgrade pip certifi --quiet || \
        log_warn "No se pudo actualizar pip/certifi. Se continuará con el pip del venv."

    retry_cmd "Instalación de requirements.txt" \
        pip_install_requirements || fatal "requirements.txt no se pudo instalar. Revise el log arriba (conflicto de versiones o red)."

    echo "$REQ_HASH" > "$VENV_STAMP"
fi

if [ -f "build.sh" ]; then
    chmod +x build.sh
    ./build.sh || fatal "La compilación de los binarios C falló."
fi

[ -x "rf_app" ] && [ -x "ltegps_app" ] || fatal "No se generaron rf_app/ltegps_app."

# ---------------------------------------------------------
# 4. AHORA SÍ: detener servicios (ventana mínima de caída)
# ---------------------------------------------------------
log "Step 4/7: Deteniendo servicios (-ane2) para actualizar..."
SERVICES=$(systemctl list-units --type=service --state=active --full --no-legend | grep "\-ane2\.service" | grep -v "ltegps-ane2\.service" | awk '{print $1}') || true

if [ -n "$SERVICES" ]; then
    for svc in $SERVICES; do
        log_sub "Deteniendo $svc..."
        if systemctl stop "$svc"; then
            STOPPED_SERVICES="$STOPPED_SERVICES $svc"
        fi
    done
fi

log "Ajustes de firmware y servicios del sistema"
if [ -f "deactivate_service.sh" ]; then
    chmod +x deactivate_service.sh
    ./deactivate_service.sh || log_warn "deactivate_service.sh reportó un problema no fatal."
fi

# ---------------------------------------------------------
# 5. Shared Memory
# ---------------------------------------------------------
log "Step 5/7: Configurando memoria compartida..."
SHM_FILE="/dev/shm/persistent.json"
if [ ! -f "$SHM_FILE" ]; then echo "{}" > "$SHM_FILE"; fi
chown "$TARGET_USER":"$TARGET_GROUP" "$SHM_FILE"
chmod 660 "$SHM_FILE"

# ---------------------------------------------------------
# 6. Permisos, registro systemd y rearranque
# ---------------------------------------------------------
log "Step 6/7: Registrando servicios..."
[ -f "init_sys.py" ] || fatal "No existe init_sys.py."
sudo -u "$TARGET_USER" "$VENV_PY" "$PROJECT_DIR/init_sys.py" --user "$TARGET_USER" \
    || fatal "init_sys.py falló (¿dependencias Python incompletas?)."

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

# Permisos dirigidos (no 755 recursivo a ciegas)
cd "$PROJECT_DIR"
chown -R "$TARGET_USER":"$TARGET_GROUP" "$PROJECT_DIR"
find "$PROJECT_DIR" -path "$PROJECT_DIR/venv" -prune -o -type d -exec chmod 755 {} +
find "$PROJECT_DIR" -path "$PROJECT_DIR/venv" -prune -o -type f -exec chmod 644 {} +
chmod 755 *.sh rf_app ltegps_app 2>/dev/null || true
if [ -f ".env" ]; then
    chmod 600 ".env"
fi

#verificar permisos de /tmp y borrar archivos de lock
chmod 1777 /tmp
rm -f /tmp/*.lock

# Rearranque inmediato de los servicios (no dependemos del reboot para operar)
for svc in $STOPPED_SERVICES; do
    log_sub "Iniciando $svc..."
    systemctl start "$svc" || log_warn "$svc no arrancó; revise con: journalctl -u $svc -n 50"
done
STOPPED_SERVICES=""

# ---------------------------------------------------------
# 7. Finalización
# ---------------------------------------------------------
log "Step 7/7: Finalizando..."
echo -e "\n${GREEN}>>> INSTALACIÓN COMPLETADA <<<${NC}"
echo -e "${GREEN}>>> Log completo: $INSTALL_LOG <<<${NC}"

if [ "$REBOOT" -eq 1 ]; then
    echo -e "${YELLOW}>>> Reiniciando en 10s (Ctrl-C para cancelar el reinicio) <<<${NC}"
    sleep 10
    reboot
else
    echo -e "${YELLOW}>>> Sin reinicio (--no-reboot). NOTA: ltegps-ane2 sigue con el binario anterior;${NC}"
    echo -e "${YELLOW}>>> para activar el nuevo: sudo systemctl restart ltegps-ane2.service (caída breve de LTE)${NC}"
fi
