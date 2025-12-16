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
import traceback
import os
import asyncio # <--- ADDED
import inspect # <--- ADDED
from typing import Optional, Callable, Union, Coroutine, Any
from enum import Enum, auto
from contextlib import redirect_stdout, redirect_stderr
from dotenv import load_dotenv

load_dotenv()

# =============================
# 2. CONFIGURATION (Safe Defaults)
# =============================
API_URL = os.getenv("API_URL", "http://127.0.0.1:8000/api/sensor/")
VERBOSE = os.getenv("VERBOSE", "false").lower() == "true"
DEVELOPMENT = os.getenv("DEVELOPMENT", "false").lower() == "true"
DEVELOPMENT = os.getenv("DEVELOPMENT", "false").lower() == "true"
LOG_FILES_NUM = int(os.getenv("LOG_FILES_NUM", "10"))

DATA_URL = os.getenv("DATA_URL", "/data")
STATUS_URL = os.getenv("STATUS_URL", "/status")
CAMPAIGN_URL = os.getenv("CAMPAIGN_URL", "/campaigns")
REALTIME_URL = os.getenv("REALTIME_URL", "/realtime")
GPS_URL = os.getenv("GPS_URL", "/gps")
NTP_SERVER = os.getenv("NTP_SERVER", "pool.ntp.org")

IPC_ADDR = os.getenv("IPC_ADDR", "ipc:///tmp/rf_engine")

INTERVAL_REQUEST_CAMPAIGNS_S = int(os.getenv("INTERVAL_REQUEST_CAMPAIGNS_S", "60"))
INTERVAL_REQUEST_REALTIME_S = int(os.getenv("INTERVAL_REQUEST_REALTIME_S", "5"))

# Paths
_THIS_FILE = pathlib.Path(__file__).resolve()
PROJECT_ROOT = _THIS_FILE.parent
QUEUE_DIR = PROJECT_ROOT / "Queue"
LOGS_DIR = PROJECT_ROOT / "Logs"
HISTORIC_DIR = PROJECT_ROOT / "Historic"

PYTHON_ENV = (PROJECT_ROOT / "venv"/ "bin"/ "python3").absolute()
PYTHON_ENV_STR = str(PYTHON_ENV)

FILE_CAMPAIGN_PARAMS = PROJECT_ROOT / "campaign_params.json"

for p in [QUEUE_DIR, LOGS_DIR, HISTORIC_DIR]:
    p.mkdir(parents=True, exist_ok=True)

# =============================
# 3. HELPERS
# =============================
def get_time_ms() -> int:
    return int(time.time() * 1000)

def get_mac() -> str:
    if mac := os.getenv("MAC_ADDRESS"):
        if mac != "00:00:00:00:00:00": return mac
    try:
        interfaces = os.listdir("/sys/class/net")
        interfaces.sort(key=lambda x: (not x.startswith("wlan"), x))
        for iface in interfaces:
            if iface.startswith(("lo", "sit", "docker", "veth", "vir", "br", "tun", "wg")):
                continue
            try:
                with open(f"/sys/class/net/{iface}/address") as f:
                    mac = f.read().strip()
                if mac and mac != "00:00:00:00:00:00": return mac
            except OSError: continue
    except Exception: pass
    return "00:00:00:00:00:00"

# =============================
# 4. LOGGING IMPLEMENTATION (Robust V2)
# =============================

class _CurrentStreamProxy:
    def __init__(self, stream_name: str):
        self._stream_name = stream_name
    def _get_current_stream(self):
        return getattr(sys, self._stream_name)
    def write(self, data):
        return self._get_current_stream().write(data)
    def flush(self):
        return self._get_current_stream().flush()
    def __getattr__(self, name):
        return getattr(self._get_current_stream(), name)

class Tee:
    """Writes to file and stdout simultaneously.
    Updated to support aggressive flushing for real-time logging."""
    def __init__(self, primary, secondary):
        self.primary = primary
        self.secondary = secondary
        self.encoding = getattr(primary, "encoding", "utf-8")

    def write(self, data):
        if data is None: return 0
        s = str(data)
        try: self.primary.write(s)
        except: pass
        try: 
            self.secondary.write(s)
            # FORCE FLUSH: Critical for "Real Time" in infinite loops
            # This ensures logs appear in the file immediately, not just when buffer fills.
            self.secondary.flush() 
        except: pass
        return len(s)

    def flush(self):
        try: self.primary.flush()
        except: pass
        try: self.secondary.flush()
        except: pass

    def fileno(self):
        return self.primary.fileno()

    def isatty(self):
        return getattr(self.primary, "isatty", lambda: False)()

class SimpleFormatter(logging.Formatter):
    def format(self, record):
        if record.exc_info: record.levelname = "EXCEPTION"
        record.levelname = f"{record.levelname:<9}"
        return super().format(record)

def set_logger() -> logging.Logger:
    try: name = pathlib.Path(sys.argv[0]).stem.upper()
    except: name = "SENSOR"

    logger = logging.getLogger(name)
    if logger.hasHandlers(): return logger

    logger.setLevel(logging.DEBUG)
    formatter = SimpleFormatter(
        "%(asctime)s[%(name)s]%(levelname)s %(message)s", 
        datefmt="%d-%b-%y(%H:%M:%S)"
    )

    stdout_proxy = _CurrentStreamProxy('stdout')
    handler = logging.StreamHandler(stdout_proxy)
    handler.setLevel(logging.INFO if VERBOSE else logging.WARNING)
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    return logger

# =============================
# 5. EXECUTION CAPTURE (Async + Sync Support)
# =============================

# Type definition for generic Sync or Async functions
TargetFunc = Union[Callable[[], int], Callable[[], Coroutine[Any, Any, int]]]

def run_and_capture(func: TargetFunc, num_files=LOG_FILES_NUM) -> int:
    """
    Executes a function (sync or async) and captures stdout/stderr to a log file.
    Handles infinite loops and KeyboardInterrupts gracefully.
    """
    timestamp = get_time_ms()
    try: module = pathlib.Path(sys.argv[0]).stem
    except: module = "app"
    
    log_file = LOGS_DIR / f"{timestamp}_{module}.log"

    # Rotation
    try:
        logs = sorted([p for p in LOGS_DIR.glob("*.log")], key=lambda p: p.stat().st_mtime)
        while len(logs) >= num_files:
            logs.pop(0).unlink(missing_ok=True)
    except Exception: pass

    rc = 1
    orig_out, orig_err = sys.stdout, sys.stderr
    
    try:
        # buffering=1 ensures line buffering.
        # Combined with Tee.flush(), this guarantees real-time updates.
        with log_file.open("w", encoding="utf-8", buffering=1) as f:
            tee_out = Tee(orig_out, f)
            tee_err = Tee(orig_err, f)
            
            with redirect_stdout(tee_out), redirect_stderr(tee_err):
                logging.getLogger().info(f"Log started: {log_file.name}")
                
                try:
                    # SMART EXECUTION: Detect if func is Async or Sync
                    if inspect.iscoroutinefunction(func):
                        rc = asyncio.run(func())
                    else:
                        rc = func()
                        
                except KeyboardInterrupt:
                    logging.getLogger().warning("Received KeyboardInterrupt. Exiting...")
                    rc = 0
                except SystemExit as e:
                    rc = e.code if isinstance(e.code, int) else 1
                except asyncio.CancelledError:
                    logging.getLogger().warning("Async task cancelled.")
                    rc = 1
                except Exception:
                    traceback.print_exc()
                    rc = 1
            
            # Final status write
            if f.tell() > 0: f.write("\n[[FINISHED]]\n")
            
    finally:
        sys.stdout, sys.stderr = orig_out, orig_err

    if rc is None: return 0
    if isinstance(rc, bool): return int(rc)
    return int(rc)

def debug()->int:
    log = set_logger()
    log.info("--- cfg.py debug ---")
    log.info(f"PROJECT_ROOT: {PROJECT_ROOT}")
    log.info(f"LOGS_DIR: {LOGS_DIR}")
    log.info(f"QUEUE_DIR: {QUEUE_DIR}")
    log.info(f"HISTORIC_DIR: {HISTORIC_DIR}")
    log.info(f"PYTHON_ENV: {PYTHON_ENV}")
    log.info(f"VERBOSE: {VERBOSE}")
    log.info(f"API_URL: {API_URL}")
    log.info(f"MAC_ADDRESS: {get_mac()}")
    log.info("--- cfg.py debug end ---")

    return 0

if __name__ == "__main__":
    rc = run_and_capture(debug)
    sys.exit(rc)
    