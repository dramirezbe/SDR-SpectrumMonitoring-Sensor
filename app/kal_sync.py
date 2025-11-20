#!/usr/bin/env python3
"""
kal_sync.py

Calibrate SDR clock offset using kalibrate-hackrf.
"""

from __future__ import annotations
import subprocess
import traceback
import sys
import re
from dataclasses import dataclass
from typing import List, Optional

import pandas as pd
import argparse

import cfg
from cfg import KalState
from utils import modify_persist

log = cfg.set_logger()

KAL_LOGS_DIR = cfg.LOGS_DIR / "kal"
CSV_FILE = KAL_LOGS_DIR / "last_scan.csv"


@dataclass
class CalResult:
    rc: int
    offset_hz: Optional[float] = None


class KalSync:
    UNIT_MAP = {"MHz": 1e6, "kHz": 1e3, "Hz": 1.0}
    # regex to capture lines like:
    # chan:  123 (959.6MHz - 17.082kHz)    power:  222034.66
    LINE_RE = re.compile(
        r"chan:\s*(\d+)\s*\(\s*([\d.]+)\s*(MHz|kHz|Hz)\s*[+-]\s*([\d.]+)\s*(MHz|kHz|Hz)?\s*\)\s*power:\s*([\d.]+)",
        re.IGNORECASE,
    )
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

    def scan(self) -> int:
        out_rows = []
        any_failure = False

        for cmd in self.cmds:
            try:
                proc = self._run(cmd)
            except FileNotFoundError:
                log.error("'kal' not found. Please install kalibrate-hackrf.")
                return 1
            except Exception:
                log.error(f"Unexpected error running {' '.join(cmd)}:\n{traceback.format_exc()}")
                any_failure = True
                continue

            if proc.returncode != 0:
                output = (proc.stdout or "") + (proc.stderr or "")
                log.error(f"Command {' '.join(cmd)} failed (rc={proc.returncode}). Output: {output.strip()}")
                any_failure = True
                continue

            lines = (proc.stdout or "").splitlines()
            # get scan header if present
            scan_name = None
            for l in lines:
                m = re.match(r"kal:\s*Scanning for\s+([^\n\r]+)\s+base stations\.?", l, re.IGNORECASE)
                if m:
                    scan_name = m.group(1).strip()
                    break
            if not scan_name:
                scan_name = " ".join(cmd)

            for l in lines:
                m = self.LINE_RE.search(l)
                if not m:
                    continue
                chan = int(m.group(1))
                freq_val = float(m.group(2))
                freq_unit = m.group(3)
                bw_val = float(m.group(4))
                bw_unit = m.group(5) if m.group(5) else "kHz"
                power = float(m.group(6))

                freq_hz = freq_val * self.UNIT_MAP.get(freq_unit, 1e6)
                bw_hz = bw_val * (1e3 if bw_unit.lower() == "khz" else self.UNIT_MAP.get(bw_unit, 1.0))

                out_rows.append(
                    {"scan": scan_name, "chan": chan, "freq_hz": freq_hz, "bw_hz": bw_hz, "power": power}
                )

            log.info(f"Completed {' '.join(cmd)} OK.")

        if any_failure:
            log.error("One or more kal commands failed — not modifying last_scan.csv.")
            return 1

        if not out_rows:
            log.info("No channels found; leaving last_scan.csv unchanged.")
            return 0

        try:
            CSV_FILE.parent.mkdir(parents=True, exist_ok=True)
            df = pd.DataFrame(out_rows, columns=["scan", "chan", "freq_hz", "bw_hz", "power"])
            df.to_csv(CSV_FILE, index=False)
            log.info(f"Wrote {len(df)} rows to {CSV_FILE}")
        except Exception:
            log.error(f"Failed writing CSV {CSV_FILE}:\n{traceback.format_exc()}")
            return 1

        return 0

    def _parse_offset_from_output(self, out: str, freq_hz: float) -> Optional[float]:
        lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
        # search for lines containing "average" first, then fallback to first numeric match
        for i, ln in enumerate(lines):
            if "average" in ln.lower():
                m = self.NUM_UNIT_RE.search(ln)
                if not m:
                    for j in range(i + 1, min(i + 4, len(lines))):
                        m = self.NUM_UNIT_RE.search(lines[j])
                        if m:
                            break
                if m:
                    return self._convert_to_hz(m, freq_hz)

        # fallback
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

    def calibrate(self) -> CalResult:
        if not CSV_FILE.exists():
            log.error(f"{CSV_FILE} does not exist; cannot calibrate.")
            return CalResult(1, None)

        try:
            df = pd.read_csv(CSV_FILE, comment="#")
        except Exception:
            log.error(f"Failed reading {CSV_FILE}:\n{traceback.format_exc()}")
            return CalResult(1, None)

        if df.empty:
            log.error(f"{CSV_FILE} is empty; cannot calibrate.")
            return CalResult(1, None)

        idx = df["power"].idxmax()
        best = df.loc[idx]
        chan = int(best["chan"])
        freq_hz = float(best["freq_hz"])
        scan_name = str(best.get("scan", ""))

        log.info(f"Calibrating using best peak: scan={scan_name}, chan={chan}, freq_hz={freq_hz}, power={best['power']}")

        try:
            proc = self._run(["kal", "-c", str(chan)])
        except FileNotFoundError:
            log.error("'kal' not found. Please install kalibrate-hackrf.")
            return CalResult(1, None)
        except Exception:
            log.error(f"Unexpected error running kal for calibration:\n{traceback.format_exc()}")
            return CalResult(1, None)

        if proc.returncode != 0:
            output = (proc.stdout or "") + (proc.stderr or "")
            log.error(f"Calibration command failed (rc={proc.returncode}). Output: {output.strip()}")
            return CalResult(1, None)

        out = (proc.stdout or "") + "\n" + (proc.stderr or "")
        offset_hz = self._parse_offset_from_output(out, freq_hz)

        if offset_hz is None:
            log.error("Could not parse offset from kal output.")
            log.debug("kal stdout/stderr:\n" + out)
            return CalResult(1, None)

        ppm_equiv = offset_hz * 1e6 / freq_hz
        log.info(f"Parsed offset: {offset_hz:.3f} Hz ({offset_hz/1e3:.3f} kHz, {ppm_equiv:.3f} ppm)")
        return CalResult(0, float(offset_hz))

    def run(self, action: KalState) -> int:
        if action == KalState.KAL_SCANNING:
            return self.scan()

        if action == KalState.KAL_CALIBRATING:
            res = self.calibrate()
            if res.rc != 0 or res.offset_hz is None:
                log.error("Calibration failed.")
                return res.rc

            # save offset to cfg.PERSIST_FILE (preserve previous behavior)
            try:
                if cfg.PERSIST_FILE is None:
                    log.error("cfg.PERSIST_FILE is not defined; cannot persist offset to vars.json.")
                    return 1
                rc_json = modify_persist("last_offset_hz", float(res.offset_hz), cfg.PERSIST_FILE)
                if rc_json != 0:
                    log.error("Failed saving offset into vars.json (modify_persist returned non-zero).")
                    return rc_json
                log.info(f"Calibration successful. Offset {res.offset_hz:.3f} Hz saved to {cfg.PERSIST_FILE}")
            except Exception:
                log.error(f"Unexpected error saving offset to vars.json:\n{traceback.format_exc()}")
                return 1

            # On full success, write last_kal_ms timestamp (two-arg form per your request).
            try:
                rc_ts = modify_persist("last_kal_ms", cfg.get_time_ms(), cfg.PERSIST_FILE)
                if rc_ts != 0:
                    log.error(f"modify_persist('last_kal_ms', ...) returned non-zero rc={rc_ts}")
                    return rc_ts
                log.info("Persisted last_kal_ms successfully.")
            except Exception:
                log.error(f"Failed to persist last_kal_ms:\n{traceback.format_exc()}")
                return 1

            return 0

        log.error(f"Unsupported action: {action}")
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Calibrates SDR clock offset using kalibrate-hackrf (scan|calibrate)."
    )
    parser.add_argument("action", choices=["scan", "calibrate"])
    if len(sys.argv) == 1:
        parser.print_help(sys.stderr)
        return 1
    args = parser.parse_args()

    state = KalState.KAL_SCANNING if args.action == "scan" else KalState.KAL_CALIBRATING
    return KalSync().run(state)


if __name__ == "__main__":
    rc = cfg.run_and_capture(main, cfg.LOG_FILES_NUM)
    sys.exit(rc)
