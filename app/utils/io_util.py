"""@file utils/io_util.py
@brief Utility functions for file I/O.
"""
from __future__ import annotations
from pathlib import Path
import tempfile
import os
import logging
from contextlib import redirect_stdout, redirect_stderr
import sys
import io
import traceback
import json
from typing import Any, Callable, Optional
from dataclasses import dataclass, field
from crontab import CronTab

def atomic_write_bytes(target_path: Path, data: bytes) -> None:
    """
    Write `data` to `target_path` atomically by writing to a temp file
    in the same directory and then replacing the target file.
    """
    target_dir = target_path.parent
    target_dir.mkdir(parents=True, exist_ok=True)

    # Create a NamedTemporaryFile in the target directory so replace() is atomic on same filesystem
    with tempfile.NamedTemporaryFile(dir=str(target_dir), delete=False) as tmpf:
        tmp_name = Path(tmpf.name)
        try:
            tmpf.write(data)
            tmpf.flush()
            os.fsync(tmpf.fileno())
        except Exception:
            # Ensure temp file removed on failure
            try:
                tmp_name.unlink(missing_ok=True)
            except Exception:
                pass
            raise

    # Atomic replace
    tmp_name.replace(target_path)

def get_tmp_var(key: str, path: Path) -> Optional[Any]:
    """
    Read a variable from the JSON variables file at `path`.
    Returns the stored value or None if file/key not present or on error.
    Safe: will create parent directories if missing but will NOT create the file.
    """
    try:
        # Ensure parent dir exists (so callers that expect file creation later don't fail)
        path.parent.mkdir(parents=True, exist_ok=True)

        if not path.exists():
            return None

        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)

        if not isinstance(data, dict):
            return None

        return data.get(key)

    except Exception:
        # Be conservative: don't raise; return None to signal "no value"
        try:
            logging.getLogger(__name__).debug("get_tmp_var: exception reading %s", path, exc_info=True)
        except Exception:
            pass
        return None


def modify_tmp(key: str, value: Any, path: Path) -> int:
    """
    Atomically set `key` to `value` inside JSON file at `path`.
    - Ensures parent directories exist.
    - If file doesn't exist, creates it and writes a JSON object with only the provided key.
    - Does not raise on failure; logs and returns 0 to match 'no error required' policy.
    """
    try:
        # Ensure parent directories exist
        path.parent.mkdir(parents=True, exist_ok=True)

        data = {}
        if path.exists():
            try:
                with path.open("r", encoding="utf-8") as f:
                    data = json.load(f) or {}
                if not isinstance(data, dict):
                    # If file exists but isn't a JSON object, start fresh
                    data = {}
            except Exception:
                # If reading/parsing fails, log and start fresh (we will overwrite file)
                try:
                    logging.getLogger(__name__).warning("modify_tmp: failed to read existing JSON, overwriting %s", path, exc_info=True)
                except Exception:
                    pass
                data = {}

        # Set/replace key
        data[key] = value

        # Serialize and write atomically
        payload = json.dumps(data, indent=2, sort_keys=True).encode("utf-8")
        atomic_write_bytes(path, payload)

        # Always return 0 per your instruction ("no need to return error")
        return 0

    except Exception:
        # Log the exception but do not propagate error to caller (user asked to avoid returning error)
        try:
            logging.getLogger(__name__).exception("modify_tmp: unexpected error writing %s", path)
        except Exception:
            pass
        return 0

class Tee:
    """
    File-like wrapper that writes to two destinations: primary (usually the real terminal stream)
    and secondary (usually a StringIO buffer).
    Delegates fileno(), isatty(), encoding where available to the primary stream.
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


def run_and_capture(func: Callable[[], Optional[int]],
                    log: logging.Logger,
                    log_dir: Path,
                    timestamp: int,
                    num_files: int) -> int:
    """
    Run `func()`, capture stdout/stderr/logging output into files under log_dir/<timestamp>.log,
    while still letting all output appear on the original terminal.

    Returns rc as int (0 for success).
    """
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"{timestamp}.log"

    # Buffers to capture output
    buf_out = io.StringIO()
    buf_err = io.StringIO()

    # Save original sys streams
    orig_stdout = sys.stdout
    orig_stderr = sys.stderr

    # Save original handler streams so we can restore them
    handler_streams = []  # list of (handler, original_stream)

    rc = 1
    try:
        # For each StreamHandler on the logger, wrap its stream with a Tee(primary=orig, secondary=buf_err)
        for handler in list(log.handlers):
            if isinstance(handler, logging.StreamHandler):
                orig_stream = handler.stream
                # Use the handler's existing stream as primary (commonly sys.stderr); secondary is our capture buffer
                handler_streams.append((handler, orig_stream))
                handler.stream = Tee(orig_stream, buf_err)

        # Replace sys.stdout/sys.stderr with a Tee that writes to both the real terminal and the buffer
        tee_out = Tee(orig_stdout, buf_out)
        tee_err = Tee(orig_stderr, buf_err)

        with redirect_stdout(tee_out), redirect_stderr(tee_err):
            try:
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

        # Write log file
        # Write log file
        files = [p for p in log_dir.iterdir() if p.is_file() and p.suffix == ".log"]
        files.sort(key=lambda p: p.stat().st_mtime)  # oldest modified first

        while len(files) >= num_files:  # ensure we keep only num_files logs
            files.pop(0).unlink()

        
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
        # restore original handler streams
        for handler, orig in handler_streams:
            try:
                handler.stream = orig
            except Exception:
                pass
        # restore sys streams (redirect context manager already does on normal exit,
        # but ensure restoration in case of unexpected errors)
        try:
            sys.stdout = orig_stdout
            sys.stderr = orig_stderr
        except Exception:
            pass



# A default logger for this module. The user can provide a more specific one.
log = logging.getLogger(__name__)

@dataclass
class CronHandler:
    """A class to handle creating, erasing, and saving user-level cron jobs."""

    # Dependencies are injected, making the class more reusable.
    logger: Any = log
    verbose: bool = False
    get_time_ms: Callable[[], int] = None

    # Internal state
    crontab_changed: bool = False
    cron: Optional[CronTab] = field(default=None, init=False)

    def __post_init__(self) -> None:
        """Initialize the CronTab object for the current user."""
        if self.get_time_ms is None:
            # This is a programming error, so we raise an exception.
            raise TypeError("A 'get_time_ms' function must be provided during instantiation.")

        try:
            # 'user=True' creates/loads the crontab for the user running the script.
            self.cron = CronTab(user=True)
            if self.verbose:
                self.logger.info("[CRON]|INFO| CronTab handler initialized successfully.")
        except Exception as e:
            self.logger.error(f"[CRON]|ERROR| Failed to create cron object: {e}")
            self.cron = None

    def is_in_activate_time(self, start: int, end: int) -> bool:
        """Checks if the current time is within a given unix ms timeframe with a 10s guard window."""
        current = self.get_time_ms()
        ten_secs = 10_000

        start_with_guard = start - ten_secs
        end_with_guard = end + ten_secs

        return start_with_guard <= current <= end_with_guard

    def save(self) -> int:
        """Writes any pending changes (add/erase) to the crontab file."""
        if self.cron is None:
            return 1

        if self.crontab_changed:
            try:
                self.cron.write()
                if self.verbose:
                    self.logger.info("[CRON]|INFO| Crontab successfully saved.")
            except Exception as e:
                self.logger.error(f"[CRON]|ERROR| Failed to save cron: {e}")
                return 1
            self.crontab_changed = False
        return 0

    def erase(self, comment: str) -> int:
        """Removes all cron jobs matching a specific comment."""
        if self.cron is None:
            return 1

        jobs_found = self.cron.find_comment(comment)
        job_list = list(jobs_found)

        if not job_list:
            return 0  # No jobs with that comment, so nothing to do.

        self.cron.remove(*job_list)
        self.crontab_changed = True

        if self.verbose:
            self.logger.info(f"[CRON]|INFO| Erased {len(job_list)} job(s) with comment: '{comment}'")

        return 0

    def add(self, command: str, comment: str, minutes: int) -> int:
        """Adds a new cron job."""
        if self.cron is None:
            return 1

        if not 1 <= minutes <= 59:
            self.logger.error(f"[CRON]|ERROR| Invalid cron minutes value: {minutes} (must be 1..59)")
            return 1

        job = self.cron.new(command=command, comment=comment)
        job.setall(f"*/{minutes} * * * *")
        self.crontab_changed = True

        if self.verbose:
            self.logger.info(f"[CRON]|INFO| Added job with comment '{comment}' to run every {minutes} minutes.")

        return 0