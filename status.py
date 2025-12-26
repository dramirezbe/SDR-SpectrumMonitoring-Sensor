#!/usr/bin/env python3
# status.py

"""
Módulo de Gestión de Estado del Dispositivo.

Este script se encarga de recopilar métricas del sistema, como la sincronización NTP,
tiempos de calibración y deltas de tiempo, para construir un paquete de datos (payload)
y enviarlo al servidor central a través de una API REST.
"""

import cfg
import sys
import os

# Setup Logger
log = cfg.set_logger()

# Import Utils
from utils import StatusDevice, ShmStore, RequestClient


def get_last_ntp_sync_ms():
    SYNC_FILE = '/var/lib/systemd/timesync/clock'
    OFFSET_MS = 5 * 60 * 60 * 1000
    
    try:
        # mtime es UTC
        timestamp_sec = os.path.getmtime(SYNC_FILE)
        # Convertimos a ms y restamos el offset para que coincida con Colombia
        return int(timestamp_sec * 1000) - OFFSET_MS
        
    except FileNotFoundError:
        return None

def build_status_final_payload(store, device):
    """
    Construye el payload final con el estado completo del dispositivo.

    Recopila información desde el almacenamiento persistente (ShmStore) y 
    del sistema (NTP) para generar una instantánea (snapshot) del estado actual.

    Args:
        store (ShmStore): Instancia del almacén de memoria compartida para consultar persistencia.
        device (StatusDevice): Instancia del dispositivo para generar el formato del snapshot.

    Returns:
        dict: Diccionario formateado con todas las métricas, MAC y timestamp actual.
    """
    # 1. Delta T
    try:
        delta_t_ms = store.consult_persistent("delta_t_ms")
    except Exception as e:
        log.error(f"Error reading delta_t_ms from tmp file: {e}")
        delta_t_ms = 0

    # 2. Last NTP Sync (Using the function above)
    # Note: We fetch the raw timestamp here. 
    # If the file is missing, we default to 0.
    last_ntp_raw = get_last_ntp_sync_ms()
    last_ntp_ms = last_ntp_raw if last_ntp_raw is not None else 0

    # 3. Last Calibration
    try:
        last_kal_ms = store.consult_persistent("last_kal_ms")
    except Exception as e:
        log.error(f"Error reading last_kal_ms from tmp file: {e}")
        last_kal_ms = 0

    # Build Snapshot
    return device.get_status_snapshot(
        delta_t_ms=delta_t_ms,
        last_kal_ms=last_kal_ms,
        last_ntp_ms=last_ntp_ms,
        timestamp_ms=cfg.get_time_ms(),
        mac=cfg.get_mac(),
    )
    

def main() -> int:
    """
    Punto de entrada principal del script.

    Inicializa los clientes de comunicación, construye el payload de estado
    y realiza la petición POST hacia el endpoint configurado.

    Returns:
        int: Código de salida (0 para éxito, 1 para error).
    """
    store = ShmStore()
    device = StatusDevice(logs_dir=cfg.LOGS_DIR, logger=log)
    cli = RequestClient(cfg.API_URL, mac_wifi=cfg.get_mac(), timeout=(5, 15), verbose=cfg.VERBOSE, logger=log)
    
    try:
        metrics_dict = build_status_final_payload(store, device)
    except Exception as e:
        log.error(f"Error building final payload: {e}")
        return 1
    
    
    # Send data
    rc, _ = cli.post_json(cfg.STATUS_URL, metrics_dict)
    if rc != 0:
        log.error(f"Error sending status: {rc}")
        return 1

    return 0

if __name__ == "__main__":
    rc = cfg.run_and_capture(main)
    sys.exit(rc)