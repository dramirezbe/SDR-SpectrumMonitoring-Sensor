"""@file utils/io_util.py
@brief Utility functions for file I/O.
"""
from __future__ import annotations
from pathlib import Path
import tempfile
import os
import logging
import json
from typing import Any, Callable, Optional
from dataclasses import dataclass, field
from crontab import CronTab

# A default logger for this module.
log = logging.getLogger(__name__)

def atomic_write_bytes(target_path: Path, data: bytes) -> None:
    """
    Write `data` to `target_path` atomically by writing to a temp file
    in the same directory and then replacing the target file.
    """
    target_dir = target_path.parent
    target_dir.mkdir(parents=True, exist_ok=True)

    # Create a NamedTemporaryFile in the target directory so replace() is atomic on same filesystem.
    # We use a path object outside the 'with' to ensure its visibility for cleanup.
    tmp_name: Optional[Path] = None
    try:
        with tempfile.NamedTemporaryFile(dir=str(target_dir), delete=False) as tmpf:
            tmp_name = Path(tmpf.name)
            tmpf.write(data)
            tmpf.flush()
            # Ensure all data is written to disk before closing/renaming
            os.fsync(tmpf.fileno())

        # Atomic replace
        if tmp_name:
            tmp_name.replace(target_path)

    except Exception as e:
        # Ensure temp file is removed on failure (write/fsync/replace)
        if tmp_name and tmp_name.exists():
            try:
                tmp_name.unlink(missing_ok=True)
            except Exception:
                log.warning("Failed to clean up temporary file %s after error: %s", tmp_name, e)
        raise

def get_tmp_var(key: str, path: Path) -> Optional[Any]:
    """
    Read a variable from the JSON variables file at `path`.
    Returns the stored value or None if file/key not present or on error.
    Safe: will create parent directories if missing but will NOT create the file.
    """
    try:
        # Ensure parent dir exists
        path.parent.mkdir(parents=True, exist_ok=True)

        if not path.exists():
            return None

        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)

        if not isinstance(data, dict):
            # Log non-dict content and treat as "no value found"
            log.debug("get_tmp_var: file %s exists but is not a JSON object (got %s)", path, type(data).__name__)
            return None

        return data.get(key)

    except Exception:
        # Be conservative: don't raise; return None to signal "no value"
        log.debug("get_tmp_var: exception reading %s", path, exc_info=True)
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
                    log.warning("modify_tmp: existing file %s is not a JSON object, starting fresh.", path)
                    data = {}
            except Exception:
                # If reading/parsing fails, log and start fresh (we will overwrite file)
                log.warning("modify_tmp: failed to read/parse existing JSON at %s, overwriting.", path, exc_info=True)
                data = {}

        # Set/replace key
        data[key] = value

        # Serialize and write atomically
        payload = json.dumps(data, indent=2, sort_keys=True).encode("utf-8")
        atomic_write_bytes(path, payload)

        # Always return 0 per policy
        return 0

    except Exception:
        # Log the exception but do not propagate error to caller
        log.exception("modify_tmp: unexpected error writing %s", path)
        return 0


@dataclass
class CronHandler:
    """A class to handle creating, erasing, and saving user-level cron jobs."""

    # IMPROVEMENT: Removed default=None for get_time_ms to make it a strictly
    # required parameter, matching the __post_init__ check.
    get_time_ms : Callable[[], int] 
    logger: Any = log
    verbose: bool = False

    # Internal state
    crontab_changed: bool = field(default=False, init=False)
    cron: Optional[CronTab] = field(default=None, init=False)

    def __post_init__(self) -> None:
        """Initialize the CronTab object for the current user."""
        # The check for 'get_time_ms is None' is now redundant but kept as a strong guardrail,
        # although its type (Callable) is now enforced by dataclass instantiation unless
        # explicitly passed None.
        if not callable(self.get_time_ms):
             raise TypeError("'get_time_ms' must be a callable function.")

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
        # Use a constant for the guard window for clarity
        GUARD_WINDOW_MS = 10_000

        start_with_guard = start - GUARD_WINDOW_MS
        end_with_guard = end + GUARD_WINDOW_MS

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

        # NOTE: find_comment returns an iterator, converting to a list is good practice
        # before passing to remove(), as done in the original code.
        jobs_found = self.cron.find_comment(comment)
        job_list = list(jobs_found)

        if not job_list:
            return 0

        self.cron.remove(*job_list)
        self.crontab_changed = True

        if self.verbose:
            self.logger.info(f"[CRON]|INFO| Erased {len(job_list)} job(s) with comment: '{comment}'")

        return 0

    def add(self, command: str, comment: str, minutes: int) -> int:
        """Adds a new cron job."""
        if self.cron is None:
            return 1

        # Validates minutes input
        if not 1 <= minutes <= 59:
            self.logger.error(f"[CRON]|ERROR| Invalid cron minutes value: {minutes} (must be 1..59)")
            return 1

        job = self.cron.new(command=command, comment=comment)
        # Sets the schedule: "Every X minutes"
        job.setall(f"*/{minutes} * * * *")
        self.crontab_changed = True

        if self.verbose:
            self.logger.info(f"[CRON]|INFO| Added job with comment '{comment}' to run every {minutes} minutes.")

        return 0