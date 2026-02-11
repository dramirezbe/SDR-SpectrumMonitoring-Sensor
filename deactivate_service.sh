#!/usr/bin/env bash
# deactivate_service.sh
# Script para desactivar servicios y configuraciones específicas del proyecto ANE2.

set -euo pipefail

if [[ $EUID -ne 0 ]]; then
  echo "[FATAL] Ejecuta como root: sudo $0"
  exit 1
fi

BOOT_CFG="/boot/config.txt"
[[ -f /boot/firmware/config.txt ]] && BOOT_CFG="/boot/firmware/config.txt"

timestamp="$(date +%Y%m%d_%H%M%S)"
backup="${BOOT_CFG}.bak_${timestamp}"

echo "[INFO] Usando config: ${BOOT_CFG}"
echo "[INFO] Backup:      ${backup}"
cp -a "${BOOT_CFG}" "${backup}"

# Función: asegura key=value en config (si existe, reemplaza; si no, agrega)
ensure_kv() {
  local key="$1"
  local value="$2"
  if grep -Eq "^[[:space:]]*${key}=" "${BOOT_CFG}"; then
    sed -i -E "s|^[[:space:]]*${key}=.*|${key}=${value}|" "${BOOT_CFG}"
  else
    echo "${key}=${value}" >> "${BOOT_CFG}"
  fi
}

# Función: asegura que exista una línea exacta
ensure_line() {
  local line="$1"
  if ! grep -Fxq "${line}" "${BOOT_CFG}"; then
    echo "${line}" >> "${BOOT_CFG}"
  fi
}

echo "[STEP] (1) Disable turbo/boost (asegurar force_turbo=0)"
ensure_kv "force_turbo" "0"

echo "[STEP] (2) Disable camera stack (asegurar start_x=0)"
ensure_kv "start_x" "0"

echo "[STEP] (3) Disable Bluetooth (dtoverlay + apagar servicios)"
ensure_line "dtoverlay=disable-bt"

for svc in bluetooth.service hciuart.service; do
  if systemctl list-unit-files | grep -q "^${svc}"; then
    systemctl disable --now "${svc}" || true
    echo "  [OK] ${svc} disabled/stopped"
  else
    echo "  [SKIP] ${svc} no existe en este sistema"
  fi
done

echo "[STEP] (4) Disable services típicos nodo sensor"
SENSOR_SERVICES=(avahi-daemon.service triggerhappy.service cups.service cups-browsed.service)
for svc in "${SENSOR_SERVICES[@]}"; do
  if systemctl list-unit-files | grep -q "^${svc}"; then
    systemctl disable --now "${svc}" || true
    echo "  [OK] ${svc} disabled/stopped"
  else
    echo "  [SKIP] ${svc} no existe en este sistema"
  fi
done

echo "[STEP] (5) Disable IPv6 (Fix 'No route' errors)"
SYSCTL_CONF="/etc/sysctl.conf"
IPV6_PARAMS=(
  "net.ipv6.conf.all.disable_ipv6=1"
  "net.ipv6.conf.default.disable_ipv6=1"
  "net.ipv6.conf.lo.disable_ipv6=1"
)

for param in "${IPV6_PARAMS[@]}"; do
  if ! grep -Fq "$param" "$SYSCTL_CONF"; then
    echo "$param" >> "$SYSCTL_CONF"
  fi
done

# Aplicar cambios de red inmediatamente sin reiniciar
sysctl -p > /dev/null
echo "  [OK] IPv6 disabled and sysctl applied"

echo
echo "[DONE] Cambios aplicados."
echo "       - Se creó backup: ${backup}"
echo "       - IPv6 ya está desactivado (no requiere reinicio para esto)."
echo "       - Para que dtoverlay/start_x/force_turbo tomen efecto: REINICIA."
echo
echo "Reiniciar ahora: sudo reboot"