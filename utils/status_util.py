# utils/status_util.py
"""
Status_module

Gathers device metrics such as CPU, RAM, Disk usage, Temperature, LTE GPS coordinates, Ping latency to API server, and recent log entries. Returns all data as a dictionary.
"""
from __future__ import annotations
from .request_util import StatusPost
import time
import os
import subprocess
import logging
from pathlib import Path
from typing import Dict, List, Any

# Assuming these are imported from your shared utils file (as provided in prompt)
# from utils.request_util import StatusPost

class StatusDevice:
    """
    A collection of methods to consult various device metrics.
    MAC address calculation is removed; it must be handled externally.
    """
    def __init__(self, disk_path: Path = Path('/'),
                 logs_dir: Path = (Path.cwd() / "Logs"),
                 logger=logging.getLogger(__name__)):
        
        self._log = logger
        self.disk_path = disk_path
        self.disk_path_str = str(disk_path)
        self.logs_dir = logs_dir

    def get_status_snapshot(self, 
                            delta_t_ms: int,
                            last_kal_ms: int,
                            last_ntp_ms: int,
                            timestamp_ms: int,
                            mac: str = "",
                            ping_ip: str = "8.8.8.8") -> StatusPost:
        """
        Aggregates metrics into a StatusPost object.
        """
        snapshot = {}

        # 1. Static/External Metadata
        snapshot["mac"] = mac
        snapshot["timestamp_ms"] = timestamp_ms
        snapshot["delta_t_ms"] = delta_t_ms
        snapshot["last_kal_ms"] = last_kal_ms
        snapshot["last_ntp_ms"] = last_ntp_ms

        # 2. CPU: Flatten keys so 'from_dict' can parse them
        cpu_data = self.get_cpu_percent()
        cpu_list = cpu_data.get("cpu", [])
        for idx, usage in enumerate(cpu_list):
            snapshot[f"cpu_{idx}"] = usage

        # 3. RAM & Swap
        snapshot.update(self.get_ram_swap_mb())

        # 4. Disk
        snapshot.update(self.get_disk())

        # 5. Temperature
        snapshot.update(self.get_temp_c())

        # 6. Totals
        totals_mem = self.get_total_ram_swap_mb()
        snapshot["total_ram_mb"] = totals_mem.get("ram_mb") or 0
        snapshot["total_swap_mb"] = totals_mem.get("swap_mb") or 0

        total_disk = self.get_total_disk()
        snapshot["total_disk_mb"] = total_disk.get("disk_mb") or 0

        # 7. Ping
        snapshot.update(self.get_ping_latency(ping_ip))

        # 8. Logs
        _, _, logs_text = self.get_logs()
        snapshot["logs"] = logs_text

        # 9. Return typed object
        return StatusPost.from_dict(snapshot)

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

        try:
            prev = read_cpu_lines()
            time.sleep(0.1) # Blocking delay for calculation
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
        except Exception:
             return {"cpu": []}

    def get_ram_swap_mb(self) -> Dict[str, int]:
        mem_total = mem_available = swap_total = swap_free = None
        try:
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
                    if (mem_total and mem_available and swap_total and swap_free):
                        break
        except Exception:
            pass

        ram_mb = 0
        if mem_total is not None and mem_available is not None:
            ram_mb = (mem_total - mem_available) // 1024

        swap_mb = 0
        if swap_total is not None and swap_free is not None:
            swap_mb = (swap_total - swap_free) // 1024

        return {"ram_mb": ram_mb, "swap_mb": swap_mb}

    def get_total_ram_swap_mb(self) -> Dict[str, int]:
        mem_total = swap_total = None
        try:
            with open("/proc/meminfo", "r") as f:
                for line in f:
                    if line.startswith("MemTotal:"):
                        mem_total = int(line.split()[1])
                    elif line.startswith("SwapTotal:"):
                        swap_total = int(line.split()[1])
        except Exception:
            pass
        
        return {
            "ram_mb": mem_total // 1024 if mem_total else 0,
            "swap_mb": swap_total // 1024 if swap_total else 0,
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
        err_dict = {"ping_ms": -1.0}
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
        result_logs = "System running normally"
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

            if "[[OK]]" in text:
                continue

            file_lines = text.splitlines()
            logs_lines.extend(file_lines)

        if logs_lines:
            logs_lines = logs_lines[-max_lines:]
            result_logs = "\n".join(logs_lines)

        return None, None, result_logs