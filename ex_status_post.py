import cfg
log = cfg.set_logger()
from utils import StatusPost, StatusDevice, RequestClient

from dataclasses import asdict
import sys

def serialize_status_post(status: StatusPost) -> dict:
    """
    Helper to convert StatusPost back to the flat JSON format 
    required by the server (flattening the cpu_loads list).
    """
    # 1. Convert standard fields to dict
    data = asdict(status)
    
    # 2. Extract list and remove it from dict
    cpu_loads = data.pop('cpu_loads', [])
    
    # 3. Flatten list into cpu_0, cpu_1, etc.
    for i, load in enumerate(cpu_loads):
        data[f'cpu_{i}'] = load
        
    return data


def main()->int:
    device = StatusDevice(logs_dir=cfg.LOGS_DIR, logger=log)
    #client = RequestClient(cfg.API_URL, mac_wifi=cfg.get_mac(), timeout=(5, 15), verbose=cfg.VERBOSE, logger=log)

    status_obj = device.get_status_snapshot(
        mac=cfg.get_mac(),
        delta_t_ms=cfg.get_time_ms(),
        last_kal_ms=cfg.get_time_ms(),
        last_ntp_ms=cfg.get_time_ms(),
        timestamp_ms=cfg.get_time_ms(),
    )
    payload = serialize_status_post(status_obj)
    #rc, resp = client.post_json(cfg.STATUS_URL, json_dict=payload)

    log.info("Payload: %s", payload)

    return 0

if __name__ == "__main__":
    rc = cfg.run_and_capture(main, cfg.LOG_FILES_NUM)
    sys.exit(rc)