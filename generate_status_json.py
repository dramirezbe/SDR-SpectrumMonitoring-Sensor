import sys
import json
import time
from typing import Tuple, Dict, Any

import cfg
from utils import StatusDevice

# Initialize Logger
log = cfg.set_logger()

# Constants
DIR_STATUS_JSONS = cfg.PROJECT_ROOT / "status_jsons"
SAMPLE_COUNT = 25
SAMPLE_INTERVAL_SEC = 1

def ensure_output_dir():
    """Ensures the output directory exists to prevent IOErrors."""
    DIR_STATUS_JSONS.mkdir(parents=True, exist_ok=True)

def get_final_dicts(device: StatusDevice) -> Tuple[int, Dict[str, Any]]:
    """
    Captures device status and appends metadata.
    """
    try:
        metrics_dict = device.get_status_snapshot()
        
        # Append metadata
        metrics_dict.update({
            "mac": cfg.get_mac(),
            "last_delta_ms": 0,
            "last_kal_ms": 0,
            "last_ntp_ms": 0,
            "delta_t_ms": 0,
            "timestamp_ms": cfg.get_time_ms()
        })
        
        return 0, metrics_dict
    except Exception as e:
        log.error(f"Error generating dictionary: {e}")
        return 1, {}

def save_json(data: Dict[str, Any], filename: Any) -> int:
    """
    Saves dictionary data to a JSON file.
    """
    try:
        # Use pathlib's open method for consistency
        with filename.open('w', encoding='utf-8') as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
        return 0
    except IOError as e:
        log.error(f"Failed to write to {filename}: {e}")
        return 1

def main() -> int:
    # 1. Setup Environment
    ensure_output_dir()
    device = StatusDevice(logs_dir=cfg.LOGS_DIR, logger=log)
    
    log.info(f"Starting capture: {SAMPLE_COUNT} samples at {SAMPLE_INTERVAL_SEC}s intervals.")

    # 2. Main Loop
    for i in range(SAMPLE_COUNT):
        # Capture Data
        err, metrics_dict = get_final_dicts(device)
        if err:
            log.error("get_final_dicts returned an error.")
            return err
        
        # Define Filename
        timestamp = metrics_dict.get("timestamp_ms", cfg.get_time_ms())
        filename = DIR_STATUS_JSONS / f"{timestamp}.json"
        
        # Save Data
        err = save_json(metrics_dict, filename)
        if err:
            return err
            
        log.info(f"[{i+1}/{SAMPLE_COUNT}] Saved {filename.name}")
        
        # Sleep handles sampling rate (Control logic moved here)
        time.sleep(SAMPLE_INTERVAL_SEC)
        
    return 0

if __name__ == "__main__":
    rc = cfg.run_and_capture(main, cfg.LOG_FILES_NUM)
    sys.exit(rc)