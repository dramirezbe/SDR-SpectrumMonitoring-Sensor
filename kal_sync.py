#!/usr/bin/env python3
# kal_sync.py

"""
Módulo de Sincronización de Frecuencia (Kalibrate).

Este script actúa como una utilidad de autocalibración para el SDR. Utiliza la 
herramienta externa `kalibrate-hackrf` para encontrar canales GSM y calcular 
el desplazamiento de frecuencia (PPM error) del oscilador local.
"""

import subprocess
import re
import time
import sys
import traceback

# Custom imports from your project
import cfg
from utils import ShmStore

log = cfg.set_logger()

# HackRF often needs gain to see signals clearly. Adjust 0-40 as needed.
DEFAULT_GAIN = "40" 
GLOBAL_TIMEOUT = 105  # 1 minute 45 seconds

def check_hackrf_status():
    """
    Verifica la disponibilidad del hardware HackRF.
    
    Returns:
        tuple: (bool, str) True si el hardware responde, y un mensaje descriptivo.
    """
    try:
        result = subprocess.run(['hackrf_info'], capture_output=True, text=True, timeout=10)
        output = (result.stdout + result.stderr).lower()
        if "busy" in output:
            return False, "HackRF is currently busy."
        if "not found" in output:
            return False, "No HackRF detected."
        return True, "HackRF Ready."
    except Exception as e:
        return False, f"Error checking HackRF: {str(e)}"

def run_kal_scan(band, deadline):
    """
    Escanea una banda GSM específica en busca de estaciones base.

    Args:
        band (str): Nombre de la banda (ej. 'GSM900').
        deadline (float): Timestamp límite para finalizar la operación.

    Returns:
        tuple: (list, bool) Lista de canales encontrados y un flag de timeout.
    """
    log.info(f"Scanning band: {band}")
    print(f"\n--- Scanning band: {band} ---")
    found_in_band = []
    
    cmd = ['kal', '-s', band, '-g', DEFAULT_GAIN]
    
    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        universal_newlines=True
    )

    try:
        while True:
            # Check Global Timeout
            if time.time() > deadline:
                log.warning(f"Global timeout reached during scan of {band}. Terminating.")
                process.terminate()
                return found_in_band, True # Return what we have + Timeout flag
            line = process.stdout.readline()
            if not line and process.poll() is not None:
                break
            
            if line:
                clean_line = line.strip()
                print(f"  [scan]: {clean_line}")
                match = re.search(r"chan:\s+(\d+).*power:\s+([\d.]+)", clean_line)
                if match:
                    found_in_band.append((match.group(1), float(match.group(2))))

    except Exception as e:
        log.error(f"Error during scan: {e}")
        process.kill()

    return found_in_band, False

def calibrate_channel(channel, deadline):
    """
    Calcula el error de frecuencia PPM usando un canal específico.

    Args:
        channel (str): Canal GSM detectado.
        deadline (float): Timestamp límite de seguridad.

    Returns:
        tuple: (bool, float, str, bool) Éxito, valor PPM, mensaje y flag de timeout.
    """
    log.info(f"Calibrating on Channel {channel}")
    print(f"\n--- Starting Real-Time Calibration on Channel {channel} ---")
    cmd = ['kal', '-c', str(channel), '-g', DEFAULT_GAIN]
    
    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        universal_newlines=True
    )

    ppm_val = None

    try:
        while True:
            # Check Global Timeout
            if time.time() > deadline:
                log.warning("Global timeout reached during calibration. Terminating.")
                process.terminate()
                return False, None, "0 (Global Timeout reached)", True

            line = process.stdout.readline()
            if not line and process.poll() is not None:
                break
            
            if line:
                print(f"  [kal]: {line.strip()}")
                match = re.search(r"average absolute error:\s+([-+]?[\d.]+)\s+ppm", line)
                if match:
                    ppm_val = float(match.group(1))

    except Exception as e:
        log.error(f"Unexpected error in calibration: {traceback.format_exc()}")
        process.kill()
        return False, None, f"0 (error: {str(e)})", False

    if ppm_val is not None:
        return True, ppm_val, f"{ppm_val} ppm", False
    else:
        return False, None, "0 (no ppm found)", False

def main() -> int:
    """
    Lógica principal de la campaña de calibración.

    Coordina el escaneo de múltiples bandas, selecciona el canal más fuerte
    y persiste el error PPM en el `ShmStore`. Si se alcanza el tiempo límite 
    establecido en `GLOBAL_TIMEOUT`, el script finaliza de forma segura para 
    no bloquear el sistema.

    Returns:
        int: 0 si la calibración fue exitosa o se manejó un timeout, 
             1 en caso de error crítico de hardware o falta de señal.
    """
    start_program = time.time()
    deadline = start_program + GLOBAL_TIMEOUT

    # 1. Hardware Check
    success, msg = check_hackrf_status()
    if not success:
        log.error(f"Abort: {msg}")
        return 1

    bands = ["GSM850", "GSM-R", "GSM900"]
    all_peaks = []
    PEAK_LIMIT = 10

    # 2. Scanning Phase
    for band in bands:
        if time.time() > deadline:
            log.warning("Global timeout reached before starting next band.")
            print("\n!!! Global Timeout Reached - Graceful Exit !!!")
            return 0 # User requested 0 on timeout
            
        found, timed_out = run_kal_scan(band, deadline)
        all_peaks.extend(found)
        
        if timed_out or len(all_peaks) >= PEAK_LIMIT:
            break

    if not all_peaks:
        if time.time() > deadline:
            return 0
        log.warning("No GSM peaks found.")
        return 1

    # 3. Sort and Select Best
    all_peaks.sort(key=lambda x: x[1], reverse=True)
    best_channel = all_peaks[0][0]
    
    # 4. Calibration Phase
    if time.time() > deadline:
        return 0

    cal_success, ppm_float, ppm_display, timed_out = calibrate_channel(best_channel, deadline)

    if timed_out:
        print("\n!!! Global Timeout Reached During Calibration - Graceful Exit !!!")
        return 0

    # 5. Result and Persistence
    print("\n" + "="*40)
    print(f"FINAL CALIBRATION REPORT")
    print(f"Status:        {'SUCCESS' if cal_success else 'FAILED'}")
    print(f"Channel Used:  {best_channel}")
    print(f"PPM Error:     {ppm_display}")
    print("="*40)

    if cal_success and ppm_float is not None:
        try:
            store = ShmStore()
            store.add_to_persistent("ppm_error", float(ppm_float))
            store.add_to_persistent("last_kal_ms", cfg.get_time_ms())
            log.info(f"Calibration successful: {ppm_float:.3f} ppm")
            return 0
        except Exception:
            log.error(f"Error saving to ShmStore:\n{traceback.format_exc()}")
            return 1
    else:
        return 1

if __name__ == "__main__":
    # Ensure even if cfg.run_and_capture is used, we wrap the exit logic
    rc = cfg.run_and_capture(main)
    sys.exit(rc)