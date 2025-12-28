import subprocess
import re
import time
import sys
import traceback

# Custom imports from your project
import cfg
from utils import ShmStore

log = cfg.set_logger()

# HackRF often needs gain to see signals clearly. Adjust 0-40 as needed.
DEFAULT_GAIN = "40" 

def check_hackrf_status():
    """
    Checks if the HackRF is available.
    Returns: (bool, message)
    """
    try:
        result = subprocess.run(['hackrf_info'], capture_output=True, text=True, timeout=10)
        output = (result.stdout + result.stderr).lower()
        if "busy" in output:
            return False, "HackRF is currently busy."
        if "not found" in output:
            return False, "No HackRF detected."
        return True, "HackRF Ready."
    except Exception as e:
        return False, f"Error checking HackRF: {str(e)}"

def run_kal_scan(band, start_time, time_limit):
    """
    Runs kal -s and prints output in real-time.
    Returns: list of (channel, power)
    """
    log.info(f"Scanning band: {band}")
    print(f"\n--- Scanning band: {band} ---")
    found_in_band = []
    
    cmd = ['kal', '-s', band, '-g', DEFAULT_GAIN]
    
    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        universal_newlines=True
    )

    try:
        while True:
            line = process.stdout.readline()
            if not line and process.poll() is not None:
                break
            
            if line:
                clean_line = line.strip()
                print(f"  [scan]: {clean_line}")
                match = re.search(r"chan:\s+(\d+).*power:\s+([\d.]+)", clean_line)
                if match:
                    found_in_band.append((match.group(1), float(match.group(2))))

            if time.time() - start_time > time_limit:
                log.warning(f"Scan timeout reached for {band}")
                process.terminate()
                break
    except Exception as e:
        log.error(f"Error during scan: {e}")
        process.kill()

    return found_in_band

def calibrate_channel(channel):
    """
    Runs kal -c and prints output in real-time.
    Returns: (success_bool, ppm_float_or_none, display_string)
    """
    log.info(f"Calibrating on Channel {channel}")
    print(f"\n--- Starting Real-Time Calibration on Channel {channel} ---")
    cmd = ['kal', '-c', str(channel), '-g', DEFAULT_GAIN]
    
    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        universal_newlines=True
    )

    ppm_val = None
    cal_start = time.time()

    try:
        while True:
            line = process.stdout.readline()
            if not line and process.poll() is not None:
                break
            
            if line:
                print(f"  [kal]: {line.strip()}")
                match = re.search(r"average absolute error:\s+([-+]?[\d.]+)\s+ppm", line)
                if match:
                    ppm_val = float(match.group(1))

            if time.time() - cal_start > 60:
                log.error("Calibration timeout (60s) exceeded.")
                process.terminate()
                return False, None, "0 (error calibrating - timeout)"

    except Exception as e:
        log.error(f"Unexpected error in calibration: {traceback.format_exc()}")
        process.kill()
        return False, None, f"0 (error calibrating - {str(e)})"

    if ppm_val is not None:
        return True, ppm_val, f"{ppm_val} ppm"
    else:
        return False, None, "0 (error calibrating - no ppm found)"

def main() -> int:
    # 1. Hardware Check
    success, msg = check_hackrf_status()
    if not success:
        log.error(f"Abort: {msg}")
        print(f"ABORT: {msg}")
        return 1

    bands = ["GSM850", "GSM-R", "GSM900"]
    all_peaks = []
    start_program = time.time()
    
    SCAN_TIME_LIMIT = 90
    PEAK_LIMIT = 10

    # 2. Scanning Phase
    for band in bands:
        if (time.time() - start_program) > SCAN_TIME_LIMIT:
            break
        if len(all_peaks) >= PEAK_LIMIT:
            break
            
        found = run_kal_scan(band, start_program, SCAN_TIME_LIMIT)
        all_peaks.extend(found)

    if not all_peaks:
        log.warning("No GSM peaks found. Skipping calibration.")
        print("\nResult: No peaks found across all bands.")
        return 1

    # 3. Sort and Select Best
    all_peaks.sort(key=lambda x: x[1], reverse=True)
    best_channel = all_peaks[0][0]
    
    log.info(f"Strongest peak found on Channel {best_channel}")
    
    # 4. Calibration Phase
    cal_success, ppm_float, ppm_display = calibrate_channel(best_channel)

    # 5. Result and Persistence
    print("\n" + "="*40)
    print(f"FINAL CALIBRATION REPORT")
    print(f"Status:        {'SUCCESS' if cal_success else 'FAILED'}")
    print(f"Channel Used:  {best_channel}")
    print(f"PPM Error:     {ppm_display}")
    print("="*40)

    if cal_success and ppm_float is not None:
        try:
            store = ShmStore()
            # Persist PPM float
            store.add_to_persistent("ppm_error", float(ppm_float))
            # Persist Timestamp using cfg helper
            store.add_to_persistent("last_kal_ms", cfg.get_time_ms())
            
            log.info(f"Calibration successful. Saved {ppm_float:.3f} ppm to shared memory.")
            return 0
        except Exception:
            log.error(f"Error saving to ShmStore:\n{traceback.format_exc()}")
            return 1
    else:
        log.error(f"Calibration failed or returned no value. Result: {ppm_display}")
        return 1

if __name__ == "__main__":
    rc = cfg.run_and_capture(main)
    sys.exit(rc)