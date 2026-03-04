#!/usr/bin/env python3
# kal_sync.py

"""
Módulo de Sincronización de Frecuencia (Kalibrate).

Este script actúa como una utilidad de autocalibración para el SDR (HackRF). 
Utiliza la herramienta externa `kalibrate-hackrf` para escanear bandas GSM, 
encontrar la estación base con la señal más fuerte, calcular el desplazamiento 
de frecuencia (PPM error) del oscilador local y guardarlo en memoria persistente.

Cuenta con un mecanismo de "heartbeat" para indicar actividad y un "timeout" 
global estricto para evitar bloqueos del sistema.
"""

import subprocess
import re
import time
import sys
import traceback
import threading

# Importaciones personalizadas del proyecto
import cfg
from utils import ShmStore

# Inicialización del logger
log = cfg.set_logger()

# Tiempo máximo de ejecución permitido para todo el script (1 minuto y 30 segundos)
GLOBAL_TIMEOUT = 90  

def heartbeat(start_time, duration):
    """
    Función que se ejecuta en un hilo secundario para imprimir un latido de vida.
    
    Imprime un mensaje en la consola cada 10 segundos exactos (ej. [10s], [20s]) 
    para indicar que el proceso sigue vivo, útil si el escaneo tarda en responder.

    Args:
        start_time (float): El timestamp (time.time()) en el que inició el programa.
        duration (int): La duración máxima permitida en segundos (GLOBAL_TIMEOUT).
    """
    next_beat = 10
    while True:
        elapsed = time.time() - start_time
        # Salir si superamos el tiempo global
        if elapsed >= duration:
            break
        # Si pasamos el próximo umbral de 10 segundos, imprimimos
        if elapsed >= next_beat:
            print(f"[{next_beat}s]")
            next_beat += 10
        # Dormimos brevemente para no consumir CPU innecesariamente
        time.sleep(0.1)

def check_hackrf_status():
    """
    Verifica la disponibilidad del hardware HackRF mediante el comando hackrf_info.
    
    Returns:
        tuple: (bool, str) Un booleano indicando si el hardware está listo, 
               y un mensaje descriptivo del estado o error.
    """
    try:
        # Ejecutamos hackrf_info con un timeout corto de seguridad
        result = subprocess.run(['hackrf_info'], capture_output=True, text=True, timeout=10)
        output = (result.stdout + result.stderr).lower()
        
        # Analizamos la salida buscando estados de error comunes
        if "busy" in output:
            return False, "HackRF is currently busy."
        if "not found" in output:
            return False, "No HackRF detected."
        return True, "HackRF Ready."
    except Exception as e:
        return False, f"Error checking HackRF: {str(e)}"

def run_kal_scan(band, deadline):
    """
    Escanea una banda GSM específica en busca de estaciones base usando kalibrate.

    Se ejecuta el comando más simple posible: `kal -s <banda>`. Lee la salida en
    tiempo real para extraer los canales y sus potencias correspondientes.

    Args:
        band (str): Nombre de la banda a escanear (ej. 'GSM900').
        deadline (float): Timestamp límite en el que el escaneo debe abortarse obligatoriamente.

    Returns:
        tuple: (list, bool) Una lista de tuplas con los canales y sus potencias 
               [(canal, potencia), ...], y un flag booleano que indica si hubo un timeout.
    """
    log.info(f"Scanning band: {band}")
    print(f"\n--- Scanning band: {band} ---")
    found_in_band = []
    
    # Comando simplificado: solo se indica el escaneo (-s) y la banda
    cmd = ['kal', '-s', band]
    
    # Popen nos permite leer la salida estándar (stdout) de forma asíncrona línea por línea
    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, # Redirigimos stderr a stdout para leer todo en un solo flujo
        text=True,
        bufsize=1,
        universal_newlines=True
    )

    try:
        while True:
            # Comprobación de timeout global antes de cada lectura
            if time.time() > deadline:
                log.warning(f"Global timeout reached during scan of {band}. Terminating.")
                process.terminate() # Matamos el proceso hijo de kalibrate
                return found_in_band, True
                
            line = process.stdout.readline()
            
            # Si readline() devuelve vacío y el proceso ya terminó, rompemos el ciclo
            if not line and process.poll() is not None:
                break
            
            if line:
                clean_line = line.strip()
                # Imprimimos la salida en pantalla omitiendo líneas de banner
                if clean_line and not clean_line.startswith("kalibrate"):
                    print(f"  [scan]: {clean_line}")
                
                # Expresión regular para buscar el número de canal y su potencia (power)
                # Ejemplo de salida esperada: "chan: 1 (935.2MHz + 2.3kHz) power: 23145.2"
                match = re.search(r"chan:\s+(\d+).*power:\s+([\d.]+)", clean_line)
                if match:
                    channel = match.group(1)
                    power = float(match.group(2))
                    found_in_band.append((channel, power))

    except Exception as e:
        log.error(f"Error during scan: {e}")
        process.kill()

    # Retornamos los picos encontrados y el flag de timeout en Falso
    return found_in_band, False

def calibrate_channel(channel, deadline):
    """
    Calcula el error de frecuencia (PPM) sintonizando un canal específico.

    Ejecuta el comando: `kal -c <canal>`. Parsea la salida para encontrar el 
    promedio de error absoluto en partes por millón (ppm).

    Args:
        channel (str): El canal GSM a utilizar para la calibración.
        deadline (float): Timestamp límite de seguridad global.

    Returns:
        tuple: (bool, float, str, bool) 
            - Éxito del cálculo (True/False)
            - Valor en coma flotante del error PPM (ej. 15.2)
            - Mensaje formateado para display
            - Flag booleano de timeout
    """
    log.info(f"Calibrating on Channel {channel}")
    print(f"\n--- Starting Real-Time Calibration on Channel {channel} ---")
    
    # Comando simplificado: solo se indica calcular (-c) y el canal
    cmd = ['kal', '-c', str(channel)]
    
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
            # Comprobación de seguridad para evitar bloqueos
            if time.time() > deadline:
                log.warning("Global timeout reached during calibration. Terminating.")
                process.terminate()
                return False, None, "0 (Global Timeout reached)", True

            line = process.stdout.readline()
            if not line and process.poll() is not None:
                break
            
            if line:
                clean_line = line.strip()
                if clean_line:
                    print(f"  [kal]: {clean_line}")
                
                # Expresión regular para encontrar el valor final del error ppm
                # Ejemplo esperado: "average absolute error: 12.345 ppm"
                match = re.search(r"average absolute error:\s+([-+]?[\d.]+)\s+ppm", clean_line)
                if match:
                    ppm_val = float(match.group(1))

    except Exception as e:
        log.error(f"Unexpected error in calibration: {traceback.format_exc()}")
        process.kill()
        return False, None, f"0 (error: {str(e)})", False

    # Si encontramos el valor, retornamos éxito
    if ppm_val is not None:
        return True, ppm_val, f"{ppm_val} ppm", False
    else:
        return False, None, "0 (no ppm found)", False

def main() -> int:
    """
    Lógica principal (Orquestador) de la campaña de calibración.

    Coordina el escaneo de múltiples bandas, selecciona el canal más fuerte,
    calibra sobre él y persiste el error PPM en el `ShmStore`. 

    Returns:
        int: 0 si la calibración fue exitosa o se manejó el timeout de forma grácil. 
             1 en caso de error crítico de hardware o si no se encuentran señales.
    """
    start_program = time.time()
    deadline = start_program + GLOBAL_TIMEOUT

    # Iniciamos el hilo secundario para el latido (heartbeat)
    # Al ser "daemon=True", este hilo se cerrará automáticamente cuando el hilo principal termine
    hb_thread = threading.Thread(target=heartbeat, args=(start_program, GLOBAL_TIMEOUT), daemon=True)
    hb_thread.start()

    # 1. Verificación del Hardware
    success, msg = check_hackrf_status()
    if not success:
        log.error(f"Abort: {msg}")
        return 1

    bands = ["GSM850", "GSM-R", "GSM900"]
    all_peaks = []
    PEAK_LIMIT = 10 # Si encontramos suficientes estaciones, podemos detener el escaneo temprano

    # 2. Fase de Escaneo
    for band in bands:
        # Verificamos si ya no nos queda tiempo antes de pasar a la siguiente banda
        if time.time() > deadline:
            break
            
        found, timed_out = run_kal_scan(band, deadline)
        all_peaks.extend(found)
        
        # Abortar escaneos posteriores si se nos acabó el tiempo o ya tenemos suficientes canales
        if timed_out or len(all_peaks) >= PEAK_LIMIT:
            break

    # Si no se encontró ninguna señal tras los escaneos
    if not all_peaks:
        if time.time() > deadline:
            # Si no hay señales y se acabó el tiempo, salimos con 0 (timeout manejado)
            return 0
        log.warning("No GSM peaks found.")
        return 1

    # 3. Ordenamiento y Selección del Mejor Canal
    # Ordenamos la lista basándonos en la potencia (índice 1 de la tupla), de mayor a menor
    all_peaks.sort(key=lambda x: x[1], reverse=True)
    best_channel = all_peaks[0][0] # Extraemos solo el número del canal con mayor potencia
    
    # 4. Fase de Calibración
    if time.time() > deadline:
        return 0

    cal_success, ppm_float, ppm_display, timed_out = calibrate_channel(best_channel, deadline)

    if timed_out:
        print("\n!!! Global Timeout Reached During Calibration - Graceful Exit !!!")
        return 0

    # 5. Reporte Final y Persistencia en Memoria (ShmStore)
    print("\n" + "="*40)
    print(f"FINAL CALIBRATION REPORT")
    print(f"Status:        {'SUCCESS' if cal_success else 'FAILED'}")
    print(f"Channel Used:  {best_channel}")
    print(f"PPM Error:     {ppm_display}")
    print("="*40)

    # Si todo salió bien, guardamos los resultados en el almacenamiento persistente
    if cal_success and ppm_float is not None:
        try:
            store = ShmStore()
            # Guardamos el error de frecuencia para que el SDR lo aplique
            store.add_to_persistent("ppm_error", float(ppm_float))
            # Guardamos el timestamp de esta calibración exitosa
            store.add_to_persistent("last_kal_ms", cfg.get_time_ms())
            
            log.info(f"Calibration successful: {ppm_float:.3f} ppm")
            return 0
        except Exception:
            log.error(f"Error saving to ShmStore:\n{traceback.format_exc()}")
            return 1
    else:
        # Si la calibración falló por motivos ajenos a un timeout
        return 1

if __name__ == "__main__":
    # Garantizamos que la aplicación capture cualquier error y retorne un código de salida limpio
    rc = cfg.run_and_capture(main)
    sys.exit(rc)