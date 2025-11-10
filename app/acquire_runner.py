#!/usr/bin/env python3
"""
@file acquire_runner.py
@brief Main acquisition and posting routine for RF data. Handles LTE init, PSD acquisition,
       HTTP posting via RequestClient, and local persistence of unsent or historic data.
"""
import cfg
import sys
import json
from pathlib import Path

from utils import AcquireFrame, atomic_write_bytes, RequestClient, run_and_capture
from status_device import StatusDevice
from libs import init_lte, LTELibError

log = cfg.get_logger()
HISTORIC_DIR = cfg.PROJECT_ROOT / "Historic"

status_obj = StatusDevice()

def get_disk_usage() -> float:
    """
    Return fraction used (0..1) for the filesystem.
    """
    disk_dict = status_obj.get_disk()
    total_disk_dict = status_obj.get_total_disk()
    disk_use = float(disk_dict.get("disk_mb", 0))
    total_disk = float(total_disk_dict.get("disk_mb", 1))

    return disk_use / total_disk


def post_data(json_dict) -> int:
    """
    Send JSON data to remote API endpoint using RequestClient.
    """
    client = RequestClient(
        base_url=cfg.API_URL,
        timeout=(5, 15),
        verbose=cfg.VERBOSE,
        logger=log,
    )

    rc, resp = client.post_json(cfg.DATA_URL, json_dict)

    if resp is not None and cfg.VERBOSE:
        try:
            preview = resp.text[:200] + ("..." if len(resp.text) > 200 else "")
            log.info(f"POST response code={resp.status_code} preview={preview}")
        except Exception:
            pass

    return rc


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

        if cfg.VERBOSE:
            log.info(f"Saved JSON to {target_path}")
        return 0
    except Exception as e:
        log.error(f"Error saving to {target_dir}: {e}")
        return 2


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
            if cfg.VERBOSE:
                log.info(f"Deleted historic file {f} to free space.")
        except Exception as e:
            log.error(f"Failed to delete historic file {f}: {e}")
            # continue attempting to delete other files
    return deleted


def main() -> int:
    """
    Main acquisition and posting routine.
    """
    if len(sys.argv) != 5:
        log.error("Usage: acquire_runner <start_freq_hz> <end_freq_hz> <resolution_hz> <antenna_port> <method_runner(now, programmed)>")
        return 2

    start_freq_hz = sys.argv[1]
    end_freq_hz = sys.argv[2]
    resolution_hz = sys.argv[3]
    antenna_port = sys.argv[4]

    if cfg.VERBOSE:
        log.info(f"Acquiring data from {start_freq_hz} to {end_freq_hz} with resolution {resolution_hz}")

    if cfg.VERBOSE:
        log.info(f"Saving samples to {cfg.SAMPLES_DIR}")

    try:
        with init_lte(cfg.LIB_LTE, cfg.VERBOSE) as lte:
            lte.switch_antenna(int(antenna_port))
    except LTELibError as e:
        log.error(f"LTE error: {e}")
        return 2

    acq = AcquireFrame(int(start_freq_hz), int(end_freq_hz), int(resolution_hz))

    try:
        acq.create_IQ(cfg.SAMPLES_DIR)
        Pxx = acq.get_psd(cfg.SAMPLES_DIR)
    except Exception as e:
        log.error(f"Error acquiring IQ/PSD: {e}")
        return 2

    timestamp = cfg.get_time_ms()

    post_dict = {
        "Pxx": Pxx.tolist(),
        "start_freq_hz": int(start_freq_hz),
        "end_freq_hz": int(end_freq_hz),
        "timestamp": timestamp,
    }

    rc_post = post_data(post_dict)

    # If POST failed -> try to queue (limit 50 files)
    if rc_post != 0:
        try:
            queue_count = len(list(cfg.QUEUE_DIR.iterdir()))
        except Exception:
            queue_count = 999  # force "full" if we can't list

        # If queue has less than 50 files just save as before
        if queue_count < 50:
            save_rc = save_json(post_dict, cfg.QUEUE_DIR, timestamp)
            if save_rc != 0:
                log.error("Failed to save to queue after POST failure.")
                return 2
            log.warning("POST failed; saved to queue.")
            return 1  # indicate network/client/server error (non-zero)

        # If queue full
        else:
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
                    if cfg.VERBOSE:
                        log.info(f"Deleted oldest queued file {oldest} to make room (queue capped at 50).")
                except Exception as e:
                    log.error(f"Failed to delete oldest queued file {oldest}: {e}")
                    return 1

                save_rc = save_json(post_dict, cfg.QUEUE_DIR, timestamp)
                if save_rc != 0:
                    log.error("Failed to save new sample to queue after deleting oldest file.")
                    return 2

                log.warning("POST failed; deleted oldest queued file and saved new sample to queue.")
                return 1

            except Exception as e:
                log.error(f"Unexpected error while managing queue files: {e}")
                return 1

    # If POST succeeded
    
    if get_disk_usage() < 0.8:
        hist_rc = save_json(post_dict, HISTORIC_DIR, timestamp)
        if hist_rc != 0:
            log.error("Failed to save to historic directory (non-fatal).")
        elif cfg.VERBOSE:
            log.info("Saved copy to historic directory.")
    else:
        # Disk usage high: attempt to delete up to 10 oldest historic files.
        if cfg.VERBOSE:
            log.warning(f"Disk usage high ({get_disk_usage():.3f}). Attempting to delete up to 10 oldest historic files.")

        deleted = _delete_oldest_files(HISTORIC_DIR, 10)
        if deleted > 0:
            if cfg.VERBOSE:
                log.info(f"Deleted {deleted} historic files; re-checking disk usage.")
        else:
            log.warning("No historic files deleted (none found or deletion errors).")

        # Re-check disk usage after deletions
        if get_disk_usage() < 0.8:
            hist_rc = save_json(post_dict, HISTORIC_DIR, timestamp)
            if hist_rc != 0:
                log.error("Failed to save to historic directory after cleaning (non-fatal).")
            elif cfg.VERBOSE:
                log.info("Saved copy to historic directory after cleaning old files.")
        else:
            log.warning(f"Disk usage still too high ({get_disk_usage():.3f}); skipping historic save.")

    # All good
    return 0


# -------------------------------------------------------------------------
# Entry point
# -------------------------------------------------------------------------
if __name__ == "__main__":
    rc = run_and_capture(main, log, cfg.LOGS_DIR / "acquire_runner", cfg.get_time_ms(), cfg.LOG_FILES_NUM)
    sys.exit(rc)