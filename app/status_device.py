# status_device.py
"""
Status_module

Gathers device metrics such as CPU, RAM, Disk usage, Temperature, LTE GPS coordinates, Ping latency to API server, and recent log entries. Returns all data as a dictionary.
"""
from __future__ import annotations
import time
from pathlib import Path
from typing import Dict, Optional, List, Union
import os
import sys
import subprocess

import cfg

from libs import LteHandler
from utils import RequestClient, modify_persist, get_persist_var

log = cfg.set_logger()


class StatusDevice:
    """
    A collection of methods to consult various device
    """
    def __init__(self, disk_path:Path=Path('/'),
                 logs_dir:Path=cfg.LOGS_DIR,
                 lte_handler:LteHandler=LteHandler(cfg.LIB_LTE, verbose=cfg.VERBOSE),
                 logger=log):
        """
        Initializes StatusDevice

        Args:
            disk_path (Path): Path to disk root
            logs_dir (Path): Path to logs directory
            lte_handler (LteHandler): LteHandler object
        """
        self._log = logger
        self.disk_path = disk_path
        self.disk_path_str = str(disk_path)
        self.lte = lte_handler
        self.logs_dir = logs_dir

    def get_cpu_percent(self) -> Dict[str, List[float]]:
        def read_cpu_lines():
            with open("/proc/stat", "r") as f:
                lines = [l for l in f.readlines() if l.startswith("cpu")]
            parsed = []
            for l in lines[1:]:  # skip aggregate 'cpu'
                parts = l.split()
                vals = [int(x) for x in parts[1:]]
                total = sum(vals)
                idle = vals[3] + (vals[4] if len(vals) > 4 else 0)
                parsed.append((total, idle))
            return parsed

        prev = read_cpu_lines()
        time.sleep(0.1)
        cur = read_cpu_lines()

        usage = []
        for (t1, i1), (t2, i2) in zip(prev, cur):
            total_delta = t2 - t1
            idle_delta = i2 - i1
            if total_delta <= 0:
                pct = 0.0
            else:
                pct = (1.0 - idle_delta / total_delta) * 100.0
            usage.append(round(pct, 2))

        return {"cpu": usage}

    def get_ram_swap_mb(self) -> Dict[str, Optional[int]]:
        mem_total = mem_available = swap_total = swap_free = None

        with open("/proc/meminfo", "r") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    mem_total = int(line.split()[1])
                elif line.startswith("MemAvailable:"):
                    mem_available = int(line.split()[1])
                elif line.startswith("SwapTotal:"):
                    swap_total = int(line.split()[1])
                elif line.startswith("SwapFree:"):
                    swap_free = int(line.split()[1])
                if (
                    mem_total is not None
                    and mem_available is not None
                    and swap_total is not None
                    and swap_free is not None
                ):
                    break

        if mem_total is None or mem_available is None:
            ram_mb = None
        else:
            ram_mb = (mem_total - mem_available) // 1024

        if swap_total is None or swap_free is None:
            swap_mb = None
        else:
            swap_mb = (swap_total - swap_free) // 1024

        return {"ram_mb": ram_mb, "swap_mb": swap_mb}

    def get_total_ram_swap_mb(self) -> Dict[str, Optional[int]]:
        mem_total = swap_total = None

        with open("/proc/meminfo", "r") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    mem_total = int(line.split()[1])
                elif line.startswith("SwapTotal:"):
                    swap_total = int(line.split()[1])
                if mem_total is not None and swap_total is not None:
                    break

        return {
            "ram_mb": mem_total // 1024 if mem_total is not None else None,
            "swap_mb": swap_total // 1024 if swap_total is not None else None,
        }

    def get_disk(self) -> dict:
        st = os.statvfs(self.disk_path_str)
        used_bytes = (st.f_blocks - st.f_bfree) * st.f_frsize
        used_mb = used_bytes // (1024 * 1024)
        return {"disk_mb": used_mb}

    def get_total_disk(self) -> dict:
        st = os.statvfs(self.disk_path_str)
        total_bytes = st.f_blocks * st.f_frsize
        total_mb = total_bytes // (1024 * 1024)
        return {"disk_mb": total_mb}

    def get_temp_c(self) -> Dict[str, float]:
        try:
            with open("/sys/class/thermal/thermal_zone0/temp", "r") as f:
                temp_c = int(f.read().strip()) / 1000.0
            return {"temp_c": temp_c}
        except Exception:
            return {"temp_c": -1.0}

    def get_metrics_dict(self) -> dict:
        metrics: Dict = {}
        metrics.update(self.get_cpu_percent())
        metrics.update(self.get_ram_swap_mb())
        metrics.update(self.get_disk())
        metrics.update(self.get_temp_c())
        return metrics

    def get_total_metrics_dict(self) -> dict:
        tot_metrics: Dict = {}
        tot_metrics.update(self.get_total_ram_swap_mb())
        tot_metrics.update(self.get_total_disk())
        return tot_metrics

    def parse_lte_gps(self):
        err_dict = {'lat': None, 'lng': None, 'alt': None}
        if self.lte is None:
            return err_dict

        raw = self.lte.get_gps()
        if raw is None:
            return err_dict

        if isinstance(raw, (bytes, bytearray)):
            try:
                raw = raw.decode("utf-8", errors="ignore")
            except Exception as e:
                self._log.error(f"LTE GPS decode error: {e}")
                return err_dict

        raw = str(raw).strip()

        lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
        gga = None
        for ln in lines:
            if ln.startswith("$GPGGA") or ln.startswith("$GNGGA"):
                gga = ln
                break
            if "$GPGGA" in ln:
                start = ln.find("$GPGGA")
                gga = ln[start:]
                break
            if "$GNGGA" in ln:
                start = ln.find("$GNGGA")
                gga = ln[start:]
                break
        if gga is None:
            if raw.startswith("$GPGGA") or raw.startswith("$GNGGA"):
                gga = raw
            else:
                self._log.error(f"LTE GPS: No GGA sentence found in: {raw!r}")
                return err_dict

        if "*" in gga:
            gga = gga.split("*", 1)[0]

        parts = gga.split(",")
        if len(parts) < 10:
            self._log.error(f"LTE GPS: Incomplete GGA sentence: {gga!r}")
            return err_dict

        lat_str = parts[2]
        lat_hemi = parts[3]
        lon_str = parts[4]
        lon_hemi = parts[5]
        alt_str = parts[9]

        def nmea_to_decimal(nmea_coord: str, hemi: str) -> Optional[float]:
            if not nmea_coord or not hemi:
                self._log.error(f"LTE GPS: Missing NMEA coordinate or hemisphere: coord={nmea_coord!r}, hemi={hemi!r}")
                return None

            coord = nmea_coord.strip()
            if "." not in coord:
                self._log.error(f"LTE GPS: Invalid NMEA coordinate format (no dot): {coord!r}")
                return None

            intpart = coord.split(".", 1)[0]
            if len(intpart) == 4:
                deg_len = 2
            elif len(intpart) == 5:
                deg_len = 3
            else:
                deg_len = 2 if hemi in ("N", "S") else 3

            try:
                deg = int(coord[:deg_len])
                minutes = float(coord[deg_len:])
            except Exception as e:
                self._log.error(f"LTE GPS: Error parsing NMEA coordinate {coord!r}: {e}")
                return None

            decimal = deg + (minutes / 60.0)
            if hemi in ("S", "W"):
                decimal = -decimal
            return decimal

        lat = nmea_to_decimal(lat_str, lat_hemi)
        if lat is None:
            self._log.error(f"LTE GPS: Failed to parse latitude from {lat_str!r} {lat_hemi!r}")
            return err_dict

        lng = nmea_to_decimal(lon_str, lon_hemi)
        if lng is None:
            self._log.error(f"LTE GPS: Failed to parse longitude from {lon_str!r} {lon_hemi!r}")
            return err_dict

        try:
            alt = float(alt_str) if alt_str != "" else None
        except Exception:
            self._log.error(f"LTE GPS: Failed to parse altitude from {alt_str!r}")
            alt = None

        return {"lat": lat, "lng": lng, "alt": alt}

    def get_ping_latency(self, ip: str):
        cmd = ["ping", "-c", "1", "-W", "1", ip]
        err_dict = {"ping_ms": None}
        try:
            output = subprocess.check_output(cmd, stderr=subprocess.STDOUT, text=True)
        except subprocess.CalledProcessError:
            return err_dict

        for line in output.splitlines():
            if "time=" in line:
                try:
                    time_part = line.split("time=")[1]
                    time_str = time_part.split()[0]
                    latency_ms = float(time_str)
                    return {"ping_ms": latency_ms}
                except Exception:
                    return err_dict
        return err_dict

    def get_logs(self):
        """
        Read ALL .log files directly in logs_dir (no subfolders).
        Filenames format is expected: <timestamp_ms>_<modulename>.log
        Files containing [[OK]] are ignored for log aggregation (but not read).
        Returns tuple: (None, None, logs_str) — timestamps are taken from persistence elsewhere.
        """
        result_logs = ""
        max_lines = 10
        logs_lines: List[str] = []

        if not self.logs_dir.exists() or not self.logs_dir.is_dir():
            return None, None, result_logs

        for p in self.logs_dir.iterdir():
            if not p.is_file() or p.suffix != ".log":
                continue

            try:
                text = p.read_text(encoding="utf-8", errors="ignore")
            except Exception as e:
                self._log.error(f"Error reading log file {p}: {e}")
                continue

            # If file contains [[OK]] skip it (we don't use it to build logs text)
            if "[[OK]]" in text:
                continue

            # include file's lines for aggregation
            file_lines = text.splitlines()
            logs_lines.extend(file_lines)

        if logs_lines:
            logs_lines = logs_lines[-max_lines:]
            result_logs = "\n".join(logs_lines)

        # We no longer compute last_kal_ms/last_ntp_ms here.
        return None, None, result_logs

    def get_final_dict(self):

        # Metrics (live)
        metrics = self.get_metrics_dict()

        # Total metrics
        total_metrics = self.get_total_metrics_dict()

        # GPS
        gps = self.parse_lte_gps() or {}

        # Ping
        ping_dict = self.get_ping_latency(cfg.API_IP) or {}
        ping_ms = ping_dict.get("ping_ms")

        # Logs (we only aggregate last lines here)
        _, _, logs_str = self.get_logs()

        # IMPORTANT: last_kal_ms and last_ntp_ms are read from persistent storage
        last_kal_ms = get_persist_var("last_kal_ms", cfg.PERSIST_FILE)
        last_ntp_ms = get_persist_var("last_ntp_ms", cfg.PERSIST_FILE)

        # dummy
        delta_t_ms = get_persist_var("last_delta_ms", cfg.PERSIST_FILE)

        final_dict: Dict[str, object] = {
            "metrics": metrics,
            "total_metrics": total_metrics,
            "gps": gps,
            "delta_t_ms": delta_t_ms,
            "ping_ms": ping_ms,
            "timestamp_ms": cfg.get_time_ms(),
            "last_kal_ms": last_kal_ms,
            "last_ntp_ms": last_ntp_ms,
            "logs": logs_str,
        }

        log.info(f"Final dict status: {final_dict}")

        return final_dict


def main() -> int:
    lte = LteHandler(cfg.LIB_LTE, verbose=cfg.VERBOSE)
    status_obj = StatusDevice(disk_path=Path('/'), logs_dir=cfg.LOGS_DIR, lte_handler=lte, logger=log)
    client = RequestClient(cfg.API_URL, timeout=(5, 15), verbose=cfg.VERBOSE, logger=log, api_key=cfg.API_KEY)

    start_delta = cfg.get_time_ms()
    rc, resp = client.post_json(cfg.STATUS_URL, status_obj.get_final_dict())
    delta = cfg.get_time_ms() - start_delta

    log.info(f"POST request rc={rc} time={delta}ms")

    rc_json = modify_persist("last_delta_ms", int(delta), cfg.PERSIST_FILE)
    if rc_json != 0:
        log.error(f"Error writing last_delta_ms to tmp file: {rc_json}")
        return rc_json

    if resp is not None and rc == 0:
        preview = resp.text[:200] + ("..." if len(resp.text) > 200 else "")
        log.info(f"POST response code={resp.status_code} preview={preview}")
    else:
        log.error("No response received or error in POST request.")
        return rc

    return 0


if __name__ == "__main__":
    rc = cfg.run_and_capture(main, cfg.LOG_FILES_NUM)
    sys.exit(rc)
