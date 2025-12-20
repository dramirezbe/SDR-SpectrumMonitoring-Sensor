# utils/status_util.py
"""
Status_module

Gathers device metrics such as CPU, RAM, Disk usage, Temperature, LTE GPS coordinates, 
Ping latency to API server, and recent log entries. Returns all data as a dictionary.
Includes retry logic and sleeps to ensure data integrity.
"""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
import time
import os
import subprocess
import logging
from pathlib import Path
from typing import Dict, List, Any

@dataclass
class StatusPost:
    mac: str
    ram_mb: int
    swap_mb: int
    disk_mb: int
    temp_c: float
    total_ram_mb: int
    total_swap_mb: int
    total_disk_mb: int
    delta_t_ms: int
    ping_ms: float
    timestamp_ms: int
    last_kal_ms: int
    last_ntp_ms: int
    logs: str
    
    # cpu_loads is internal; it will be flattened to cpu_0, cpu_1... in to_dict().
    cpu_loads: List[float] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]):
        """
        Custom constructor to handle dynamic flat keys (cpu_0, cpu_1...).
        """
        known_fields = {
            "mac", "ram_mb", "swap_mb", "disk_mb", "temp_c",
            "total_ram_mb", "total_swap_mb", "total_disk_mb",
            "delta_t_ms", "ping_ms", "timestamp_ms",
            "last_kal_ms", "last_ntp_ms", "logs"
        }
        
        # Filter for known fields so unexpected keys do not break initialization.
        init_args = {k: v for k, v in data.items() if k in known_fields}
        obj = cls(**init_args)
        
        # Dynamically find and sort CPU keys so cpu_0..cpu_n are ordered.
        cpu_keys = [k for k in data.keys() if k.startswith("cpu_") and k[4:].isdigit()]
        cpu_keys.sort(key=lambda x: int(x.split('_')[1]))
        
        obj.cpu_loads = [data[k] for k in cpu_keys]
        
        return obj

    def to_dict(self) -> Dict[str, Any]:
        """
        Converts the dataclass back to a dictionary with flattened CPU keys.
        Matches the required JSON output format.
        """
        data = asdict(self)
        
        # Remove the list field from the output so it can be flattened.
        if "cpu_loads" in data:
            del data["cpu_loads"]
            
        # Flatten cpu_loads list back into cpu_0, cpu_1, etc.
        for idx, usage in enumerate(self.cpu_loads):
            data[f"cpu_{idx}"] = usage
            
        return data

class StatusDevice:
    """
    A collection of methods to consult various device metrics.
    Includes retry mechanisms to avoid null data on busy I/O.
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
                            ping_ip: str = "8.8.8.8") -> Dict[str, Any]:
        """
        Aggregates metrics and returns a dictionary matching the StatusPost JSON structure.
        """
        snapshot = {}

        # 1. Static/External Metadata.
        snapshot["mac"] = mac
        snapshot["timestamp_ms"] = timestamp_ms
        snapshot["delta_t_ms"] = delta_t_ms
        snapshot["last_kal_ms"] = last_kal_ms
        snapshot["last_ntp_ms"] = last_ntp_ms

        # 2. CPU: flatten list into cpu_0, cpu_1...
        cpu_data = self.get_cpu_percent()
        cpu_list = cpu_data.get("cpu", [])[:4] # Limit to first 4 CPUs even if there are more
        for idx, usage in enumerate(cpu_list):
            snapshot[f"cpu_{idx}"] = usage

        # 3. RAM & Swap.
        snapshot.update(self.get_ram_swap_mb())

        # 4. Disk.
        snapshot.update(self.get_disk())

        # 5. Temperature.
        snapshot.update(self.get_temp_c())

        # 6. Totals.
        totals_mem = self.get_total_ram_swap_mb()
        snapshot["total_ram_mb"] = totals_mem.get("ram_mb") or 0
        snapshot["total_swap_mb"] = totals_mem.get("swap_mb") or 0

        total_disk = self.get_total_disk()
        snapshot["total_disk_mb"] = total_disk.get("disk_mb") or 0

        # 7. Ping.
        snapshot.update(self.get_ping_latency(ping_ip))

        # 8. Logs.
        _, _, logs_text = self.get_logs()
        snapshot["logs"] = logs_text

        # 9. Serialize via Dataclass to ensure strict format.
        return StatusPost.from_dict(snapshot).to_dict()

    def get_cpu_percent(self) -> Dict[str, List[float]]:
        """
        Calculates CPU usage by reading /proc/stat twice (or more) with retries.
        Robust against cases where counters don't advance in short windows.
        """
        def read_cpu_lines():
            """
            Reads per-core cumulative counters from /proc/stat.

            Returns:
                List[(total_jiffies, idle_jiffies)] for cpu0..cpuN.
                Skips the first aggregated "cpu" line and only keeps per-core lines.
            """
            try:
                with open("/proc/stat", "r") as f:
                    lines = [l for l in f.readlines() if l.startswith("cpu")]
            except Exception:
                # If /proc/stat is not readable, return no CPU data.
                return []
                
            parsed = []
            # lines[0] is the aggregate 'cpu' line.
            for l in lines[1:]:  # skip aggregate 'cpu' line
                parts = l.split()
                if len(parts) < 5:
                    # Not enough fields to compute idle/total reliably.
                    continue
                vals = [int(x) for x in parts[1:]]
                total = sum(vals)
                idle = vals[3] + (vals[4] if len(vals) > 4 else 0)  # idle + iowait
                parsed.append((total, idle))
            return parsed

        try:
            prev = read_cpu_lines()

            if not prev:
                return {"cpu": []}

            max_tries = 5
            # Initial sleep between reads, set to 1s for better stability.
            sleep_s = 1.0

            for _ in range(max_tries):
                time.sleep(sleep_s)
                cur = read_cpu_lines()

                if not cur or len(cur) != len(prev):
                    sleep_s = min(sleep_s * 2.0, 1.5)
                    prev = cur if cur else prev
                    continue

                usage = []
                any_progress = False

                for (t1, i1), (t2, i2) in zip(prev, cur):
                    total_delta = t2 - t1
                    idle_delta = i2 - i1

                    if total_delta > 0:
                        any_progress = True
                        pct = (1.0 - (idle_delta / total_delta)) * 100.0
                        usage.append(round(pct, 3))
                    else:
                        usage.append(0.0)
                if any_progress:
                    return {"cpu": usage}
                
                # If there was no progress, increase sleep and retry.
                sleep_s = min(sleep_s * 2.0, 1.5)
                prev = cur
            return {"cpu": []}
        except Exception:
            return {"cpu": []}

    def get_ram_swap_mb(self) -> Dict[str, int]:
        """
        Reads /proc/meminfo. Retries up to 3 times if file read returns incomplete data.
        """
        mem_total = mem_available = swap_total = swap_free = None
        
        # Retry mechanism to avoid null data.
        for _ in range(3):
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
                    
                    # If we successfully found the keys, break loop.
                    if (mem_total is not None and mem_available is not None):
                        break
            except Exception:
                # Small sleep before retry.
                time.sleep(0.05)
                continue

        ram_mb = 0
        if mem_total is not None and mem_available is not None:
            ram_mb = (mem_total - mem_available) // 1024

        swap_mb = 0
        if swap_total is not None and swap_free is not None:
            swap_mb = (swap_total - swap_free) // 1024

        return {"ram_mb": ram_mb, "swap_mb": swap_mb}

    def get_total_ram_swap_mb(self) -> Dict[str, int]:
        mem_total = swap_total = None
        # Retry mechanism.
        for _ in range(3):
            try:
                with open("/proc/meminfo", "r") as f:
                    for line in f:
                        if line.startswith("MemTotal:"):
                            mem_total = int(line.split()[1])
                        elif line.startswith("SwapTotal:"):
                            swap_total = int(line.split()[1])
                if mem_total is not None:
                    break
            except Exception:
                time.sleep(0.05)

        return {
            "ram_mb": mem_total // 1024 if mem_total else 0,
            "swap_mb": swap_total // 1024 if swap_total else 0,
        }

    def get_disk(self) -> dict:
        try:
            st = os.statvfs(self.disk_path_str)
            # Used space is total blocks minus free blocks.
            used_bytes = (st.f_blocks - st.f_bfree) * st.f_frsize
            used_mb = used_bytes // (1024 * 1024)
            return {"disk_mb": used_mb}
        except Exception:
             return {"disk_mb": 0}

    def get_total_disk(self) -> dict:
        try:
            st = os.statvfs(self.disk_path_str)
            # Total space is all blocks.
            total_bytes = st.f_blocks * st.f_frsize
            total_mb = total_bytes // (1024 * 1024)
            return {"disk_mb": total_mb}
        except Exception:
            return {"disk_mb": 0}

    def get_temp_c(self) -> Dict[str, float]:
        """
        Reads thermal zone temp. Retries on failure to handle busy sensors.
        """
        path = "/sys/class/thermal/thermal_zone0/temp"
        for _ in range(3):
            try:
                with open(path, "r") as f:
                    content = f.read().strip()
                    if content:
                        # Kernel exposes millidegrees Celsius.
                        temp_c = int(content) / 1000.0
                        return {"temp_c": temp_c}
            except Exception:
                # Sensor might be busy, wait 50ms and retry.
                time.sleep(0.05)
                continue
        
        return {"temp_c": -1.0}

    def get_ping_latency(self, ip: str):
        cmd = ["ping", "-c", "1", "-W", "1", ip]
        err_dict = {"ping_ms": -1.0}
        try:
            # Run a single ping with a 1s timeout.
            output = subprocess.check_output(cmd, stderr=subprocess.STDOUT, text=True)
        except subprocess.CalledProcessError:
            return err_dict

        for line in output.splitlines():
            if "time=" in line:
                try:
                    # Parse latency from the ping output line.
                    time_part = line.split("time=")[1]
                    time_str = time_part.split()[0]
                    latency_ms = float(time_str)
                    return {"ping_ms": latency_ms}
                except Exception:
                    return err_dict
        return err_dict

    def get_logs(self):
        """
        Retrieves the last 10 lines of logs from the logs directory.
        Iterates files from newest to oldest, filtering out '[[OK]]' flags.
        """
        result_logs = "System running normally"
        max_lines = 10
        collected_lines: List[str] = []

        if not self.logs_dir.exists() or not self.logs_dir.is_dir():
            # If logs directory is missing, return default message.
            return None, None, result_logs

        # 1. Identify and sort log files by timestamp (Newest First).
        # format: <timestamp>_<module>.log
        log_files = []
        for p in self.logs_dir.glob("*.log"):
            try:
                # Extract timestamp from filename stem
                ts_part = p.stem.split('_')[0]
                if ts_part.isdigit():
                    log_files.append((int(ts_part), p))
            except Exception:
                continue
        
        # Sort descending (Newest -> Oldest).
        log_files.sort(key=lambda x: x[0], reverse=True)

        # 2. Collect lines.
        for _, p in log_files:
            try:
                text = p.read_text(encoding="utf-8", errors="ignore")
                lines = text.splitlines()

                # Filter out lines containing [[OK]], keep the rest
                valid_lines = [line for line in lines if "[[OK]]" not in line]

                if not valid_lines:
                    continue

                # Prepend the valid lines from this file to our collection.
                # Since we are iterating New->Old files, the current file's lines 
                # come *after* whatever we process next (which is older).
                # Example state: collected = [NewFileLines] -> [OldFileLines + NewFileLines]
                collected_lines = valid_lines + collected_lines

                # If we have gathered enough lines, trim and stop
                if len(collected_lines) >= max_lines:
                    collected_lines = collected_lines[-max_lines:]
                    break

            except Exception as e:
                # Log read errors but continue with other files.
                self._log.error(f"Error reading log file {p}: {e}")
                continue

        if collected_lines:
            result_logs = "\n".join(collected_lines)

        # Returns None, None, str to match expected signature in get_status_snapshot.
        return None, None, result_logs