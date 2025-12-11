# utils/status_util.py
"""
Status_module

Gathers device metrics such as CPU, RAM, Disk usage, Temperature, LTE GPS coordinates, Ping latency to API server, and recent log entries. Returns all data as a dictionary.
"""
from __future__ import annotations
import time
from pathlib import Path
from typing import Dict, Optional, List, Any
import os
import subprocess
import logging

class StatusDevice:
    """
    A collection of methods to consult various device metrics
    """
    def __init__(self, disk_path: Path = Path('/'),
                 logs_dir: Path = (Path.cwd() / "Logs"),
                 logger=logging.getLogger(__name__)):
        """
        Initializes StatusDevice

        Args:
            disk_path (Path): Path to disk root
            logs_dir (Path): Path to logs directory
        """
        self._log = logger
        self.disk_path = disk_path
        self.disk_path_str = str(disk_path)
        self.logs_dir = logs_dir

    def get_status_snapshot(self, ping_ip: str = "8.8.8.8") -> Dict[str, Any]:
        """
        Aggregates all metrics into a single flat dictionary matching 
        the specific requirement.
        """
        result = {}

        # 1. CPU: Flatten the list 'cpu': [25.5, 30.2] -> 'cpu_0': 25.5, 'cpu_1': 30.2
        cpu_data = self.get_cpu_percent()
        cpu_list = cpu_data.get("cpu", [])
        for idx, usage in enumerate(cpu_list):
            result[f"cpu_{idx}"] = usage

        # 2. RAM & Swap (Used)
        # Returns: {'ram_mb': int, 'swap_mb': int}
        result.update(self.get_ram_swap_mb())

        # 3. Disk (Used)
        # Returns: {'disk_mb': int}
        result.update(self.get_disk())

        # 4. Temperature
        # Returns: {'temp_c': float}
        result.update(self.get_temp_c())

        # 5. Totals (RAM, Swap, Disk)
        # We fetch them, but we need to rename the keys to match spec (prefix with total_)
        totals_mem = self.get_total_ram_swap_mb()
        result["total_ram_mb"] = totals_mem.get("ram_mb")
        result["total_swap_mb"] = totals_mem.get("swap_mb")

        total_disk = self.get_total_disk()
        result["total_disk_mb"] = total_disk.get("disk_mb")

        # 6. Ping
        # Returns: {'ping_ms': float} or None
        result.update(self.get_ping_latency(ping_ip))

        # 7. Logs
        # get_logs returns (None, None, logs_str)
        _, _, logs_text = self.get_logs()
        result["logs"] = logs_text

        return result

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
        try:
            st = os.statvfs(self.disk_path_str)
            used_bytes = (st.f_blocks - st.f_bfree) * st.f_frsize
            used_mb = used_bytes // (1024 * 1024)
            return {"disk_mb": used_mb}
        except Exception:
             return {"disk_mb": 0}

    def get_total_disk(self) -> dict:
        try:
            st = os.statvfs(self.disk_path_str)
            total_bytes = st.f_blocks * st.f_frsize
            total_mb = total_bytes // (1024 * 1024)
            return {"disk_mb": total_mb}
        except Exception:
            return {"disk_mb": 0}

    def get_temp_c(self) -> Dict[str, float]:
        try:
            with open("/sys/class/thermal/thermal_zone0/temp", "r") as f:
                temp_c = int(f.read().strip()) / 1000.0
            return {"temp_c": temp_c}
        except Exception:
            return {"temp_c": -1.0}

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

        return None, None, result_logs