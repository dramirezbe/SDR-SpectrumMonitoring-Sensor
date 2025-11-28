#!/usr/bin/env python3
"""@file cfg.py
@brief Global configuration file
"""

from __future__ import annotations
import logging
import pathlib
import time
import sys
import io
import traceback
from typing import Optional, Callable
from contextlib import redirect_stdout, redirect_stderr

# Removed unused import of get_persist_var as it's not used in the provided code.
# from utils import get_persist_var 

__all__ = [
    "VERBOSE", "PYTHON_EXEC",
    "API_URL", "REALTIME_URL",
    "DATA_URL", 
    "PROJECT_ROOT",
    "get_time_ms",
    "set_logger",
    "run_and_capture"
]

LOG_LEVEL = logging.INFO
VERBOSE = True
LOG_FILES_NUM = 10
API_IP = "localhost"
API_PORT = 9000
DATA_URL = "/data"
REALTIME_URL = "/realtime"
DEVELOPMENT = True

# Logging formatting defaults
_DEFAULT_DATEFMT = "%Y-%m-%d-%H:%M:%S" # Not used, but kept for clarity
_DEFAULT_LOG_FORMAT = "%(asctime)s[%(name)s]%(levelname)s: %(message)s" # Not used, but kept for clarity


def get_time_ms() -> int:
    """Returns current time in milliseconds since epoch."""
    # Use time.monotonic() if measuring elapsed time, but time.time() is correct for epoch time.
    return int(time.time() * 1000)


_THIS_FILE = pathlib.Path(__file__).resolve()
# Use .parent to get the directory containing this file (the project root)
PROJECT_ROOT: pathlib.Path = _THIS_FILE.parent


LOGS_DIR = (PROJECT_ROOT / "Logs").resolve()

# Ensure critical directories exist
for _path in [LOGS_DIR]:
    _path.mkdir(parents=True, exist_ok=True)


API_URL = f"http://{API_IP}:{API_PORT}"


# Simplified executable path logic
if DEVELOPMENT:
    # Adjusted path for typical venv structure where 'dev-venv' is in PROJECT_ROOT
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

    # Added required methods for file-like objects for robustness
    def write(self, data):
        return self._get_current_stream().write(data)

    def flush(self):
        return self._get_current_stream().flush()

    def __getattr__(self, name):
        try:
            return getattr(self._get_current_stream(), name)
        except AttributeError:
            if name == 'encoding':
                # Default encoding for most terminal streams
                return 'utf-8' 
            raise


class Tee:
    """
    File-like wrapper that writes to two destinations.
    The original Tee implementation was sound, minimal changes made.
    """
    def __init__(self, primary, secondary):
        self.primary = primary
        self.secondary = secondary
        # Safely determine encoding
        self.encoding = getattr(primary, "encoding", getattr(secondary, "encoding", None))

    def write(self, data):
        if data is None:
            return 0
        
        # Ensure data is treated as a string before writing
        s = str(data)
        
        # Write to primary, ignoring errors
        written_len = 0
        try:
            written_len = self.primary.write(s)
        except Exception:
            pass
        
        # Write to secondary, ignoring errors
        try:
            self.secondary.write(s)
        except Exception:
            pass
        
        # Return length written to primary (mimics single stream behavior)
        return written_len if written_len > 0 else len(s)

    def flush(self):
        try:
            self.primary.flush()
        except Exception:
            pass
        try:
            self.secondary.flush()
        except Exception:
            pass

    # Simplified and corrected file-like methods
    def fileno(self):
        if hasattr(self.primary, "fileno"):
            return self.primary.fileno()
        raise io.UnsupportedOperation("fileno not available")

    def isatty(self):
        return getattr(self.primary, "isatty", lambda: False)()

    def readable(self): return False
    def writable(self): return True
    
    # Delegate other methods to primary stream
    def __getattr__(self, name):
        return getattr(self.primary, name)


class SimpleFormatter(logging.Formatter):
    """
    A simple formatter that customizes the log level field.
    """
    def __init__(self, fmt, datefmt):
        super().__init__(fmt, datefmt=datefmt)
        
    def format(self, record):
        # Override levelname for exceptions
        if record.exc_info:
            # NOTE: The original code used "EXCEPTION", this is kept.
            record.levelname = "EXCEPTION" 
        
        # Left align levelname and pad it
        original_levelname = record.levelname
        record.levelname = f"{original_levelname:<9}"
        
        # Format the message
        formatted_message = super().format(record)
        
        # Restore original levelname for other handlers (though none exist here)
        record.levelname = original_levelname
        
        return formatted_message


def set_logger() -> logging.Logger:
    """
    Sets up a logger with a console handler and a custom formatter.
    The log name is derived from the calling module's filename.
    """
    try:
        # Get the frame of the function that called set_logger (sys._getframe(1))
        caller_frame = sys._getframe(1) 
        caller_file = pathlib.Path(caller_frame.f_code.co_filename) 
        # Use filename stem as log name
        log_name = caller_file.stem.upper() 
    except Exception:
        # Fallback name
        log_name = "SENSOR_UNKNOWN"

    logger = logging.getLogger(log_name)

    # Check if logger is already configured to prevent duplicate handlers
    if logger.hasHandlers():
        return logger

    # Set logger to DEBUG to allow all messages to pass to handlers
    logger.setLevel(logging.DEBUG)
    
    # Use f-string for log format to embed the log_name statically
    log_format = f"%(asctime)s[{log_name}]%(levelname)s %(message)s"
    date_format = "%d-%b-%y(%H:%M:%S)" # Simplified date format
    
    formatter = SimpleFormatter(log_format, datefmt=date_format)

    # Use the proxy to respect redirects
    stdout_proxy = _CurrentStreamProxy('stdout')
    console_handler = logging.StreamHandler(stdout_proxy)
    
    # Set console logging level based on VERBOSE
    console_level = logging.DEBUG if VERBOSE else logging.INFO # Changed to DEBUG if VERBOSE is True for maximum output
    
    console_handler.setLevel(console_level)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    return logger

# =============================
# 8. EXECUTION CAPTURE
# =============================
def run_and_capture(func: Callable[[], Optional[int]], 
                    num_files: int = LOG_FILES_NUM) -> int:
    """
    Runs a function, captures its stdout/stderr, logs output to a file,
    performs log rotation, and returns the exit code.
    """
    log_dir = LOGS_DIR
    timestamp = get_time_ms()
    
    try:
        module_name = pathlib.Path(sys.argv[0]).stem
    except Exception:
        module_name = "unknown_module"
        
    log_file = log_dir / f"{timestamp}_{module_name}.log"
    log_dir.mkdir(parents=True, exist_ok=True)

    buf_out = io.StringIO()
    buf_err = io.StringIO()

    orig_stdout = sys.stdout
    orig_stderr = sys.stderr

    rc = 1
    
    try:
        tee_out = Tee(orig_stdout, buf_out)
        tee_err = Tee(orig_stderr, buf_err)
    except Exception:
        return 1

    try:
        with redirect_stdout(tee_out), redirect_stderr(tee_err):
            try:
                logging.getLogger(module_name.upper()).info(f"Log file: {log_file.name}")
                result = func()
                
                if result is None:
                    rc = 0
                elif isinstance(result, int):
                    rc = result
                elif isinstance(result, bool):
                    rc = int(result)
                else:
                    try:
                        rc = int(result)
                    except Exception:
                        rc = 1 

            except SystemExit as e:
                rc = e.code if isinstance(e.code, int) else 1
            except Exception:
                traceback.print_exc(file=sys.stderr)
                rc = 1

        out_text = buf_out.getvalue().strip()
        err_text = buf_err.getvalue().strip()
        total_words = len(out_text.split()) + len(err_text.split())

        files = [p for p in log_dir.iterdir() if p.is_file() and p.name.endswith(".log") and "_" in p.name]
        files.sort(key=lambda p: p.stat().st_mtime)

        # Uses the default num_files (LOG_FILES_NUM) unless overridden
        while len(files) >= num_files:
            try:
                files.pop(0).unlink()
            except Exception:
                pass
        
        with log_file.open("w", encoding="utf-8") as fh:
            if total_words == 0:
                fh.write("[[OK]]\n")
            else:
                if out_text:
                    fh.write(out_text + "\n")
                if err_text:
                    fh.write(err_text + "\n")
        
        return rc

    finally:
        pass


# =============================
# 10. MODULE DEBUG
# =============================
if VERBOSE and __name__ == "__main__":
    # Example usage for debugging the file itself
    log = set_logger()
    log.info("--- cfg.py debug (Pure Python) ---")
    log.info(f"PROJECT_ROOT: {PROJECT_ROOT}")
    log.info(f"LOGS_DIR:     {LOGS_DIR}")
    log.info(f"API_URL:      {API_URL}")
    log.info("--- cfg.py debug end ---")