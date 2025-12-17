#!/usr/bin/env python3
#status.py

import cfg
import sys
import os

# Setup Logger
log = cfg.set_logger()

# Import Utils
from utils import StatusDevice, ShmStore, RequestClient


def get_last_ntp_sync_ms():
    """
    Returns the last NTP sync time as a Unix timestamp in milliseconds.
    Returns:
        int: Unix timestamp in ms (e.g., 1734395127137)
        None: If the sync file is missing.
    """
    # The file systemd uses to mark the last sync time
    SYNC_FILE = '/var/lib/systemd/timesync/clock'
    
    try:
        # getmtime returns seconds as a float (e.g., 1734395127.137)
        timestamp_sec = os.path.getmtime(SYNC_FILE)
        
        # Convert to milliseconds and cast to integer (Absolute Timestamp)
        return int(timestamp_sec * 1000)
        
    except FileNotFoundError:
        # File doesn't exist implies sync never happened or service is off
        return None

def build_status_final_payload(store, device):
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