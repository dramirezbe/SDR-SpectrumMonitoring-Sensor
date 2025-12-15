#!/usr/bin/env python3
"""
@file campaign_runner.py
@brief Main acquisition and posting routine for RF data. Handles LTE init, PSD acquisition,
       HTTP posting via RequestClient, and local persistence of unsent or historic data.
"""
import cfg
log = cfg.set_logger()
from utils import atomic_write_bytes, RequestClient, StatusDevice, ShmStore, ZmqPairController
from functions import format_data_for_upload

import sys
import json
import asyncio
import time
from pathlib import Path

# --- END HELPERS ---

def get_disk_usage(status_obj) -> float:
    """
    Return fraction used (0..1) for the filesystem.
    """
    disk_dict = status_obj.get_disk()
    total_disk_dict = status_obj.get_total_disk()
    disk_use = float(disk_dict.get("disk_mb", 0))
    total_disk = float(total_disk_dict.get("disk_mb", 1))

    return disk_use / total_disk



def save_json(current_dict: dict, target_dir: Path, timestamp) -> int:
    """
    Save plain JSON to `target_dir` atomically.
    Returns:
        0 -> success
        2 -> error
    """
    try:
        json_bytes = json.dumps(current_dict, separators=(",", ":")).encode("utf-8")
        target_dir = Path(target_dir)
        target_path = target_dir / f"{timestamp}.json"
        atomic_write_bytes(target_path, json_bytes)

        log.info(f"Saved JSON to {target_path}")
        return 0
    except Exception as e:
        log.error(f"Error saving to {target_dir}: {e}")
        return 2

def get_rf_params(store):
    try:
        rf_dict = {
            "rf_mode": "campaign",
            "center_freq_hz": store.consult_persistent("center_freq_hz"),
            "span": store.consult_persistent("span"),
            "sample_rate_hz": store.consult_persistent("sample_rate_hz"),
            "rbw_hz": store.consult_persistent("rbw_hz"),
            "overlap": store.consult_persistent("overlap"),
            "window": store.consult_persistent("window"),
            "scale": store.consult_persistent("scale"),
            "lna_gain": store.consult_persistent("lna_gain"),
            "vga_gain": store.consult_persistent("vga_gain"),
            "antenna_amp": store.consult_persistent("antenna_amp"),
            "antenna_port": store.consult_persistent("antenna_port"),
            "ppm_error": store.consult_persistent("ppm_error"),
        }
    except Exception as e:
        log.error(f"Error reading rf params from Shared Memory file: {e}")
        rf_dict = {}

    return rf_dict


def _delete_oldest_files(dir_path: Path, to_delete: int) -> int:
    """
    Delete up to `to_delete` oldest .json files in dir_path.
    Returns number of files actually deleted.
    """
    try:
        files = [p for p in Path(dir_path).iterdir() if p.is_file() and p.suffix == ".json"]
    except Exception as e:
        log.error(f"Error listing historic files in {dir_path}: {e}")
        return 0

    if not files:
        return 0

    def _age_key(p: Path):
        try:
            return int(p.stem)
        except Exception:
            try:
                return int(p.stat().st_mtime)
            except Exception:
                return 2**63 - 1

    files_sorted = sorted(files, key=_age_key)
    deleted = 0
    for f in files_sorted[:to_delete]:
        try:
            f.unlink()
            deleted += 1
            log.info(f"Deleted historic file {f} to free space.")
        except Exception as e:
            log.error(f"Failed to delete historic file {f}: {e}")
            # continue attempting to delete other files
    return deleted


async def main() -> int:
    """
    Main acquisition and posting routine.
    """
    status_obj = StatusDevice(logs_dir=cfg.LOGS_DIR, logger=log)
    cli = RequestClient(cfg.API_URL, mac_wifi=cfg.get_mac(), timeout=(5, 15), verbose=cfg.VERBOSE, logger=log)
    store = ShmStore()
    rf_cfg = get_rf_params(store)
    campaign_id = store.consult_persistent("campaign_id")

    if not rf_cfg:
        log.error("Error reading rf params from Shared Memory file")
        return 1

    # --- 3. Main Server Loop ---
    zmq_ctrl = ZmqPairController(addr=cfg.IPC_ADDR, is_server=True, verbose=False)
    await asyncio.sleep(0.5)

    await zmq_ctrl.send_command(rf_cfg)
    log.info("Waiting for PSD data from C engine...")

    try:
        # 2. Wait for Data
        raw_payload = await asyncio.wait_for(zmq_ctrl.wait_for_data(), timeout=10)
        
        # 3. Format
        data_dict = format_data_for_upload(raw_payload)
        data_dict["campaign_id"] = 5
        
        # --- LOGGING DATA ---
        log.info("----DATATOSEND--------")
        final_pxx = data_dict.get('Pxx', [])
        pxx_preview = final_pxx[:5] if isinstance(final_pxx, list) else []
        log.info(f"Pxx (First 5)     : {pxx_preview}")
        log.info("----------------------")

    except asyncio.TimeoutError:
        log.warning("TIMEOUT: No data from C-Engine. Retrying...")
        return 1

    # 4. Upload

    start_delta_t = time.perf_counter()
    rc, resp = cli.post_json("/data", data_dict)
    end_delta_t = time.perf_counter()
    delta_t_ms = int((end_delta_t - start_delta_t) * 1000)

    log.info(f"rc={rc} resp={resp}")
    log.info(f"string json={resp.text}")

    # save to queue
    if rc != 0:
        try:
            queue_count = len(list(cfg.QUEUE_DIR.iterdir()))
        except Exception:
            queue_count = 999  # force "full" if we can't list

        if queue_count < 50:
            save_rc = save_json(data_dict, cfg.QUEUE_DIR, cfg.get_time_ms())
            if save_rc != 0:
                log.error("Failed to save to queue after POST failure.")
                return 2
            log.warning("POST failed; saved to queue.")
            return 1 
        else:
            # Queue full logic
            try:
                files = [p for p in cfg.QUEUE_DIR.iterdir() if p.is_file() and p.suffix == ".json"]
                if not files:
                    log.error("Queue appears full but no JSON files found; dropping sample.")
                    return 1

                def _age_key(p: Path):
                    try:
                        return int(p.stem)
                    except Exception:
                        try:
                            return int(p.stat().st_mtime)
                        except Exception:
                            return 2**63 - 1

                oldest = min(files, key=_age_key)
                try:
                    oldest.unlink()
                    log.info(f"Deleted oldest queued file {oldest} to make room (queue capped at 50).")
                except Exception as e:
                    log.error(f"Failed to delete oldest queued file {oldest}: {e}")
                    return 1

                save_rc = save_json(data_dict, cfg.QUEUE_DIR, cfg.get_time_ms())
                if save_rc != 0:
                    log.error("Failed to save new sample to queue after deleting oldest file.")
                    return 2

                log.warning("POST failed; deleted oldest queued file and saved new sample to queue.")
                return 1

            except Exception as e:
                log.error(f"Unexpected error while managing queue files: {e}")
                return 1

    #If POST succeeded, save to historic directory
    store.add_to_persistent("delta_t_ms", delta_t_ms)
    if get_disk_usage(status_obj) < 0.8:
        hist_rc = save_json(data_dict, cfg.HISTORIC_DIR, cfg.get_time_ms())
        if hist_rc != 0:
            log.error("Failed to save to historic directory (non-fatal).")
        else:
            log.info("Saved copy to historic directory.")
    else:
        log.warning(f"Disk usage high ({get_disk_usage(status_obj):.3f}). Attempting to delete up to 10 oldest historic files.")
        deleted = _delete_oldest_files(cfg.HISTORIC_DIR, 10)
        if deleted > 0:
            log.info(f"Deleted {deleted} historic files; re-checking disk usage.")
        else:
            log.warning("No historic files deleted (none found or deletion errors).")

        if get_disk_usage(status_obj) < 0.8:
            hist_rc = save_json(data_dict, cfg.HISTORIC_DIR, cfg.get_time_ms())
            if hist_rc != 0:
                log.error("Failed to save to historic directory after cleaning (non-fatal).")
            else:
                log.info("Saved copy to historic directory after cleaning old files.")
        else:
            log.warning(f"Disk usage still too high ({get_disk_usage(status_obj):.3f}); skipping historic save.")

    return 0

# -------------------------------------------------------------------------
# Entry point
# -------------------------------------------------------------------------
if __name__ == "__main__":
    # run_and_capture wraps main, logging exceptions to file
    rc = cfg.run_and_capture(main)
    sys.exit(rc)