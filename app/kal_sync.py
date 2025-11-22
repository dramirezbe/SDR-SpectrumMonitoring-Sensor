#!/usr/bin/env python3
"""
kal_sync.py

Calibrate SDR clock offset using kalibrate-hackrf.
Workflow: Scans for GSM base stations. Stops immediately upon finding 
the FIRST peak (kills scan subprocess), then calibrates against it.

No arguments required.
"""

from __future__ import annotations
import subprocess
import traceback
import sys
import re
from dataclasses import dataclass
from typing import List, Optional, Dict, Any

import cfg
from utils import modify_persist

log = cfg.set_logger()

@dataclass
class CalResult:
    rc: int
    offset_ppm: Optional[float] = None
    offset_hz: Optional[float] = None


class KalSync:
    UNIT_MAP = {"MHz": 1e6, "kHz": 1e3, "Hz": 1.0}
    # regex to capture lines like:
    # chan:  123 (959.6MHz - 17.082kHz)    power:  222034.66
    LINE_RE = re.compile(
        r"chan:\s*(\d+)\s*\(\s*([\d.]+)\s*(MHz|kHz|Hz)\s*[+-]\s*([\d.]+)\s*(MHz|kHz|Hz)?\s*\)\s*power:\s*([\d.]+)",
        re.IGNORECASE,
    )
    # regex to capture lines like:
    # average: 17.082kHz
    NUM_UNIT_RE = re.compile(r"([+-]?\s*[\d.]+)\s*(kHz|Hz|MHz|ppm)\b", re.IGNORECASE)

    def __init__(self):
        self.cmds = self._build_cmds()

    @staticmethod
    def _build_cmds() -> List[List[str]]:
        if cfg.VERBOSE:
            return [["kal", "-s", "GSM900", "-v"], ["kal", "-s", "GSM850", "-v"], ["kal", "-s", "GSM-R", "-v"]]
        return [["kal", "-s", "GSM900"], ["kal", "-s", "GSM850"], ["kal", "-s", "GSM-R"]]

    def _run(self, cmd: List[str]) -> subprocess.CompletedProcess:
        return subprocess.run(cmd, capture_output=True, text=True)

    def scan_and_find_first(self) -> Optional[Dict[str, Any]]:
        """
        Runs kalibrate scans. Reads output in real-time.
        As soon as a peak is found, terminates the subprocess and returns the peak info.
        """
        log.info("Starting GSM Scan (stopping on first peak)...")

        for cmd in self.cmds:
            scan_name = " ".join(cmd)
            log.info(f"Running: {scan_name}")
            
            # Use Popen to read output line-by-line
            try:
                # bufsize=1 means line buffered
                with subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1) as proc:
                    found_peak = None
                    
                    try:
                        # Read line by line
                        for line in proc.stdout:
                            # Check for channel info
                            m = self.LINE_RE.search(line)
                            if m:
                                chan = int(m.group(1))
                                freq_val = float(m.group(2))
                                freq_unit = m.group(3)
                                power = float(m.group(6))
                                freq_hz = freq_val * self.UNIT_MAP.get(freq_unit, 1e6)

                                found_peak = {
                                    "scan": scan_name, 
                                    "chan": chan, 
                                    "freq_hz": freq_hz, 
                                    "power": power
                                }
                                log.info(f"Peak found: {line.strip()}")
                                break  # Break the loop to terminate
                    except Exception as e:
                        log.error(f"Error reading output from {scan_name}: {e}")
                    
                    # If we broke out or finished, we're here. 
                    # Terminate the process if it's still running.
                    if proc.poll() is None:
                        proc.terminate()
                        try:
                            proc.wait(timeout=1)
                        except subprocess.TimeoutExpired:
                            proc.kill()
                    
                    if found_peak:
                        return found_peak

            except FileNotFoundError:
                log.error("'kal' not found. Please install kalibrate-hackrf.")
                return None
            except Exception:
                log.error(f"Unexpected error running {scan_name}:\n{traceback.format_exc()}")
                continue

        log.info("No GSM channels found after checking all bands.")
        return None

    def _parse_offset_from_output(self, out: str, freq_hz: float) -> Optional[float]:
        lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
        
        # search for lines containing "average" first
        for i, ln in enumerate(lines):
            if "average" in ln.lower():
                m = self.NUM_UNIT_RE.search(ln)
                if not m:
                    # look ahead slightly
                    for j in range(i + 1, min(i + 4, len(lines))):
                        m = self.NUM_UNIT_RE.search(lines[j])
                        if m:
                            break
                if m:
                    return self._convert_to_hz(m, freq_hz)

        # fallback to first number found
        for ln in lines:
            m = self.NUM_UNIT_RE.search(ln)
            if m:
                return self._convert_to_hz(m, freq_hz)
        return None

    def _convert_to_hz(self, m: re.Match, freq_hz: float) -> Optional[float]:
        val_str = m.group(1).replace(" ", "")
        unit = m.group(2).lower()
        try:
            val = float(val_str)
        except Exception:
            return None
        
        if unit == "ppm":
            return val * freq_hz / 1e6
        if unit == "khz":
            return val * 1e3
        if unit == "mhz":
            return val * 1e6
        return val * 1.0  # Hz

    def execute_calibration(self, best_peak: Dict[str, Any]) -> CalResult:
        chan = best_peak["chan"]
        freq_hz = best_peak["freq_hz"]
        power = best_peak["power"]
        
        log.info(f"Calibrating against found peak: Chan {chan} ({freq_hz/1e6:.1f} MHz) Power: {power}")

        try:
            # Run calibration on specific channel
            proc = self._run(["kal", "-c", str(chan)])
        except FileNotFoundError:
            log.error("'kal' not found.")
            return CalResult(1)
        except Exception:
            log.error(f"Unexpected error running kal for calibration:\n{traceback.format_exc()}")
            return CalResult(1)

        out = (proc.stdout or "") + "\n" + (proc.stderr or "")
        
        # Get Offset in Hz
        offset_hz = self._parse_offset_from_output(out, freq_hz)

        if offset_hz is None:
            log.error("Could not parse offset from kal output.")
            log.debug("kal stdout/stderr:\n" + out)
            return CalResult(1)

        # Calculate PPM
        if freq_hz == 0:
            log.error("Frequency is zero, cannot calculate PPM.")
            return CalResult(1)

        ppm = (offset_hz / freq_hz) * 1e6
        
        log.info(f"Result: {offset_hz:.2f} Hz offset @ {freq_hz/1e6:.1f} MHz = {ppm:.3f} PPM")
        
        return CalResult(0, offset_ppm=ppm, offset_hz=offset_hz)

    def run(self) -> int:
        # Step 1: Scan and find FIRST peak in memory
        first_peak = self.scan_and_find_first()
        
        if not first_peak:
            log.warning("No GSM peaks found. Skipping calibration.")
            return 1 

        # Step 2: Calibrate using that peak
        res = self.execute_calibration(first_peak)

        if res.rc != 0 or res.offset_ppm is None:
            log.error("Calibration failed. Variables will not be updated.")
            return res.rc

        # Step 3: Persist Result (Only on success)
        try:
            if cfg.PERSIST_FILE is None:
                log.error("cfg.PERSIST_FILE is not defined; cannot persist offset.")
                return 1

            # Save PPM
            rc_json = modify_persist("last_offset_ppm", float(res.offset_ppm), cfg.PERSIST_FILE)
            if rc_json != 0:
                log.error("Failed saving last_offset_ppm into vars.json.")
                return rc_json

            # Save Timestamp
            modify_persist("last_kal_ms", cfg.get_time_ms(), cfg.PERSIST_FILE)
            
            log.info(f"Calibration successful. Offset {res.offset_ppm:.3f} ppm saved.")
            return 0
        except Exception:
            log.error(f"Unexpected error saving offset:\n{traceback.format_exc()}")
            return 1


def main() -> int:
    return KalSync().run()


if __name__ == "__main__":
    rc = cfg.run_and_capture(main, cfg.LOG_FILES_NUM)
    sys.exit(rc)