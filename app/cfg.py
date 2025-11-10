#!/usr/bin/env python3
"""@file cfg.py
@brief Global configuration file
"""

from __future__ import annotations
import logging
import pathlib
import time
import sys
import os
from typing import Optional, Set
from enum import Enum, auto
import inspect

__all__ = [
    "LOG_LEVEL", "VERBOSE", "LOG_FILES_NUM",
    "API_IP", "API_PORT", "API_URL", "RETRY_DELAY_SECONDS", 
    "DATA_URL", "STATUS_URL", "JOBS_URL",
    "APP_DIR", "PROJECT_ROOT", "LIB_LTE", "SAMPLES_DIR", "QUEUE_DIR", "NTP_SERVER",
    "LOGS_DIR", "HISTORIC_DIR", "TMP_FILE", "COMPILED_PATH",
    "get_logger", "configure_logging", "get_time_ms", "resource_path",
    "KalState", "OrchestratorState",
    "FROZEN", "EXECUTABLE_PATH",
]

# -----------------------
# --- basic config ---
# -----------------------
LOG_LEVEL = logging.INFO
VERBOSE = True
LOG_FILES_NUM = 10
API_IP = "localhost"
API_PORT = 8000
API_URL = f"http://{API_IP}:{API_PORT}"
DATA_URL = "/data"
STATUS_URL = "/status"
JOBS_URL = "/jobs"
NTP_SERVER = "pool.ntp.org"

RETRY_DELAY_SECONDS = 5

# -----------------------
# --- logging helpers ---
# -----------------------
_DEFAULT_DATEFMT = "%Y-%m-%d-%H:%M:%S"
_DEFAULT_LOG_FORMAT = "%(asctime)s[%(name)s]%(levelname)s: %(message)s"

def _ensure_handler(logger: logging.Logger) -> None:
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(_DEFAULT_LOG_FORMAT, datefmt=_DEFAULT_DATEFMT))
        logger.addHandler(handler)

def _infer_caller_module_name(skip_modules: Optional[Set[str]] = None) -> str:
    if skip_modules is None:
        skip_modules = set()
    stack = inspect.stack()
    for frame_info in stack[1:]:
        module = inspect.getmodule(frame_info.frame)
        if not module:
            continue
        mod_name = module.__name__
        if mod_name in skip_modules:
            continue
        if mod_name.startswith("unittest") or mod_name.startswith("pytest"):
            continue
        if mod_name == "__main__":
            filename = frame_info.filename
            if filename:
                base = os.path.splitext(os.path.basename(filename))[0]
                if base:
                    return base.capitalize()
            continue
        return mod_name.split(".")[-1].capitalize()
    return "App"

def get_logger(name: Optional[str] = None, level: int = LOG_LEVEL) -> logging.Logger:
    if name is None:
        skip = {__name__}
        name = _infer_caller_module_name(skip_modules=skip)
    logger = logging.getLogger(name)
    logger.setLevel(level)
    _ensure_handler(logger)
    logger.propagate = False
    return logger

def configure_logging(level: int = LOG_LEVEL) -> None:
    root = logging.getLogger()
    root.setLevel(level)
    if not root.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(_DEFAULT_LOG_FORMAT, datefmt=_DEFAULT_DATEFMT))
        root.addHandler(handler)

def get_time_ms() -> int:
    return int(time.time() * 1000)

# -----------------------
# --- small helpers for runtime paths ---
# -----------------------
def _invoked_exe() -> Optional[pathlib.Path]:
    """Return resolved path invoked by user (sys.argv[0]) if available."""
    try:
        if len(sys.argv) > 0 and sys.argv[0]:
            return pathlib.Path(sys.argv[0]).resolve()
    except Exception:
        pass
    return None

def _is_path_in_meipass(p: pathlib.Path) -> bool:
    """Heuristic: return True if path looks like it is under a PyInstaller _MEI dir."""
    try:
        me = getattr(sys, "_MEIPASS", None)
        if me:
            mep = pathlib.Path(me).resolve()
            return mep == p or mep in p.parents
        # fallback heuristic: name contains _MEI
        return any("_MEI" in part for part in p.parts)
    except Exception:
        return False

# -----------------------
# --- runtime flags (defined before resource_path) ---
# -----------------------
FROZEN = bool(getattr(sys, "frozen", False))

EXECUTABLE_PATH: Optional[pathlib.Path] = None
if FROZEN:
    invoked = _invoked_exe()
    if invoked and invoked.exists() and not _is_path_in_meipass(invoked):
        EXECUTABLE_PATH = invoked
    else:
        try:
            execp = pathlib.Path(sys.executable).resolve()
            if execp.exists() and not _is_path_in_meipass(execp):
                EXECUTABLE_PATH = execp
        except Exception:
            EXECUTABLE_PATH = None

# -----------------------
# --- base app paths (dev defaults) ---
# -----------------------
_THIS_FILE = pathlib.Path(__file__).resolve()
APP_DIR: pathlib.Path = _THIS_FILE.parent
PROJECT_ROOT: pathlib.Path = APP_DIR.parent

# If we have an on-disk executable, detect PROJECT_ROOT when exe is in PROJECT_ROOT/build/<exe>
if EXECUTABLE_PATH is not None:
    try:
        exe_parent = EXECUTABLE_PATH.parent  # expected .../build
        # Accept only PROJECT_ROOT/build/<exe> layout
        if exe_parent.name == "build" and exe_parent.parent.exists():
            PROJECT_ROOT = exe_parent.parent.resolve()
            APP_DIR = (PROJECT_ROOT / "app").resolve()
    except Exception:
        # keep dev defaults if detection fails
        pass

# -----------------------
# --- resource helper (safe, self-contained) ---
# -----------------------
def resource_path(*parts: str) -> pathlib.Path:
    """
    Return a Path to a bundled resource that works in dev, onedir and onefile.

    Priority:
      1. If sys._MEIPASS exists (onefile extraction), use that.
      2. If running frozen, use the directory of the invoked executable.
      3. Otherwise (dev), use the app/ directory (this file's parent).
    """
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        try:
            base = pathlib.Path(meipass).resolve()
            return base.joinpath(*parts).resolve()
        except Exception:
            pass

    if getattr(sys, "frozen", False):
        invoked = _invoked_exe()
        if invoked:
            try:
                base = invoked.parent.resolve()
                return base.joinpath(*parts).resolve()
            except Exception:
                pass
        try:
            base = pathlib.Path(sys.executable).resolve().parent
            return base.joinpath(*parts).resolve()
        except Exception:
            pass

    try:
        base = pathlib.Path(__file__).resolve().parent
        return base.joinpath(*parts).resolve()
    except Exception:
        return pathlib.Path.cwd().joinpath(*parts).resolve()

# -----------------------
# --- computed paths that depend on resource_path / project layout ---
# -----------------------
LIB_LTE = (PROJECT_ROOT / "libs_C" / "lte_driver.so").resolve()

try:
    SAMPLES_DIR = (PROJECT_ROOT / "Samples").resolve()
except Exception:
    SAMPLES_DIR = pathlib.Path("./Samples").resolve()

try:
    QUEUE_DIR = (PROJECT_ROOT / "Queue").resolve()
except Exception:
    QUEUE_DIR = pathlib.Path("./Queue").resolve()

try:
    LOGS_DIR = (PROJECT_ROOT / "Logs").resolve()
except Exception:
    LOGS_DIR = pathlib.Path("./Logs").resolve()

try:
    HISTORIC_DIR = (PROJECT_ROOT / "Historic").resolve()
except Exception:
    HISTORIC_DIR = pathlib.Path("./Historic").resolve()

TMP_FILE = (PROJECT_ROOT / "tmp" / "vars.json").resolve()

# COMPILED_PATH points to PROJECT_ROOT/build (root build)
COMPILED_PATH = (PROJECT_ROOT / "build").resolve()

# -----------------------
# --- enums & exports ---
# -----------------------
class KalState(Enum):
    KAL_SCANNING = auto()
    KAL_CALIBRATING = auto()

class OrchestratorState(Enum):
    ORCH_IDLE = auto()
    ORCH_REALTIME = auto()
    ORCH_CAMPAIGN_SYNC = auto()


# quick debug print when executed directly
if VERBOSE and __name__ == "__main__":
    print("cfg debug:")
    print("  FROZEN:", FROZEN)
    print("  EXECUTABLE_PATH:", EXECUTABLE_PATH)
    print("  APP_DIR:", APP_DIR)
    print("  PROJECT_ROOT:", PROJECT_ROOT)
    print("  LIB_LTE:", LIB_LTE)
    print("  COMPILED_PATH:", COMPILED_PATH)
