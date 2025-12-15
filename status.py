import cfg
import sys
log = cfg.set_logger()
from utils import StatusDevice, ShmStore, RequestClient

def build_status_final_payload(store, device):
    try:
        delta_t_ms = store.consult_persistent("delta_t_ms")
    except Exception as e:
        log.error(f"Error reading delta_t_ms from tmp file: {e}")
        delta_t_ms = 0
    try:
        last_ntp_ms = store.consult_persistent("last_ntp_ms")
    except Exception as e:
        log.error(f"Error reading last_ntp_ms from tmp file: {e}")
        last_ntp_ms = 0

    try:
        last_kal_ms = store.consult_persistent("last_kal_ms")
    except Exception as e:
        log.error(f"Error reading last_kal_ms from tmp file: {e}")
        last_kal_ms = 0

    return device.get_status_snapshot(
        delta_t_ms=delta_t_ms,
        last_kal_ms=last_kal_ms,
        last_ntp_ms=last_ntp_ms,
        timestamp_ms=cfg.get_time_ms(),
        mac=cfg.get_mac(),
    )
    

def main()->int:
    store = ShmStore()
    device = StatusDevice(logs_dir=cfg.LOGS_DIR, logger=log)
    cli = RequestClient(cfg.API_URL, mac_wifi=cfg.get_mac(), timeout=(5, 15), verbose=cfg.VERBOSE, logger=log)
    try:
        metrics_dict = build_status_final_payload(store, device)
    except Exception as e:
        log.error(f"Error building final payload: {e}")
        return 1
    
    rc, resp = cli.post_json(cfg.STATUS_URL, metrics_dict)
    if rc != 0:
        log.error(f"Error sending status: {rc}")
        return 1

    return 0

if __name__ == "__main__":
    rc = cfg.run_and_capture(main)
    sys.exit(rc)