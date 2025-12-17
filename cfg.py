#!/usr/bin/env python3
#cfg.py


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
import asyncio 
import inspect 
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Callable, Union, Coroutine, Any
from contextlib import redirect_stdout, redirect_stderr
from dotenv import load_dotenv

load_dotenv()

# =============================
# 2. CONFIGURATION (Safe Defaults)
# =============================
API_URL = os.getenv("API_URL", "http://127.0.0.1:8000/api/sensor/")
VERBOSE = os.getenv("VERBOSE", "false").lower() == "true"
DEVELOPMENT = os.getenv("DEVELOPMENT", "false").lower() == "true"
LOG_FILES_NUM = int(os.getenv("LOG_FILES_NUM", "10"))
LOG_ROTATION_LINES = int(os.getenv("LOG_ROTATION_LINES", "50"))

DATA_URL = os.getenv("DATA_URL", "/data")
STATUS_URL = os.getenv("STATUS_URL", "/status")
CAMPAIGN_URL = os.getenv("CAMPAIGN_URL", "/campaigns")
REALTIME_URL = os.getenv("REALTIME_URL", "/realtime")
GPS_URL = os.getenv("GPS_URL", "/gps")

IPC_ADDR = os.getenv("IPC_ADDR", "ipc:///tmp/rf_engine")

INTERVAL_REQUEST_CAMPAIGNS_S = int(os.getenv("INTERVAL_REQUEST_CAMPAIGNS_S", "60"))
INTERVAL_REQUEST_REALTIME_S = int(os.getenv("INTERVAL_REQUEST_REALTIME_S", "5"))
INTERVAL_STATUS_S = int(os.getenv("INTERVAL_STATUS_S", "30"))
INTERVAL_RETRY_QUEUE_S = int(os.getenv("INTERVAL_RETRY_QUEUE_S", "300"))

# Paths
_THIS_FILE = pathlib.Path(__file__).resolve()
PROJECT_ROOT = _THIS_FILE.parent
QUEUE_DIR = PROJECT_ROOT / "Queue"
LOGS_DIR = PROJECT_ROOT / "Logs"
HISTORIC_DIR = PROJECT_ROOT / "Historic"

PYTHON_ENV = (PROJECT_ROOT / "venv"/ "bin"/ "python3").absolute()
PYTHON_ENV_STR = str(PYTHON_ENV)

# =============================
# 3. HELPERS
# =============================
def get_time_ms() -> int:
    """Returns pure UTC timestamp in milliseconds UTC-5 (Colombia time)."""
    return int(time.time() * 1000) - (5 * 60 * 60 * 1000)

def human_readable(ts_ms, target_tz="UTC"):
    """
    Converts raw timestamp to a readable string in the specific timezone.
    Does NOT rely on manual integer shifting.
    """
    # 1. Convert ms to seconds
    # 2. Convert to datetime aware object in UTC
    dt_utc = datetime.fromtimestamp(ts_ms / 1000, tz=ZoneInfo("UTC"))
    
    # 3. Convert that exact moment to the target timezone
    dt_local = dt_utc.astimezone(ZoneInfo(target_tz))
    
    return dt_local.strftime('%Y-%m-%d %H:%M:%S')

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
# 4. LOGGING IMPLEMENTATION
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
    """Writes to file and stdout simultaneously."""
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

class SmartRotatingFile:
    """
    A file-like object that automatically rotates to a new file 
    after a certain number of newlines are written.
    Enforces LOG_FILES_NUM limits on every rotation.
    """
    def __init__(self, module_name: str, max_lines: int, max_files: int):
        self.module_name = module_name
        self.max_lines = max_lines
        self.max_files = max_files
        self.current_lines = 0
        self.file_handle = None
        self._open_new_file()

    def _cleanup_old_logs(self):
        """Maintains the total number of log files under the limit."""
        try:
            logs = sorted([p for p in LOGS_DIR.glob("*.log")], key=lambda p: p.stat().st_mtime)
            # We remove enough files so that adding one more won't exceed the limit
            while len(logs) >= self.max_files:
                oldest = logs.pop(0)
                try: oldest.unlink(missing_ok=True)
                except Exception: pass
        except Exception: 
            pass

    def _open_new_file(self):
        """Closes current and opens next."""
        if self.file_handle:
            try:
                self.file_handle.write("\n[[LOG ROTATED]]\n")
                self.file_handle.close()
            except: pass

        self._cleanup_old_logs()

        timestamp = get_time_ms()
        filename = LOGS_DIR / f"{timestamp}_{self.module_name}.log"
        
        self.file_handle = open(filename, "w", encoding="utf-8", buffering=1)
        self.current_lines = 0

    def write(self, data):
        if not self.file_handle: return
        
        s = str(data)
        self.file_handle.write(s)
        self.file_handle.flush()
        
        # Count lines to trigger rotation
        if self.max_lines > 0:
            self.current_lines += s.count('\n')
            if self.current_lines >= self.max_lines:
                self._open_new_file()

    def flush(self):
        if self.file_handle:
            self.file_handle.flush()

    def close(self):
        """
        Closes the file.
        If no logs were written (current_lines == 0), marks it as [[OK]].
        Does NOT write [[FINISHED]].
        """
        if self.file_handle:
            if self.current_lines == 0:
                self.file_handle.write("\n[[OK]]\n")
            
            self.file_handle.close()
            self.file_handle = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

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
# 5. EXECUTION CAPTURE
# =============================

TargetFunc = Union[Callable[[], int], Callable[[], Coroutine[Any, Any, int]]]

def run_and_capture(func: TargetFunc, num_files=LOG_FILES_NUM) -> int:
    """
    Executes a function (sync or async) and captures stdout/stderr.
    Uses SmartRotatingFile to rotate logs after 50 lines (default).
    """
    try: module = pathlib.Path(sys.argv[0]).stem
    except: module = "app"
    
    rc = 1
    orig_out, orig_err = sys.stdout, sys.stderr
    
    # Initialize the rotating file handler
    # This replaces the standard 'open()' context manager
    rotating_log = SmartRotatingFile(
        module_name=module, 
        max_lines=LOG_ROTATION_LINES, 
        max_files=num_files
    )

    try:
        with rotating_log as f:
            # Tee now writes to stdout AND our SmartRotatingFile object
            tee_out = Tee(orig_out, f)
            tee_err = Tee(orig_err, f)
            
            with redirect_stdout(tee_out), redirect_stderr(tee_err):
                logging.getLogger().info(f"Log system started. Rotation limit: {LOG_ROTATION_LINES} lines.")
                
                try:
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
    log.info(f"LOG_ROTATION_LINES: {LOG_ROTATION_LINES}")
    log.info("Simulating loop for rotation test...")
    
    # Test rotation logic
    for i in range(1, 150):
        log.info(f"Log Line {i} - testing rotation")
        time.sleep(0.01)

    log.info("--- cfg.py debug end ---")
    return 0

if __name__ == "__main__":
    rc = run_and_capture(debug)
    sys.exit(rc)