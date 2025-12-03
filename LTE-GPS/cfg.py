#!/usr/bin/env python3
"""@file cfg.py
@brief Global configuration file - Pure Python/Docker Environment
"""

# =============================
# 1. IMPORTS
# =============================
from __future__ import annotations
import logging
import pathlib
import time
import sys
import io
import traceback
from typing import Optional, Callable
from enum import Enum, auto, IntEnum
from contextlib import redirect_stdout, redirect_stderr
from dataclasses import dataclass

from utils import get_persist_var

# =============================
# 2. PUBLIC EXPORTS (__all__)
# =============================
__all__ = [
    "LOG_LEVEL", "VERBOSE", "LOG_FILES_NUM", "PYTHON_EXEC",
    "API_IP", "API_PORT", "API_URL", "RETRY_DELAY_SECONDS", "REALTIME_URL",
    "DATA_URL", "STATUS_URL", "JOBS_URL", "CAMPAIGNS_INTERVAL_S", "REALTIME_INTERVAL_S",
    "APP_DIR", "PROJECT_ROOT", "SAMPLES_DIR", "QUEUE_DIR", "NTP_SERVER", "IPC_CMD_ADDR", "IPC_DATA_ADDR",
    "LOGS_DIR", "HISTORIC_DIR", "PERSIST_FILE",
    "get_time_ms", 
    "KalState",
    "SysState",
    "ZmqClients",
    "set_logger",
    "run_and_capture"
]

# =============================
# 3. GLOBAL CONFIGURATION
# =============================
LOG_LEVEL = logging.INFO
VERBOSE = True
LOG_FILES_NUM = 10
API_IP = "localhost"
API_PORT = 3000
DATA_URL = "/data"
STATUS_URL = "/status"
JOBS_URL = "/jobs"
REALTIME_URL = "/realtime"
NTP_SERVER = "pool.ntp.org"
DEVELOPMENT = True

# --- CONFIGURATION ---
# Address where Python BINDS (Sends Commands)
IPC_CMD_ADDR = "ipc:///tmp/zmq_feed"
# Address where Python CONNECTS (Receives Data from C)
IPC_DATA_ADDR = "ipc:///tmp/zmq_data"


CAMPAIGNS_INTERVAL_S = 60
REALTIME_INTERVAL_S = 5

RETRY_DELAY_SECONDS = 5

# Logging formatting defaults
_DEFAULT_DATEFMT = "%Y-%m-%d-%H:%M:%S"
_DEFAULT_LOG_FORMAT = "%(asctime)s[%(name)s]%(levelname)s: %(message)s"

# =============================
# 4. TIME HELPERS
# =============================
def get_time_ms() -> int:
    """Returns current time in milliseconds since epoch."""
    return int(time.time() * 1000)

def get_mac() -> str:
    """Return the Ethernet MAC address of the Linux device."""
    try:
        import os

        for iface in os.listdir("/sys/class/net"):
            path = f"/sys/class/net/{iface}/address"
            # Skip non-Ethernet or virtual interfaces
            if iface.startswith(("lo", "sit", "docker", "veth", "vir", "br")):
                continue
            try:
                with open(path) as f:
                    mac = f.read().strip()
                if mac and mac != "00:00:00:00:00:00":
                    return mac
            except Exception:
                pass

    except Exception:
        pass

    return ""


# =============================
# 5. PROJECT PATHS
# =============================
# Since we are not using PyInstaller, we calculate paths relative to this file.
# Structure assumed:
# PROJECT_ROOT/
#   ├── app/
#   │   └── cfg.py
#   ├── libs_C/
#   ├── Logs/
#   └── ...

_THIS_FILE = pathlib.Path(__file__).resolve()
APP_DIR: pathlib.Path = _THIS_FILE.parent
PROJECT_ROOT: pathlib.Path = APP_DIR.parent

# Define standard directories based on Project Root
SAMPLES_DIR = (PROJECT_ROOT / "Samples").resolve()
QUEUE_DIR = (PROJECT_ROOT / "Queue").resolve()
LOGS_DIR = (PROJECT_ROOT / "Logs").resolve()
HISTORIC_DIR = (PROJECT_ROOT / "Historic").resolve()
PERSIST_FILE = (PROJECT_ROOT / "persistent.json").resolve()

# Ensure critical directories exist
for _path in [SAMPLES_DIR, QUEUE_DIR, LOGS_DIR, HISTORIC_DIR]:
    _path.mkdir(parents=True, exist_ok=True)

# =============================
# 6. DYNAMIC CONFIG LOADING
# =============================
# API URL depends on device_id stored in persistent file
_device_id = get_persist_var('device_id', PERSIST_FILE)
API_URL = f"http://{API_IP}:{API_PORT}/api/sensor/"




if DEVELOPMENT:
    PYTHON_EXEC = (PROJECT_ROOT / "dev-venv" / "bin" / "python").resolve()
else:
    PYTHON_EXEC = (PROJECT_ROOT / "venv" / "bin" / "python").resolve()

# =============================
# 7. LOGGING IMPLEMENTATION
# =============================

class _CurrentStreamProxy:
    """
    A file-like proxy that always delegates to the *current*
    sys.stdout or sys.stderr.
    """
    def __init__(self, stream_name: str):
        if stream_name not in ('stdout', 'stderr'):
            raise ValueError("Stream name must be 'stdout' or 'stderr'")
        self._stream_name = stream_name

    def _get_current_stream(self):
        return getattr(sys, self._stream_name)

    def write(self, data):
        return self._get_current_stream().write(data)

    def flush(self):
        return self._get_current_stream().flush()

    def __getattr__(self, name):
        try:
            return getattr(self._get_current_stream(), name)
        except AttributeError:
            if name == 'encoding':
                return 'utf-8'
            raise


class Tee:
    """
    File-like wrapper that writes to two destinations.
    """
    def __init__(self, primary, secondary):
        self.primary = primary
        self.secondary = secondary
        self.encoding = getattr(primary, "encoding", None)

    def write(self, data):
        if data is None:
            return 0
        s = str(data)
        try:
            self.primary.write(s)
        except Exception:
            pass
        try:
            self.secondary.write(s)
        except Exception:
            pass
        return len(s)

    def flush(self):
        try:
            self.primary.flush()
        except Exception:
            pass
        try:
            self.secondary.flush()
        except Exception:
            pass

    def fileno(self):
        if hasattr(self.primary, "fileno"):
            return self.primary.fileno()
        raise OSError("fileno not available")

    def isatty(self):
        return getattr(self.primary, "isatty", lambda: False)()

    def readable(self): return False
    def writable(self): return True
    def __getattr__(self, name):
        return getattr(self.primary, name)


class SimpleFormatter(logging.Formatter):
    def __init__(self, fmt, datefmt):
        super().__init__(fmt, datefmt=datefmt)
        
    def format(self, record):
        if record.exc_info:
            record.levelname = "EXCEPTION"
        record.levelname = f"{record.levelname:<9}"
        return super().format(record)


def set_logger() -> logging.Logger:
    try:
        caller_frame = sys._getframe(1) 
        caller_file = pathlib.Path(caller_frame.f_code.co_filename) 
        log_name = caller_file.stem.upper() 
    except Exception:
        log_name = "SENSOR_UNKNOWN"

    logger = logging.getLogger(log_name)

    if logger.hasHandlers():
        return logger

    logger.setLevel(logging.DEBUG)
    
    log_format = f"%(asctime)s[{log_name}]%(levelname)s %(message)s"
    date_format = "%d-%b-%y(%H:%M:%S)"
    
    formatter = SimpleFormatter(log_format, datefmt=date_format)

    stdout_proxy = _CurrentStreamProxy('stdout')
    console_handler = logging.StreamHandler(stdout_proxy)
    
    console_level = logging.INFO if VERBOSE else logging.WARNING
    
    console_handler.setLevel(console_level)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    return logger

# =============================
# 8. EXECUTION CAPTURE
# =============================
def run_and_capture(func: Callable[[], Optional[int]],
                    num_files: int) -> int:
    log_dir = LOGS_DIR
    timestamp = get_time_ms()
    
    try:
        module_name = pathlib.Path(sys.argv[0]).stem
    except Exception:
        module_name = "unknown_module"
        
    log_file = log_dir / f"{timestamp}_{module_name}.log"
    
    # Ensure directory exists
    log_dir.mkdir(parents=True, exist_ok=True)

    # --- 1. Log Rotation (Perform BEFORE execution) ---
    # We do this first to ensure space is available
    try:
        files = [p for p in log_dir.iterdir() if p.is_file() and p.suffix == ".log"]
        files.sort(key=lambda p: p.stat().st_mtime)

        while len(files) >= num_files:
            try:
                files.pop(0).unlink()
            except Exception:
                pass
    except Exception:
        pass

    orig_stdout = sys.stdout
    orig_stderr = sys.stderr
    rc = 1

    # --- 2. Stream directly to file ---
    # buffering=1 means line-buffered (writes to disk every newline)
    try:
        with log_file.open("w", encoding="utf-8", buffering=1) as f_log:
            
            # The Tee class now writes to Console (Primary) and File (Secondary) immediately
            tee_out = Tee(orig_stdout, f_log)
            tee_err = Tee(orig_stderr, f_log)

            with redirect_stdout(tee_out), redirect_stderr(tee_err):
                try:
                    logging.getLogger("SENSOR").info(f"Log file: {log_file.name}")
                    rc = func()
                except SystemExit as e:
                    rc = e.code if isinstance(e.code, int) else 1
                except KeyboardInterrupt:
                    # Handle Ctrl+C gracefully for infinite loops
                    rc = 0
                except Exception:
                    traceback.print_exc(file=sys.stderr)
                    rc = 1
            
            # --- 3. Empty Log Check ---
            # If nothing was written (file pointer is at 0), write [[OK]]
            if f_log.tell() == 0:
                 f_log.write("[[OK]]\n")

    finally:
        # Restore standard streams
        try:
            sys.stdout = orig_stdout
            sys.stderr = orig_stderr
        except Exception:
            pass
            
    # Normalize Return Code
    if rc is None: return 0
    if isinstance(rc, bool): return int(rc)
    if not isinstance(rc, int): return 1
    return rc

# =============================
# 9. ENUMS
# =============================
class KalState(Enum):
    KAL_SCANNING = auto()
    KAL_CALIBRATING = auto()

class SysState(Enum):
    IDLE = auto()
    CAMPAIGN = auto()
    REALTIME = auto()

@dataclass
class ZmqClients:
    antenna_mux: str = "antenna_mux"
    realtime: str = "realtime"       



# =============================
# 10. MODULE DEBUG
# =============================
if VERBOSE and __name__ == "__main__":
    log = set_logger()
    log.info("--- cfg.py debug (Pure Python) ---")
    log.info(f"PROJECT_ROOT: {PROJECT_ROOT}")
    log.info(f"APP_DIR:      {APP_DIR}")
    log.info(f"LOGS_DIR:     {LOGS_DIR}")
    log.info(f"API_URL:      {API_URL}")
    log.info("--- cfg.py debug end ---")