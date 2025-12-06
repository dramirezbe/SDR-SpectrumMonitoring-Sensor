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
import os
import traceback
from typing import Optional, Callable
from enum import Enum, auto, IntEnum
from contextlib import redirect_stdout, redirect_stderr
from dataclasses import dataclass
from dotenv import load_dotenv
from utils import get_persist_var

load_dotenv()

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
    "set_logger",
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
    """Return the Ethernet MAC address of the Linux Wi-Fi device."""
    try:
        import os

        # Prioritize checking for common Wi-Fi interface prefixes
        wifi_prefixes = ("wlan", "wlp")

        for iface in os.listdir("/sys/class/net"):
            path = f"/sys/class/net/{iface}/address"

            # Check if the interface starts with a known Wi-Fi prefix
            if not iface.startswith(wifi_prefixes):
                continue
                
            try:
                with open(path) as f:
                    mac = f.read().strip()
                # Ensure the MAC address is not empty or the all-zeros broadcast address
                if mac and mac != "00:00:00:00:00:00":
                    return mac
            except Exception:
                # Silently catch file read/access errors
                pass

    except Exception:
        # Silently catch errors related to os.listdir or import
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
API_URL = os.getenv("API_URL")




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
# 10. MODULE DEBUG
# =============================
if VERBOSE and __name__ == "__main__":
    log = set_logger()
    log.info("--- cfg.py debug (Pure Python) ---")
    log.info(f"PROJECT_ROOT: {PROJECT_ROOT}")
    log.info(f"API_URL:      {API_URL}")
    log.info("--- cfg.py debug end ---")
