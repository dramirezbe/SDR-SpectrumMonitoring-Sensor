"""@file utils/io_util.py
@brief Utility functions for file I/O.
"""
from __future__ import annotations
from pathlib import Path
import tempfile
import os
import logging
import json
import time
from typing import Any, Callable, Optional, TYPE_CHECKING
from dataclasses import dataclass, field
from crontab import CronTab

# Use TYPE_CHECKING to avoid circular imports at runtime if request_util imports this file
if TYPE_CHECKING:
    from .request_util import CampaignListResponse

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


@dataclass
class CronHandler:
    """A class to handle creating, erasing, and saving user-level cron jobs."""

    get_time_ms : Callable[[], int] 
    logger: Any = log
    verbose: bool = False

    # Internal state
    crontab_changed: bool = field(default=False, init=False)
    cron: Optional[CronTab] = field(default=None, init=False)

    def __post_init__(self) -> None:
        """Initialize the CronTab object for the current user."""
        if not callable(self.get_time_ms):
             raise TypeError("'get_time_ms' must be a callable function.")

        try:
            self.cron = CronTab(user=True)
            if self.verbose:
                self.logger.info("[CRON]|INFO| CronTab handler initialized successfully.")
        except Exception as e:
            self.logger.error(f"[CRON]|ERROR| Failed to create cron object: {e}")
            self.cron = None

    def is_in_activate_time(self, start: int, end: int) -> bool:
        """Checks if the current time is within a given unix ms timeframe with a 10s guard window."""
        current = self.get_time_ms()
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

        if not 1 <= minutes <= 59:
            self.logger.error(f"[CRON]|ERROR| Invalid cron minutes value: {minutes} (must be 1..59)")
            return 1

        job = self.cron.new(command=command, comment=comment)
        job.setall(f"*/{minutes} * * * *")
        self.crontab_changed = True

        if self.verbose:
            self.logger.info(f"[CRON]|INFO| Added job '{comment}' (every {minutes}m).")

        return 0

    def process_campaigns(self, response: CampaignListResponse, script_path: str) -> int:
        """
        Syncs the scheduler with the provided Campaign list.
        
        1. Iterates through campaigns.
        2. Checks if campaign is active (Timeframe + Status).
        3. Updates/Adds cron job if active.
        4. Saves changes.
        
        Args:
            response: CampaignListResponse object.
            script_path: Absolute path to the python script to run.
                         Job cmd: `/usr/bin/python3 <script_path> --campaign_id <ID>`
        """
        if self.cron is None:
            return 1
            
        processed_count = 0
        
        for camp in response.campaigns:
            # Unique identifier for this campaign's cron job
            comment_tag = f"CAMP_{camp.campaign_id}"
            
            # 1. Clean up existing job for this campaign ID to ensure freshness
            #    (We remove it first, then re-add if valid. This handles updates to freq/params)
            self.erase(comment_tag)

            # 2. Check Status (Only 'active' or 'scheduled' are candidates)
            if camp.status not in ("active", "scheduled"):
                if self.verbose:
                    self.logger.info(f"[CRON] Skip ID {camp.campaign_id}: Status '{camp.status}'")
                continue

            # 3. Check Timeframe (Must be currently valid/active)
            if not self.is_in_activate_time(camp.timeframe.start, camp.timeframe.end):
                if self.verbose:
                    self.logger.info(f"[CRON] Skip ID {camp.campaign_id}: Outside timeframe.")
                continue

            # 4. Calculate Frequency (Cron is minute-based)
            #    If period < 60s, default to 1 minute.
            minutes_interval = max(1, int(camp.acquisition_period_s / 60))

            # 5. Build Command
            #    Assumes python3 environment.
            cmd = f"/usr/bin/python3 {script_path} --campaign_id {camp.campaign_id}"
            
            # 6. Add Job
            if self.add(command=cmd, comment=comment_tag, minutes=minutes_interval) == 0:
                processed_count += 1
        
        # Write changes to disk
        return self.save()


class ElapsedTimer:
    def __init__(self):
        self.end_time = 0

    def init_count(self, seconds):
        self.end_time = time.time() + seconds

    def time_elapsed(self):
        return time.time() >= self.end_time