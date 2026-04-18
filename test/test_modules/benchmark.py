import time
import os
import sys
import csv
import psutil
import subprocess
import threading
from datetime import datetime
import logging
from pathlib import Path
from typing import Optional

import cfg
from utils.io_util import atomic_write_bytes


def _load_bpf_class():
    """Load BPF class lazily so module import works even if bcc is missing."""
    # Allow using distro-packaged bcc from inside a virtualenv.
    py_ver = f"{sys.version_info.major}.{sys.version_info.minor}"
    extra_paths = [
        "/usr/lib/python3/dist-packages",
        f"/usr/lib/python{py_ver}/dist-packages",
        "/usr/local/lib/python3/dist-packages",
        f"/usr/local/lib/python{py_ver}/dist-packages",
    ]
    for p in extra_paths:
        if os.path.isdir(p) and p not in sys.path:
            sys.path.append(p)

    try:
        from bcc import BPF  # type: ignore
        return BPF
    except Exception as exc:
        raise ModuleNotFoundError(
            "No se pudo importar bcc (python bindings para BPF). "
            "Instala el paquete del sistema y ejecútalo con ese Python. "
            "Ubuntu/Debian: sudo apt install bpfcc-tools python3-bpfcc linux-headers-$(uname -r)"
        ) from exc


def _load_bpf_perf_enums():
    """Load BCC perf enums compatible with current bcc version."""
    # Keep lazy import to avoid hard dependency at module import time.
    py_ver = f"{sys.version_info.major}.{sys.version_info.minor}"
    extra_paths = [
        "/usr/lib/python3/dist-packages",
        f"/usr/lib/python{py_ver}/dist-packages",
        "/usr/local/lib/python3/dist-packages",
        f"/usr/local/lib/python{py_ver}/dist-packages",
    ]
    for p in extra_paths:
        if os.path.isdir(p) and p not in sys.path:
            sys.path.append(p)

    try:
        from bcc import PerfType, PerfHWConfig  # type: ignore
        return PerfType, PerfHWConfig
    except Exception as exc:
        raise ModuleNotFoundError(
            "No se pudo importar PerfType/PerfHWConfig desde bcc. "
            "Verifica instalación de python3-bpfcc."
        ) from exc


def _count_online_cpus_linux() -> int:
    """Return online CPU count on Linux, falling back safely when unavailable."""
    online_path = "/sys/devices/system/cpu/online"
    try:
        with open(online_path, "r", encoding="utf-8") as f:
            spec = f.read().strip()
        if not spec:
            raise ValueError("empty cpu online spec")

        count = 0
        for chunk in spec.split(","):
            chunk = chunk.strip()
            if not chunk:
                continue
            if "-" in chunk:
                start_s, end_s = chunk.split("-", 1)
                start = int(start_s)
                end = int(end_s)
                if end >= start:
                    count += (end - start + 1)
            else:
                int(chunk)
                count += 1

        if count > 0:
            return count
    except Exception:
        pass

    fallback = os.cpu_count() or 1
    return max(1, int(fallback))


def _get_cpu_freq_mhz() -> float:
    """Get current CPU frequency in MHz from sysfs."""
    try:
        with open("/sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq", "r", encoding="utf-8") as f:
            freq_khz = int(f.read().strip())
            return freq_khz / 1000.0
    except Exception:
        return 0.0


def _get_temperature_celsius() -> float:
    """Get CPU temperature in Celsius from thermal sysfs."""
    try:
        with open("/sys/class/thermal/thermal_zone0/temp", "r", encoding="utf-8") as f:
            temp_millidegrees = int(f.read().strip())
            return temp_millidegrees / 1000.0
    except Exception:
        return 0.0


def _get_throttle_state() -> str:
    """Get RPi throttle state using vcgencmd."""
    try:
        result = subprocess.check_output(
            ["vcgencmd", "get_throttled"],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=1
        ).strip()
        # Extract hex value after '='
        if "=" in result:
            return result.split("=", 1)[1]
        return "0x0"
    except Exception:
        return "N/A"

class BenchmarkCSV:
    def start(self, folder_path, csv_name, duration, interval=0.5):
        os.makedirs(folder_path, exist_ok=True)
        
        # Generar timestamp humano y concatenarlo al nombre
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        final_csv_name = f"{ts}_{csv_name}"
        
        self.file = open(os.path.join(folder_path, final_csv_name), 'w', newline='')
        self.writer = csv.writer(self.file)

        num_cores = os.cpu_count()
        headers = ["Time_Human", "Time_Unix_ms", "CPU_Load_1m", "CPU_Freq_MHz"] + [
            f"Core_{i}_%" for i in range(num_cores)
        ] + ["RAM_Used_%", "Swap_Used_%", "Disk_Write_MBps", "Temp_C", "Throttled"]
        self.writer.writerow(headers)

        self.duration = duration
        self.interval = interval
        self.running = True
        self.last_disk_write = self._get_disk_bytes()
        self.last_time = time.time()

        self.thread = threading.Thread(target=self._run_loop, daemon=True)
        self.thread.start()

    def _get_disk_bytes(self):
        try:
            with open('/proc/diskstats', 'r') as f:
                # Índice 9 = sectores escritos. * 512 = bytes
                return sum(int(l.split()[9]) for l in f.readlines() if 'loop' not in l and 'ram' not in l) * 512
        except:
            return 0

    def _run_loop(self):
        end_t = time.time() + self.duration
        while self.running and time.time() < end_t:
            start_loop = time.time()
            self.save_data()
            
            elapsed = time.time() - start_loop
            wait = self.interval - elapsed
            if wait > 0:
                time.sleep(wait)
        self.stop()

    def save_data(self):
        now = time.time()
        load = os.getloadavg()[0]
        
        try: freq = int(psutil.cpu_freq().current)
        except: freq = 0
            
        cores = psutil.cpu_percent(percpu=True)
        ram = psutil.virtual_memory().percent
        swap = psutil.swap_memory().percent

        d_write = self._get_disk_bytes()
        t_diff = now - self.last_time
        mbps = ((d_write - self.last_disk_write) / t_diff) / (1024 * 1024) if t_diff > 0 else 0
        self.last_disk_write, self.last_time = d_write, now

        try:
            with open("/sys/class/thermal/thermal_zone0/temp", "r") as f:
                temp = round(int(f.read()) / 1000, 1)
        except: temp = "N/A"
            
        try:
            throttled = subprocess.check_output(
                ['vcgencmd', 'get_throttled'], text=True, stderr=subprocess.DEVNULL
            ).strip().split('=')[1]
        except: throttled = "N/A"

        self.writer.writerow([
            datetime.fromtimestamp(now).strftime("%H:%M:%S.%f")[:-3],
            int(now * 1000), round(load, 2), freq
        ] + cores + [ram, swap, round(mbps, 3), temp, throttled])
        
        self.file.flush()
        os.fsync(self.file.fileno()) # <-- Esto asegura la escritura física en disco

    def stop(self):
        self.running = False
        if not self.file.closed:
            self.file.close()

class BPFBenchmark:
    def __init__(
        self,
        binary_route: str,
        function_name: str,
        log: logging.Logger = None,
        target_pid: int = -1,
        output_csv: Optional[str] = None,
    ):
        self._log = log or logging.getLogger(__name__)
        self.binary_route = binary_route
        self.function_name = function_name
        self.target_pid = int(target_pid) if target_pid is not None else -1
        self.output_csv = output_csv
        self._csv_lock = threading.Lock()
        self.online_cpus = _count_online_cpus_linux()
        self._perf_hw_enabled = True

        if self.output_csv:
            self._init_csv()
        self.BPF_TEXT = """
                        #include <uapi/linux/ptrace.h>
                        #include <linux/sched.h>

                        struct data_t {
                            u32 pid;
                            u32 tid;
                            u64 duration_ns;
                            u64 instructions;
                            u64 cache_misses;
                        };

                        BPF_PERF_OUTPUT(events);

                        BPF_HASH(start_times, u32, u64);
                        BPF_HASH(start_instr, u32, u64);
                        BPF_HASH(start_cache, u32, u64);

                        BPF_PERF_ARRAY(hw_instr, __ONLINE_CPUS__);
                        BPF_PERF_ARRAY(hw_cache, __ONLINE_CPUS__);

                        int enter_func(struct pt_regs *ctx) {
                            u64 id = bpf_get_current_pid_tgid();
                            u32 tid = (u32)id;
                            u64 ts = bpf_ktime_get_ns();
                            u32 key = bpf_get_smp_processor_id();
                            
                            u64 inst = hw_instr.perf_read(key);
                            u64 cache = hw_cache.perf_read(key);
                            
                            start_times.update(&tid, &ts);
                            start_instr.update(&tid, &inst);
                            start_cache.update(&tid, &cache);
                            return 0;
                        }

                        int exit_func(struct pt_regs *ctx) {
                            u64 id = bpf_get_current_pid_tgid();
                            u32 tid = (u32)id;
                            u32 pid = id >> 32;
                            u64 *tsp = start_times.lookup(&tid);
                            u64 *instp = start_instr.lookup(&tid);
                            u64 *cachep = start_cache.lookup(&tid);
                            
                            if (tsp == 0 || instp == 0 || cachep == 0) return 0;

                            u32 key = bpf_get_smp_processor_id();
                            u64 duration = bpf_ktime_get_ns() - *tsp;
                            u64 instr_count = hw_instr.perf_read(key) - *instp;
                            u64 cache_count = hw_cache.perf_read(key) - *cachep;

                            start_times.delete(&tid);
                            start_instr.delete(&tid);
                            start_cache.delete(&tid);

                            struct data_t data = {pid, tid, duration, instr_count, cache_count};
                            events.perf_submit(ctx, &data, sizeof(data));

                            return 0;
                        }
                        """
        self.BPF_TEXT = self.BPF_TEXT.replace("__ONLINE_CPUS__", str(self.online_cpus))
        self.BPF_TEXT_FALLBACK = """
                        #include <uapi/linux/ptrace.h>
                        #include <linux/sched.h>

                        struct data_t {
                            u32 pid;
                            u32 tid;
                            u64 duration_ns;
                            u64 instructions;
                            u64 cache_misses;
                        };

                        BPF_PERF_OUTPUT(events);
                        BPF_HASH(start_times, u32, u64);

                        int enter_func(struct pt_regs *ctx) {
                            u64 id = bpf_get_current_pid_tgid();
                            u32 tid = (u32)id;
                            u64 ts = bpf_ktime_get_ns();
                            start_times.update(&tid, &ts);
                            return 0;
                        }

                        int exit_func(struct pt_regs *ctx) {
                            u64 id = bpf_get_current_pid_tgid();
                            u32 tid = (u32)id;
                            u32 pid = id >> 32;
                            u64 *tsp = start_times.lookup(&tid);
                            if (tsp == 0) return 0;

                            u64 duration = bpf_ktime_get_ns() - *tsp;
                            start_times.delete(&tid);

                            struct data_t data = {pid, tid, duration, 0, 0};
                            events.perf_submit(ctx, &data, sizeof(data));
                            return 0;
                        }
                        """
        BPF = _load_bpf_class()
        try:
            self.b = BPF(text=self.BPF_TEXT)
        except Exception as exc:
            self._perf_hw_enabled = False
            self._log.warning(
                "Falling back to timing-only BPF mode (no hw perf counters): %s",
                exc,
            )
            self.b = BPF(text=self.BPF_TEXT_FALLBACK)

    def _attach_probes(self):
        attach_pid = self.target_pid if self.target_pid > 0 else -1
        self.b.attach_uprobe(name=self.binary_route, sym=self.function_name, fn_name="enter_func", pid=attach_pid)
        self.b.attach_uretprobe(name=self.binary_route, sym=self.function_name, fn_name="exit_func", pid=attach_pid)

    def _bind_perf_events(self):
        if not self._perf_hw_enabled:
            self._log.warning("Hardware perf counters disabled. Reporting time-only events.")
            return
        PerfType, PerfHWConfig = _load_bpf_perf_enums()
        self.b["hw_instr"].open_perf_event(int(PerfType.HARDWARE), int(PerfHWConfig.INSTRUCTIONS), pid=-1)
        self.b["hw_cache"].open_perf_event(int(PerfType.HARDWARE), int(PerfHWConfig.CACHE_MISSES), pid=-1)

    def _init_csv(self):
        """Initialize CSV file with headers."""
        path = Path(self.output_csv)
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            header = "timestamp_ms,pid,tid,function,duration_ms,instructions,cache_misses,cpu_mhz,temp_celsius,throttle_state,ips\n"
            atomic_write_bytes(str(path), header.encode("utf-8"))
            self._log.info("CSV output initialized at %s", path)

    def _append_csv_row(self, row: str):
        """Append a CSV row using atomic writes to prevent corruption."""
        if not self.output_csv:
            return
        path = Path(self.output_csv)
        with self._csv_lock:
            prev = b""
            if path.exists():
                prev = path.read_bytes()
            payload = prev + row.encode("utf-8")
            atomic_write_bytes(str(path), payload)

    def _BPF_callback(self, cpu, data, size):
        event = self.b["events"].event(data)
        ms = event.duration_ns / 1e6
        ts_ms = cfg.get_time_ms()
        
        # Calculate IPS (Instructions Per Second)
        ips = (event.instructions / (ms / 1000.0)) if ms > 0 else 0.0
        
        # Get system metrics
        cpu_mhz = _get_cpu_freq_mhz()
        temp_celsius = _get_temperature_celsius()
        throttle_state = _get_throttle_state()
        
        self._log.info(
            f"PID {event.pid} TID {event.tid} | Time: {ms:.3f} ms | Instr: {event.instructions} | Misses: {event.cache_misses} | "
            f"IPS: {ips:.0f} | CPU: {cpu_mhz:.0f} MHz | Temp: {temp_celsius:.1f}°C | Throttle: {throttle_state}"
        )

        if self.output_csv:
            row = (
                f"{ts_ms},{event.pid},{event.tid},"
                f"{self.function_name},{ms:.6f},{event.instructions},{event.cache_misses},"
                f"{cpu_mhz:.1f},{temp_celsius:.1f},{throttle_state},{ips:.0f}\n"
            )
            self._append_csv_row(row)

    def init(self):
        self._attach_probes()
        self._bind_perf_events()
        self.b["events"].open_perf_buffer(self._BPF_callback)
        pid_info = f" pid={self.target_pid}" if self.target_pid > 0 else " pid=ALL"
        self._log.info(
            f"Monitoring '{self.function_name}' in '{self.binary_route}' ({pid_info})... Press Ctrl+C to exit."
        )
