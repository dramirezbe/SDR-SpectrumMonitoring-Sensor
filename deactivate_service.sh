#!/usr/bin/env bash

BOOT_CFG="firmware.txt"
DEST_CFG="/boot/firmware/config.txt"

# Backup del archivo destino original
timestamp="$(date +%Y%m%d_%H%M%S)"
cp -a "${DEST_CFG}" "${DEST_CFG}.bak_${timestamp}"

# Funciones operando sobre firmware.txt
ensure_kv() {
  if grep -Eq "^[[:space:]]*$1=" "${BOOT_CFG}"; then
    sed -i -E "s|^[[:space:]]*$1=.*|$1=$2|" "${BOOT_CFG}"
  else
    echo "$1=$2" >> "${BOOT_CFG}"
  fi
}

ensure_line() {
  if ! grep -Fxq "$1" "${BOOT_CFG}"; then
    echo "$1" >> "${BOOT_CFG}"
  fi
}

ensure_kv "force_turbo" "0"
ensure_kv "start_x" "0"
ensure_line "dtoverlay=disable-bt"

# Desactivar servicios
for svc in bluetooth.service hciuart.service avahi-daemon.service triggerhappy.service cups.service cups-browsed.service; do
  systemctl disable --now "${svc}" 2>/dev/null || true
done

# Priorizar IPv4
sed -i "s|#precedence ::ffff:0:0/96  100|precedence ::ffff:0:0/96  100|" /etc/gai.conf || true

# Reemplazar config final
sudo mv "${BOOT_CFG}" "${DEST_CFG}"

echo "[DONE] firmware.txt movido a ${DEST_CFG}. Ejecuta sudo reboot."