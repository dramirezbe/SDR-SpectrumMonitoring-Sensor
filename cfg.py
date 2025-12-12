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
from typing import Optional, Callable
from enum import Enum, auto
from contextlib import redirect_stdout, redirect_stderr
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()

# =============================
# 2. CONFIGURATION (Safe Defaults)
# =============================
# Defaults matching your provided .env for robustness
API_URL = os.getenv("API_URL", "http://127.0.0.1:8000/api/sensor/")
VERBOSE = os.getenv("VERBOSE", "false").lower() == "true"
DEVELOPMENT = os.getenv("DEVELOPMENT", "false").lower() == "true"
LOG_FILES_NUM = int(os.getenv("LOG_FILES_NUM", "10"))

DATA_URL = os.getenv("DATA_URL", "/data")
STATUS_URL = os.getenv("STATUS_URL", "/status")
JOBS_URL = os.getenv("JOBS_URL", "/jobs")
REALTIME_URL = os.getenv("REALTIME_URL", "/realtime")
NTP_SERVER = os.getenv("NTP_SERVER", "pool.ntp.org")

IPC_CMD_ADDR = os.getenv("IPC_CMD_ADDR", "ipc:///tmp/zmq_feed")
IPC_DATA_ADDR = os.getenv("IPC_DATA_ADDR", "ipc:///tmp/zmq_data")

INTERVAL_REQUEST_CAMPAIGNS_S = int(os.getenv("INTERVAL_REQUEST_CAMPAIGNS_S", "60"))
INTERVAL_REQUEST_REALTIME_S = int(os.getenv("INTERVAL_REQUEST_REALTIME_S", "5"))

# Paths
_THIS_FILE = pathlib.Path(__file__).resolve()
PROJECT_ROOT = _THIS_FILE.parent
QUEUE_DIR = PROJECT_ROOT / "Queue"
LOGS_DIR = PROJECT_ROOT / "Logs"
HISTORIC_DIR = PROJECT_ROOT / "Historic"

# Ensure critical directories exist
for p in [QUEUE_DIR, LOGS_DIR, HISTORIC_DIR]:
    p.mkdir(parents=True, exist_ok=True)

# =============================
# 3. HELPERS
# =============================
def get_time_ms() -> int:
    """Returns current time in milliseconds since epoch."""
    return int(time.time() * 1000)

def get_mac() -> str:
    # 1. Try Environment Variable
    if mac := os.getenv("MAC_ADDRESS"):
        if mac != "00:00:00:00:00:00": return mac

    # 2. Fallback to System Files
    try:
        # Sort to prioritize eth0 over eth1, etc.
        for iface in sorted(os.listdir("/sys/class/net")):
            if iface.startswith(("lo", "sit", "docker", "veth", "vir", "br", "tun")):
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
    """Delegates to the *current* sys.stdout/stderr to handle redirection gracefully."""
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
    """Writes to file and stdout simultaneously, supporting fileno/isatty."""
    def __init__(self, primary, secondary):
        self.primary = primary
        self.secondary = secondary
        self.encoding = getattr(primary, "encoding", "utf-8")

    def write(self, data):
        if data is None: return 0
        s = str(data)
        try: self.primary.write(s)
        except: pass
        try: self.secondary.write(s)
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
    try:
        name = pathlib.Path(sys.argv[0]).stem.upper()
    except:
        name = "SENSOR"

    logger = logging.getLogger(name)
    if logger.hasHandlers(): return logger

    logger.setLevel(logging.DEBUG)
    
    formatter = SimpleFormatter(
        "%(asctime)s[%(name)s]%(levelname)s %(message)s", 
        datefmt="%d-%b-%y(%H:%M:%S)"
    )

    # Use Proxy to ensure it keeps working even if we redirect stdout later
    stdout_proxy = _CurrentStreamProxy('stdout')
    handler = logging.StreamHandler(stdout_proxy)
    handler.setLevel(logging.INFO if VERBOSE else logging.WARNING)
    handler.setFormatter(formatter)
    
    logger.addHandler(handler)
    return logger

# =============================
# 5. EXECUTION CAPTURE (Robust V2)
# =============================
def run_and_capture(func: Callable[[], Optional[int]], num_files=LOG_FILES_NUM) -> int:
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

    # Execution
    rc = 1
    orig_out, orig_err = sys.stdout, sys.stderr
    
    try:
        # buffering=1 ensures lines are written immediately
        with log_file.open("w", encoding="utf-8", buffering=1) as f:
            tee_out = Tee(orig_out, f)
            tee_err = Tee(orig_err, f)
            
            # Context manager redirection (Safer)
            with redirect_stdout(tee_out), redirect_stderr(tee_err):
                logging.getLogger().info(f"Log started: {log_file.name}")
                try:
                    rc = func()
                except KeyboardInterrupt:
                    rc = 0
                except SystemExit as e:
                    rc = e.code if isinstance(e.code, int) else 1
                except Exception:
                    traceback.print_exc()
                    rc = 1
            
            if f.tell() == 0: f.write("[[OK]]\n")
    finally:
        sys.stdout, sys.stderr = orig_out, orig_err

    # Normalize RC
    if rc is None: return 0
    if isinstance(rc, bool): return int(rc)
    return int(rc)

# =============================
# 6. ENUMS
# =============================
class SysState(Enum):
    IDLE = auto()
    CAMPAIGN = auto()
    REALTIME = auto()
    CALIBRATING = auto()

def debug()->int:
    log = set_logger()
    log.info("--- cfg.py debug ---")
    log.info(f"PROJECT_ROOT: {PROJECT_ROOT}")
    log.info(f"LOGS_DIR: {LOGS_DIR}")
    log.info(f"VERBOSE: {VERBOSE}")
    log.info(f"API_URL: {API_URL}")
    log.info(f"MAC_ADDRESS: {get_mac()}")
    log.info("--- cfg.py debug end ---")

    return 0

if __name__ == "__main__":
    rc = run_and_capture(debug)
    sys.exit(rc)
    