#!/usr/bin/env bash

# deactivate_service.sh
# Ajustes de firmware y desactivación de servicios innecesarios en la RPi.
# Idempotente: solo reescribe /boot/firmware/config.txt si el contenido cambió.

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
BOOT_CFG="${SCRIPT_DIR}/firmware.txt"
DEST_CFG="/boot/firmware/config.txt"
MARKER="/etc/ane2_firmware_tuned.sha256"

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

CFG_HASH="$(sha256sum "${BOOT_CFG}" | awk '{print $1}')"

# Solo escribir en /boot si el contenido es distinto al aplicado la última vez
if [ -f "${MARKER}" ] && [ "$(cat "${MARKER}")" = "${CFG_HASH}" ] && [ -f "${DEST_CFG}" ]; then
  echo "[SKIP] firmware.txt sin cambios; ${DEST_CFG} ya está aplicado."
else
  timestamp="$(date +%Y%m%d_%H%M%S)"
  cp -a "${DEST_CFG}" "${DEST_CFG}.bak_${timestamp}" 2>/dev/null || true

  # tee escribe in-place: no intenta preservar ownership (FAT32) y no rompe hardlinks
  cat "${BOOT_CFG}" | tee "${DEST_CFG}" > /dev/null

  echo "${CFG_HASH}" > "${MARKER}"
  echo "[DONE] firmware.txt aplicado a ${DEST_CFG}. Se requiere reinicio para surtir efecto."
fi

# Desactivar servicios
for svc in bluetooth.service hciuart.service avahi-daemon.service triggerhappy.service cups.service cups-browsed.service; do
  systemctl disable --now "${svc}" 2>/dev/null || true
done

# Priorizar IPv4
sed -i "s|#precedence ::ffff:0:0/96  100|precedence ::ffff:0:0/96  100|" /etc/gai.conf || true
