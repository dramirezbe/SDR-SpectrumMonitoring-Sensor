#!/usr/bin/env python3
"""
@file campaign_runner.py
@brief Main acquisition and posting routine for RF data. Handles LTE init, PSD acquisition,
       HTTP posting via RequestClient, and local persistence of unsent or historic data.
"""
import cfg
import sys
import json
from pathlib import Path

from utils import atomic_write_bytes, RequestClient, CampaignHackRF, get_persist_var
from status_device import StatusDevice
from libs import init_lte, LTELibError

# --- START OF PLOTTING ADDITIONS ---
import numpy as np
import matplotlib.pyplot as plt
# --- END OF PLOTTING ADDITIONS ---

log = cfg.set_logger()
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
    client = RequestClient(cfg.API_URL, timeout=(5, 15), verbose=cfg.VERBOSE, logger=log, api_key=cfg.API_KEY)

    rc, resp = client.post_json(cfg.DATA_URL, json_dict)

    if resp is not None:
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
            log.info(f"Deleted historic file {f} to free space.")
        except Exception as e:
            log.error(f"Failed to delete historic file {f}: {e}")
            # continue attempting to delete other files
    return deleted


# --- START OF PLOTTING ADDITIONS (MODIFIED) ---
def save_psd_plot(freqs, Pxx, timestamp, log):
    """
    Generate and save a plot of the Power Spectral Density (Pxx) vs. Frequency or Sample Index.
    """
    if Pxx is None:
        log.warning("Cannot plot: Pxx data is missing.")
        return 2

    try:
        Pxx = np.array(Pxx)
        
        if freqs is not None:
            # Use actual frequency vector
            freqs = np.array(freqs)
            freqs_mhz = freqs / 1e6
            x_axis = freqs_mhz
            x_label = 'Frequency (MHz)'
        else:
            # Fallback: Use sample index if frequency vector is missing
            x_axis = np.arange(len(Pxx))
            x_label = 'Sample Index (Frequency Data Unavailable)'
            log.warning("Frequency vector 'freqs' is None. Plotting against sample index.")
        
        plt.figure(figsize=(10, 6))
        plt.plot(x_axis, Pxx)
        plt.title(f'Power Spectral Density (PSD) Acquisition - {timestamp}')
        plt.xlabel(x_label)
        plt.ylabel('Power (dBm)')
        plt.grid(True)
        plt.tight_layout()
        
        # Save the plot with the timestamp in the filename in the current directory
        plot_path = Path(f"{timestamp}_psd.png")
        plt.savefig(plot_path)
        plt.close() # Close the figure to free memory
        
        log.info(f"Saved PSD plot to {plot_path}")
        return 0
    except Exception as e:
        log.error(f"Error saving PSD plot: {e}")
        return 2
# --- END OF PLOTTING ADDITIONS (MODIFIED) ---


def main() -> int:
    """
    Main acquisition and posting routine.
    """
    if len(sys.argv) != 5:
        log.error("Usage: acquire_runner <start_freq_hz> <end_freq_hz> <resolution_hz> <antenna_port>")
        return 2

    start_freq_hz = int(sys.argv[1])
    end_freq_hz = int(sys.argv[2])
    resolution_hz = int(sys.argv[3])
    antenna_port = int(sys.argv[4])

    log.info(f"Acquiring data from {start_freq_hz} to {end_freq_hz} with resolution {resolution_hz}")

    log.info(f"Saving samples to {cfg.SAMPLES_DIR}")


    #switch antenna
    try:
        with init_lte(cfg.LIB_LTE, cfg.VERBOSE) as lte:
            lte.switch_antenna(int(antenna_port))
    except LTELibError as e:
        log.error(f"LTE error: {e}")
        return 2
    
    hack_rf = CampaignHackRF(start_freq_hz=start_freq_hz, end_freq_hz=end_freq_hz,
                sample_rate_hz=20_000_000, resolution_hz=resolution_hz, 
                scale='dBm', with_shift=True)
    
    result = hack_rf.get_psd()   # puede ser: Pxx or (f, Pxx) or None or (None, None)

    # Desestructura según el tipo
    if isinstance(result, tuple):
        freqs, Pxx = result
    else:
        freqs = None
        Pxx = result

    
    # --- START OF MODIFICATION: Plotting Code Integration ---
    timestamp = cfg.get_time_ms() # Move timestamp assignment up to use for plotting filename
    
    # Plot the PSD if Pxx data exists
    if Pxx is not None:
        save_psd_plot(freqs, Pxx, timestamp, log)
    else:
        log.warning("Pxx is None. Skipping plot generation.")
    # --- END OF MODIFICATION: Plotting Code Integration ---


    post_dict = {
        "Pxx": Pxx.tolist() if Pxx is not None else None,
        "start_freq_hz": start_freq_hz,
        "end_freq_hz": end_freq_hz,
        "timestamp": timestamp, # Uses the timestamp defined above
        "lat": 0,
        "lng":0,
        "campaign_id": get_persist_var("campaign_id",cfg.PERSIST_FILE)
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
        else:
            log.info("Saved copy to historic directory.")
    else:
        # Disk usage high: attempt to delete up to 10 oldest historic files.
        log.warning(f"Disk usage high ({get_disk_usage():.3f}). Attempting to delete up to 10 oldest historic files.")

        deleted = _delete_oldest_files(HISTORIC_DIR, 10)
        if deleted > 0:
            log.info(f"Deleted {deleted} historic files; re-checking disk usage.")
        else:
            log.warning("No historic files deleted (none found or deletion errors).")

        # Re-check disk usage after deletions
        if get_disk_usage() < 0.8:
            hist_rc = save_json(post_dict, HISTORIC_DIR, timestamp)
            if hist_rc != 0:
                log.error("Failed to save to historic directory after cleaning (non-fatal).")
            else:
                log.info("Saved copy to historic directory after cleaning old files.")
        else:
            log.warning(f"Disk usage still too high ({get_disk_usage():.3f}); skipping historic save.")

    # All good
    return 0


# -------------------------------------------------------------------------
# Entry point
# -------------------------------------------------------------------------
if __name__ == "__main__":
    rc = cfg.run_and_capture(main, cfg.LOG_FILES_NUM)
    sys.exit(rc)