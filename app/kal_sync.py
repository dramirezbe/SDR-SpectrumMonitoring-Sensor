#!/usr/bin/env python3
"""
@file kal_sync.py
@brief Uses kalibrate-hackrf to calibrate SDR clock offset.
"""
import cfg
from cfg import KalState
from utils import run_and_capture

import subprocess
import traceback
import sys
import pandas as pd
import re
from typing import Tuple, Optional

from utils import modify_tmp

log = cfg.get_logger()

KAL_LOGS_DIR = cfg.LOGS_DIR / "kal"
CSV_FILE = KAL_LOGS_DIR / "last_scan.csv"

if cfg.VERBOSE:
    CMD_SCAN = [
        ["kal", "-s", "GSM900", "-v"],
        ["kal", "-s", "GSM850", "-v"],
        ["kal", "-s", "GSM-R", "-v"],
    ]
else:
    CMD_SCAN = [
        ["kal", "-s", "GSM900"],
        ["kal", "-s", "GSM850"],
        ["kal", "-s", "GSM-R"],
    ]


def call_scan() -> int:
    """
    Run kal scans for each band in CMD_SCAN, parse results and write last_scan.csv.

    CSV columns: scan, chan (int), freq_hz (float), bw_hz (float), power (float)

    Return:
      0 -> success (CSV updated if channels found; or no channels but no failures)
      1 -> failure (binary missing or at least one kal command failed); CSV NOT modified
    """

    unit_map = {"MHz": 1e6, "kHz": 1e3, "Hz": 1.0}
    out_rows = []
    any_failure = False

    # Matches: chan:  123 (959.6MHz - 17.082kHz)    power:  222034.66
    line_re = re.compile(
        r"chan:\s*(\d+)\s*\(\s*([\d.]+)\s*(MHz|kHz|Hz)\s*[+-]\s*([\d.]+)\s*(MHz|kHz|Hz)?\s*\)\s*power:\s*([\d.]+)",
        re.IGNORECASE,
    )

    for cmd in CMD_SCAN:
        try:
            result = subprocess.run(cmd, capture_output=True, text=True)
        except FileNotFoundError:
            log.error("'kal' not found. Please install kalibrate-hackrf.")
            return 1
        except Exception:
            log.error(f"Unexpected error running {' '.join(cmd)}:\n{traceback.format_exc()}")
            any_failure = True
            continue

        if result.returncode != 0:
            output = (result.stdout or "") + (result.stderr or "")
            log.error(f"Command {' '.join(cmd)} failed (rc={result.returncode}). Output: {output.strip()}")
            any_failure = True
            continue

        stdout_lines = (result.stdout or "").splitlines()

        # Prefer header like: "kal: Scanning for GSM-900 base stations."
        scan_name = None
        for l in stdout_lines:
            m_header = re.match(r"kal:\s*Scanning for\s+([^\n\r]+)\s+base stations\.?", l, re.IGNORECASE)
            if m_header:
                scan_name = m_header.group(1).strip()
                break
        if not scan_name:
            scan_name = " ".join(cmd)

        for l in stdout_lines:
            m = line_re.search(l)
            if not m:
                continue
            chan = int(m.group(1))
            freq_val = float(m.group(2))
            freq_unit = m.group(3)
            bw_val = float(m.group(4))
            bw_unit = m.group(5) if m.group(5) else "kHz"
            power = float(m.group(6))

            freq_hz = freq_val * unit_map.get(freq_unit, 1e6)
            bw_hz = bw_val * unit_map.get(bw_unit, 1e3)

            out_rows.append({
                "scan": scan_name,
                "chan": chan,
                "freq_hz": freq_hz,
                "bw_hz": bw_hz,
                "power": power,
            })

        if cfg.VERBOSE:
            log.info(f"Completed {' '.join(cmd)} OK.")

    # If any failure occurred, do not touch CSV and return failure
    if any_failure:
        log.error("One or more kal commands failed — not modifying last_scan.csv.")
        return 1

    # If no channels parsed, do not modify existing CSV
    if not out_rows:
        if cfg.VERBOSE:
            log.info("No channels found; leaving last_scan.csv unchanged.")
        return 0

    # Write dataframe (overwrite). Note: we do NOT write an offset comment into CSV.
    try:
        CSV_FILE.parent.mkdir(parents=True, exist_ok=True)
        df = pd.DataFrame(out_rows, columns=["scan", "chan", "freq_hz", "bw_hz", "power"])
        with CSV_FILE.open('w') as f:
            df.to_csv(f, index=False)

        if cfg.VERBOSE:
            log.info(f"Wrote {len(df)} rows to {CSV_FILE}")

    except Exception:
        log.error(f"Failed writing CSV {CSV_FILE}:\n{traceback.format_exc()}")
        return 1

    return 0


def calibrate() -> Tuple[int, Optional[float]]:
    """
    Read CSV_FILE, pick the strongest channel, run `kal -c <chan>`, parse the
    measured offset and return (err, offset_hz).

    Returns:
      (0, offset_hz) on success
      (1, None) on failure
    """
    # Ensure CSV exists
    if not CSV_FILE.exists():
        log.error(f"{CSV_FILE} does not exist; cannot calibrate.")
        return 1, None

    try:
        # comment='#' is fine but we expect no offset line; still keep for robustness
        df = pd.read_csv(CSV_FILE, comment='#')
    except Exception:
        log.error(f"Failed reading {CSV_FILE}:\n{traceback.format_exc()}")
        return 1, None

    if df.empty:
        log.error(f"{CSV_FILE} is empty; cannot calibrate.")
        return 1, None

    # pick the row with max power
    idx = df["power"].idxmax()
    best = df.loc[idx]
    chan = int(best["chan"])
    freq_hz = float(best["freq_hz"])  # used if kal prints ppm
    scan_name = str(best.get("scan", ""))

    if cfg.VERBOSE:
        log.info(f"Calibrating using best peak: scan={scan_name}, chan={chan}, freq_hz={freq_hz}, power={best['power']}")

    # run kal calibration
    try:
        result = subprocess.run(["kal", "-c", str(chan)], capture_output=True, text=True)
    except FileNotFoundError:
        log.error("'kal' not found. Please install kalibrate-hackrf.")
        return 1, None
    except Exception:
        log.error(f"Unexpected error running kal for calibration:\n{traceback.format_exc()}")
        return 1, None

    if result.returncode != 0:
        output = (result.stdout or "") + (result.stderr or "")
        log.error(f"Calibration command failed (rc={result.returncode}). Output: {output.strip()}")
        return 1, None

    out = (result.stdout or "") + "\n" + (result.stderr or "")

    # Try to find the main "average" offset reported by kal.
    lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
    offset_hz: Optional[float] = None

    # pattern captures optional sign, number and unit
    num_unit_re = re.compile(r'([+-]?\s*[\d.]+)\s*(kHz|Hz|MHz|ppm)\b', re.IGNORECASE)

    for i, ln in enumerate(lines):
        if "average" in ln.lower():
            # look on the same line first
            m = num_unit_re.search(ln)
            if not m:
                # look at next 1-3 lines for the numeric value
                for j in range(i+1, min(i+4, len(lines))):
                    m = num_unit_re.search(lines[j])
                    if m:
                        break
            if m:
                val_str = m.group(1).replace(" ", "")
                unit = m.group(2).lower()
                try:
                    val = float(val_str)
                except Exception:
                    log.error(f"Failed to parse numeric offset '{val_str}' from kal output.")
                    return 1, None

                if unit == "ppm":
                    offset_hz = val * freq_hz / 1e6
                elif unit == "khz":
                    offset_hz = val * 1e3
                elif unit == "mhz":
                    offset_hz = val * 1e6
                else:  # Hz
                    offset_hz = val * 1.0

                break

    # Fallback: if we didn't find the 'average' block, try to pick the first matched numeric unit anywhere
    if offset_hz is None:
        for ln in lines:
            m = num_unit_re.search(ln)
            if m:
                val_str = m.group(1).replace(" ", "")
                unit = m.group(2).lower()
                try:
                    val = float(val_str)
                except Exception:
                    continue
                if unit == "ppm":
                    offset_hz = val * freq_hz / 1e6
                elif unit == "khz":
                    offset_hz = val * 1e3
                elif unit == "mhz":
                    offset_hz = val * 1e6
                else:
                    offset_hz = val * 1.0
                break

    if offset_hz is None:
        log.error("Could not parse offset from kal output.")
        if cfg.VERBOSE:
            log.debug("kal stdout/stderr:\n" + out)
        return 1, None

    if cfg.VERBOSE:
        ppm_equiv = offset_hz * 1e6 / freq_hz
        if cfg.VERBOSE:
            log.info(f"Parsed offset: {offset_hz:.3f} Hz ({offset_hz/1e3:.3f} kHz, {ppm_equiv:.3f} ppm)")

    return 0, float(offset_hz)


def main() -> int:
    args = sys.argv
    if len(args) < 2:
        log.error("Usage: kal_sync.py <state>")
        return 1

    arg = str(args[1]).lower()

    if arg == "scan":
        state = KalState.KAL_SCANNING
    elif arg == "calibrate":
        state = KalState.KAL_CALIBRATING
    else:
        log.error(f"Unknown state: {arg}, Usage: kal_sync.py <scan|calibrate>")
        return 1

    rc = 1  # Default to failure
    match state:
        case KalState.KAL_SCANNING:
            rc = call_scan()

        case KalState.KAL_CALIBRATING:
            # Step 1: Run calibration to get the offset value
            rc_cal, offset_hz = calibrate()

            if rc_cal == 0 and offset_hz is not None:
               
                try:
                    if cfg.TMP_FILE is None:
                        log.error("cfg.TMP_FILE is not defined; cannot persist offset to vars.json.")
                        return 1

                    # modify_tmp returns 0 on success, 1 on failure per your util's contract
                    rc_json = modify_tmp("last_offset_hz", float(offset_hz), cfg.TMP_FILE)
                    if rc_json != 0:
                        log.error("Failed saving offset into vars.json (modify_tmp returned non-zero).")
                        return rc_json

                    if cfg.VERBOSE:
                        log.info(f"Calibration successful. Offset {offset_hz:.3f} Hz saved to {cfg.TMP_FILE}")
                    return 0

                except Exception:
                    log.error(f"Unexpected error saving offset to vars.json:\n{traceback.format_exc()}")
                    return 1

            elif rc_cal != 0:
                log.error("Calibration failed.")
                return rc_cal
            else:
                log.error("Calibration returned success but no offset.")
                return 1

    return rc


if __name__ == "__main__":
    rc = run_and_capture(main, log, KAL_LOGS_DIR, cfg.get_time_ms(), cfg.LOG_FILES_NUM)
    sys.exit(rc)
