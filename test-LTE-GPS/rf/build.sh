#!/usr/bin/env bash
set -euo pipefail

# Directorio del script
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Compilador y flags
CC=gcc
CFLAGS="-Wall -Wextra -O2"
LDFLAGS="-lhackrf -lzmq -lcjson -lm -lpthread"

# Lista de fuentes C (ajusta según tus archivos reales)
SOURCES=(
  rf.c
  psd.c
  sdr_HAL.c
  ring_buffer.c
  zmqsub.c
  zmqpub.c
)

# Nombre del ejecutable
OUT=rf_engine

echo "Compilando ${OUT}..."
$CC $CFLAGS "${SOURCES[@]}" -o "$OUT" $LDFLAGS

echo
echo "✅ Build completado."
echo "Ejecutable generado: ./$OUT"
