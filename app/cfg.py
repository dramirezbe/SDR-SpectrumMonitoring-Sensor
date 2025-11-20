#!/usr/bin/env python3
"""@file cfg.py
@brief Global configuration file
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
from typing import Optional, Callable
from enum import Enum, auto
from contextlib import redirect_stdout, redirect_stderr
import traceback # Import traceback for printing exceptions

from utils import get_persist_var

# =============================
# 2. PUBLIC EXPORTS (__all__)
# =============================
__all__ = [
    "LOG_LEVEL", "VERBOSE", "LOG_FILES_NUM",
    "API_IP", "API_PORT", "API_URL", "RETRY_DELAY_SECONDS", "API_KEY", 
    "DATA_URL", "STATUS_URL", "JOBS_URL",
    "APP_DIR", "PROJECT_ROOT", "LIB_LTE", "SAMPLES_DIR", "QUEUE_DIR", "NTP_SERVER",
    "LOGS_DIR", "HISTORIC_DIR", "PERSIST_FILE", "COMPILED_PATH",
    "get_time_ms", 
    "KalState", "OrchestratorState",
    "FROZEN", "EXECUTABLE_PATH",
    "set_logger", # Added set_logger to __all__ as it's the main logger function
]

# =============================
# 3. GLOBAL CONFIGURATION
# =============================
LOG_LEVEL = logging.INFO
VERBOSE = True
LOG_FILES_NUM = 10
API_IP = "localhost"
API_PORT = 9000
DATA_URL = "/data"
STATUS_URL = "/status"
JOBS_URL = "/jobs"
NTP_SERVER = "pool.ntp.org"

RETRY_DELAY_SECONDS = 5

# Logging formatting defaults (used by SimpleFormatter)
_DEFAULT_DATEFMT = "%Y-%m-%d-%H:%M:%S" # Not directly used in SimpleFormatter, but kept for context
_DEFAULT_LOG_FORMAT = "%(asctime)s[%(name)s]%(levelname)s: %(message)s" # Not directly used, but kept for context

# =============================
# 4. TIME HELPERS
# =============================
def get_time_ms() -> int:
    """Returns current time in milliseconds since epoch."""
    return int(time.time() * 1000)

# =============================
# 5. RUNTIME PATH/FLAG HELPERS
# =============================
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

# -----------------------------
# 5.1. RUNTIME FLAGS (FROZEN, EXECUTABLE_PATH)
# -----------------------------
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

# -----------------------------
# 5.2. BASE APP PATHS (Calculated)
# -----------------------------
_THIS_FILE = pathlib.Path(__file__).resolve()
APP_DIR: pathlib.Path = _THIS_FILE.parent
PROJECT_ROOT: pathlib.Path = APP_DIR.parent

# If we have an on-disk executable (frozen), detect PROJECT_ROOT
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

# Final path definitions
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

PERSIST_FILE = (PROJECT_ROOT / "persistent.json").resolve()

# COMPILED_PATH points to PROJECT_ROOT/build (root build)
COMPILED_PATH = (PROJECT_ROOT / "build").resolve()

# API URL depends on path definition
API_URL = f"http://{API_IP}:{API_PORT}/api/v1/devices/{str(get_persist_var('device_id', PERSIST_FILE))}"

API_KEY = get_persist_var("api_key", PERSIST_FILE)
API_KEY = API_KEY if API_KEY else ""


# =============================
# 6. LOGGING IMPLEMENTATION
# =============================

class _CurrentStreamProxy:
    """
    A file-like proxy that always delegates to the *current*
    sys.stdout or sys.stderr. This solves the problem of
    log handlers holding a stale reference to the original stream after
    redirect_stdout/stderr is used.
    """
    def __init__(self, stream_name: str):
        # stream_name is 'stdout' or 'stderr'
        if stream_name not in ('stdout', 'stderr'):
            raise ValueError("Stream name must be 'stdout' or 'stderr'")
        self._stream_name = stream_name

    def _get_current_stream(self):
        """Fetches the stream from sys module by name."""
        return getattr(sys, self._stream_name)

    # --- Delegate core file-like methods ---
    def write(self, data):
        """Write to the *current* stream."""
        return self._get_current_stream().write(data)

    def flush(self):
        """Flush the *current* stream."""
        return self._get_current_stream().flush()

    # --- Delegate other common attributes/methods ---
    def __getattr__(self, name):
        """
        Delegates other attributes (like .encoding, .isatty())
        to the *current* stream object.
        """
        try:
            return getattr(self._get_current_stream(), name)
        except AttributeError:
            # Provide a fallback for 'encoding' if the stream doesn't have it
            if name == 'encoding':
                return 'utf-8' # A reasonable default
            raise


class Tee:
    """
    File-like wrapper that writes to two destinations: primary (usually the real terminal stream)
    and secondary (usually a StringIO buffer).
    Delegates fileno(), isatty(), encoding where available to the primary stream.
    Used by run_and_capture.
    """
    def __init__(self, primary, secondary):
        self.primary = primary
        self.secondary = secondary
        # many logging handlers expect an 'encoding' attribute
        self.encoding = getattr(primary, "encoding", None)

    def write(self, data):
        # ensure we always convert to str
        if data is None:
            return 0
        s = str(data)
        # write to both, swallow errors independently so one failing doesn't prevent the other
        try:
            self.primary.write(s)
        except Exception:
            # best effort: ignore
            pass
        try:
            self.secondary.write(s)
        except Exception:
            pass
        # return number of characters written (approx)
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
        # delegate if available, otherwise raise (some libraries expect fileno)
        if hasattr(self.primary, "fileno"):
            return self.primary.fileno()
        raise OSError("fileno not available")

    def isatty(self):
        return getattr(self.primary, "isatty", lambda: False)()

    # small niceties used by some libraries
    def readable(self): return False
    def writable(self): return True
    def __getattr__(self, name):
        # fallback to primary for other attributes
        return getattr(self.primary, name)


class SimpleFormatter(logging.Formatter):
    """
    Custom formatter (no color):
    1. Changes ERROR to EXCEPTION if exc_info is present.
    2. Pads levelname for alignment.
    """
    def __init__(self, fmt, datefmt):
        super().__init__(fmt, datefmt=datefmt)
        
    def format(self, record):
        # 1. Handle EXCEPTION
        if record.exc_info:
            record.levelname = "EXCEPTION"
            
        # 2. Pad levelname (9 chars for "EXCEPTION")
        record.levelname = f"{record.levelname:<9}"
        
        # 3. Let parent class format
        return super().format(record)


def set_logger() -> logging.Logger:
    """
    Configures and returns a root logger for the application, using the name
    of the calling script as the logger name and tag.
    """
    
    # 1. Determinar el nombre del script que llama (e.g., 'dummy')
    # Usamos sys._getframe(1) para obtener el marco de la pila del llamador.
    try:
        # stack frame del llamador (ej: el módulo que importó cfg y llamó a set_logger)
        caller_frame = sys._getframe(1) 
        # path completo del archivo que llamó a la función
        caller_file = pathlib.Path(caller_frame.f_code.co_filename) 
        # nombre del script sin extensión (ej: 'dummy')
        log_name = caller_file.stem.upper() 
    except Exception:
        log_name = "SENSOR_UNKNOWN" # Fallback si falla la detección

    # 2. Usar un logger con nombre único basado en el script
    logger = logging.getLogger(log_name)

    # 3. Evitar duplicación de handlers
    if logger.hasHandlers():
        return logger

    # 4. Configurar el formato
    logger.setLevel(logging.DEBUG)
    
    # El formato ahora utiliza el nombre del logger: %(name)s
    log_format = f"%(asctime)s[{log_name}]%(levelname)s %(message)s"
    date_format = "%d-%b-%y(%H:%M:%S)"
    
    formatter = SimpleFormatter(log_format, datefmt=date_format)

    # 5. Configurar Handler de Consola
    stdout_proxy = _CurrentStreamProxy('stdout')
    console_handler = logging.StreamHandler(stdout_proxy)
    
    # Implements VERBOSE flag
    console_level = logging.INFO if VERBOSE else logging.WARNING
    
    console_handler.setLevel(console_level)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    return logger

# =============================
# 7. EXECUTION CAPTURE
# =============================
def run_and_capture(func: Callable[[], Optional[int]],
                    num_files: int) -> int:
    """
    Run `func()`, capture stdout/stderr output into files under log_dir/<timestamp>_<module_name>.log,
    while still letting all output appear on the original terminal.

    Returns rc as int (0 for success).
    """
    log_dir = LOGS_DIR
    timestamp = get_time_ms()
    
    # Automatically determine the module name
    try:
        # sys.argv[0] is the path to the executed script
        module_name = pathlib.Path(sys.argv[0]).stem
    except Exception:
        module_name = "unknown_module"
        
    log_file = log_dir / f"{timestamp}_{module_name}.log"
    
    log_dir.mkdir(parents=True, exist_ok=True)

    # Buffers to capture output
    buf_out = io.StringIO()
    buf_err = io.StringIO()

    # Save original sys streams
    orig_stdout = sys.stdout
    orig_stderr = sys.stderr

    rc = 1
    try:
        # Replace sys.stdout/sys.stderr with a Tee that writes to both the real terminal and the buffer
        tee_out = Tee(orig_stdout, buf_out)
        tee_err = Tee(orig_stderr, buf_err)

        with redirect_stdout(tee_out), redirect_stderr(tee_err):
            try:
                # Add basic logging of the log file name
                logging.getLogger("SENSOR").info(f"Log file: {log_file.name}")
                rc = func()
            except SystemExit as e:
                # preserve int exit codes if provided
                rc = e.code if isinstance(e.code, int) else 1
            except Exception:
                # print full traceback to stderr (will be captured and shown)
                traceback.print_exc(file=sys.stderr)
                rc = 1

        # Extract captured text
        out_text = buf_out.getvalue().strip()
        err_text = buf_err.getvalue().strip()
        total_words = len(out_text.split()) + len(err_text.split())

        # coerce rc to int (None -> 0)
        if rc is None:
            rc = 0
        elif isinstance(rc, bool):
            rc = int(rc)
        elif not isinstance(rc, int):
            try:
                rc = int(rc)
            except Exception:
                rc = 1

        # Write log file and manage history
        # Use LOG_FILES_NUM from the global scope (since num_files is LOG_FILES_NUM)
        files = [p for p in log_dir.iterdir() if p.is_file() and p.suffix == ".log"]
        files.sort(key=lambda p: p.stat().st_mtime)  # oldest modified first

        while len(files) >= num_files:  # ensure we keep only num_files logs
            files.pop(0).unlink()

        
        with log_file.open("w", encoding="utf-8") as fh:
            if total_words == 0:
                fh.write("[[OK]]\n")
            else:
                # All logger output (INFO, ERROR, etc.) will be in out_text
                # because the logger's proxy writes to the redirected stdout.
                # err_text will only contain direct stderr writes (like tracebacks).
                if out_text:
                    fh.write(out_text + "\n")
                if err_text:
                    fh.write(err_text + "\n")

        return rc

    finally:
        # restore sys streams (redirect context manager already does on normal exit,
        # but ensure restoration in case of unexpected errors)
        try:
            sys.stdout = orig_stdout
            sys.stderr = orig_stderr
        except Exception:
            pass

# =============================
# 8. ENUMS
# =============================
class KalState(Enum):
    """States for the Kalibration process."""
    KAL_SCANNING = auto()
    KAL_CALIBRATING = auto()

class OrchestratorState(Enum):
    """States for the main application orchestrator."""
    ORCH_IDLE = auto()
    ORCH_REALTIME = auto()
    ORCH_CAMPAIGN_SYNC = auto()


# =============================
# 9. MODULE EXECUTION
# =============================
# quick debug print when executed directly
if VERBOSE and __name__ == "__main__":
    # Initialize the logger immediately so it can be used in this file
    log = set_logger()
    log.info("--- cfg.py debug ---")
    log.info(f"PROJECT_ROOT: {PROJECT_ROOT}")
    
    # Finished Debug Prints:
    log.info(f"APP_DIR: {APP_DIR}")
    log.info(f"FROZEN: {FROZEN}")
    log.info(f"EXECUTABLE_PATH: {EXECUTABLE_PATH}")
    log.info(f"LIB_LTE: {LIB_LTE}")
    log.info(f"LOGS_DIR: {LOGS_DIR}")
    log.info(f"API_URL: {API_URL}")
    log.info(f"API KEY: {API_KEY}")
    log.info(f"LOG_LEVEL: {LOG_LEVEL}")
    log.info(f"NTP_SERVER: {NTP_SERVER}")
    log.info("--- cfg.py debug end ---")