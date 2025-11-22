#!/usr/bin/env python3
"""
@file campaign_runner.py
@brief Main acquisition and posting routine for RF data. Handles LTE init, PSD acquisition,
       HTTP posting via RequestClient, and local persistence of unsent or historic data.
"""
import cfg
import sys
import json
import argparse
import time
from pathlib import Path

from utils import atomic_write_bytes, RequestClient, CampaignHackRF, get_persist_var, modify_persist
from status_device import StatusDevice

# --- START OF PLOTTING ADDITIONS ---
import numpy as np
import matplotlib.pyplot as plt
# --- END OF PLOTTING ADDITIONS ---

log = cfg.set_logger()
HISTORIC_DIR = cfg.PROJECT_ROOT / "Historic"

status_obj = StatusDevice()

# --- HELPER CLASSES/FUNCTIONS ---

class HelpOnErrorParser(argparse.ArgumentParser):
    """Custom parser that prints full help on error."""
    def error(self, message):
        sys.stderr.write(f'Error: {message}\n')
        self.print_help()
        sys.exit(2)

def str_to_bool(value):
    """Helper to parse various boolean string representations."""
    if isinstance(value, bool):
        return value
    if value.lower() in ('yes', 'true', 't', 'y', '1', 'on'):
        return True
    elif value.lower() in ('no', 'false', 'f', 'n', '0', 'off'):
        return False
    else:
        raise argparse.ArgumentTypeError('Boolean value expected.')

# --- END HELPERS ---

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
    client = RequestClient(cfg.API_URL, timeout=(5, 15), verbose=cfg.VERBOSE, logger=log, api_key=cfg.get_mac())

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
    # --- 1. Define Argument Parser ---
    parser = HelpOnErrorParser(description="RF Campaign Runner")

    # Required Core Arguments
    parser.add_argument("-f1", "--start_freq", type=int, required=True, help="Start Frequency in Hz")
    parser.add_argument("-f2", "--end_freq", type=int, required=True, help="End Frequency in Hz")
    parser.add_argument("-w", "--resolution", type=int, required=True, help="Resolution Bandwidth (Hz)")
    parser.add_argument("-p", "--port", type=int, required=True, help="Antenna Port ID")

    # Extended Arguments (with defaults just in case, though Orchestrator sends them)
    parser.add_argument("-wi", "--window", type=str, default="hamming", help="FFT Windowing function")
    parser.add_argument("-o", "--overlap", type=float, default=0.5, help="FFT Overlap (0.0 - 1.0)")
    parser.add_argument("-fs", "--sample_rate", type=int, default=20000000, help="SDR Sample Rate in Hz")
    parser.add_argument("-l", "--lna", type=int, default=0, help="LNA Gain (dB)")
    parser.add_argument("-g", "--vga", type=int, default=0, help="VGA Gain (dB)")
    parser.add_argument("-a", "--antenna_amp", type=str_to_bool, default=False, help="Antenna Amp (True/False/1/0)")

    # Parse Arguments
    args = parser.parse_args()

    # Map to local variables for clarity
    start_freq_hz = args.start_freq
    end_freq_hz = args.end_freq
    resolution_hz = args.resolution
    antenna_port = args.port
    
    # Extended
    window = args.window
    overlap = args.overlap
    sample_rate_hz = args.sample_rate
    lna_gain = args.lna
    vga_gain = args.vga
    antenna_amp = args.antenna_amp

    # Log Initial State
    log.info(f"Acquiring {start_freq_hz}-{end_freq_hz} Hz | Res: {resolution_hz} | Port: {antenna_port}")
    log.info(f"Ext Args: Win={window}, Overlap={overlap}, SR={sample_rate_hz}, LNA={lna_gain}, VGA={vga_gain}, Amp={antenna_amp}")
    log.info(f"Saving samples to {cfg.SAMPLES_DIR}")

    # Switch antenna persistence
    rc = modify_persist("antenna_port", antenna_port, cfg.PERSIST_FILE)
    if rc != 0:
        log.error(f"Failed saving antenna_port into persistent.json (modify_persist returned non-zero).")
        return rc
    time.sleep(0.1) #giving time to change antenna
    
    # --- Initialize HackRF with new arguments ---
    hack_rf = CampaignHackRF(
        start_freq_hz=start_freq_hz, 
        end_freq_hz=end_freq_hz,
        sample_rate_hz=sample_rate_hz, 
        resolution_hz=resolution_hz, 
        scale='dBm', 
        with_shift=False,
        window=window,
        overlap=overlap,
        lna_gain=lna_gain,
        vga_gain=vga_gain,
        antenna_amp=antenna_amp
    )
    
    result = hack_rf.get_psd()

    # Destructure result
    if isinstance(result, tuple):
        freqs, Pxx = result
    else:
        freqs = None
        Pxx = result

    # --- Plotting Code ---
    timestamp = cfg.get_time_ms()
    
    if Pxx is not None:
        save_psd_plot(freqs, Pxx, timestamp, log)
    else:
        log.warning("Pxx is None. Skipping plot generation.")

    # Prepare Data Payload
    post_dict = {
        "device_id": get_persist_var("device_id",cfg.PERSIST_FILE),
        "Pxx": Pxx.tolist() if Pxx is not None else None,
        "start_freq_hz": start_freq_hz,
        "end_freq_hz": end_freq_hz,
        "timestamp": timestamp,
        "campaign_id": get_persist_var("campaign_id",cfg.PERSIST_FILE),
    }
    log.info(f"POST Payload: {post_dict}")

    rc_post = post_data(post_dict)

    # --- Logic for Queue vs Historic Save (Identical to before) ---
    # If POST failed -> try to queue (limit 50 files)
    if rc_post != 0:
        try:
            queue_count = len(list(cfg.QUEUE_DIR.iterdir()))
        except Exception:
            queue_count = 999  # force "full" if we can't list

        if queue_count < 50:
            save_rc = save_json(post_dict, cfg.QUEUE_DIR, timestamp)
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
        log.warning(f"Disk usage high ({get_disk_usage():.3f}). Attempting to delete up to 10 oldest historic files.")
        deleted = _delete_oldest_files(HISTORIC_DIR, 10)
        if deleted > 0:
            log.info(f"Deleted {deleted} historic files; re-checking disk usage.")
        else:
            log.warning("No historic files deleted (none found or deletion errors).")

        if get_disk_usage() < 0.8:
            hist_rc = save_json(post_dict, HISTORIC_DIR, timestamp)
            if hist_rc != 0:
                log.error("Failed to save to historic directory after cleaning (non-fatal).")
            else:
                log.info("Saved copy to historic directory after cleaning old files.")
        else:
            log.warning(f"Disk usage still too high ({get_disk_usage():.3f}); skipping historic save.")

    return 0


# -------------------------------------------------------------------------
# Entry point
# -------------------------------------------------------------------------
if __name__ == "__main__":
    # run_and_capture wraps main, logging exceptions to file
    rc = cfg.run_and_capture(main, cfg.LOG_FILES_NUM)
    sys.exit(rc)