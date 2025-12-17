#!/usr/bin/env python3
#utils/io_util.py
from __future__ import annotations
from pathlib import Path
import tempfile
import os
import logging
import json
import time
import fcntl
from typing import Optional
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


class ShmStore:
    def __init__(self, filename="persistent.json"):
        """
        Initialize the storage in /dev/shm (RAM).
        Creates the file with an empty JSON object {} if it doesn't exist.
        """
        self.filepath = os.path.join("/dev/shm", filename)
        
        # Initialize file if it's missing (e.g., first run after boot)
        if not os.path.exists(self.filepath):
            self._write_file({})

    def _read_file(self):
        """Internal: Safely reads the JSON with a shared lock."""
        if not os.path.exists(self.filepath):
            return {}
            
        try:
            with open(self.filepath, 'r') as f:
                # Wait for permission to read (prevents reading during a write)
                fcntl.flock(f, fcntl.LOCK_SH) 
                try:
                    return json.load(f)
                finally:
                    fcntl.flock(f, fcntl.LOCK_UN)
        except (json.JSONDecodeError, IOError):
            return {}

    def _write_file(self, data):
        """Internal: Safely writes the JSON with an exclusive lock."""
        # Open in write mode ('w') which truncates, but we lock immediately
        with open(self.filepath, 'w') as f:
            fcntl.flock(f, fcntl.LOCK_EX) # Block others from reading/writing
            try:
                json.dump(data, f)
                f.flush()
                os.fsync(f.fileno()) # Force write to RAM immediately
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)

    def add_to_persistent(self, key, value):
        """
        Updates a specific key while keeping the rest of the data intact.
        """
        # 1. Load current full state
        current_data = self._read_file()
        
        # 2. Update only the requested key
        current_data[key] = value
        
        # 3. Save full state back
        self._write_file(current_data)

    def consult_persistent(self, key):
        """
        Returns the value for the key, or None if key not found.
        """
        current_data = self._read_file()
        return current_data.get(key, None)
    
    def update_from_dict(self, data_dict):
        """
        Updates multiple values at once using a dictionary.
        This is more efficient/atomic than calling add_to_persistent in a loop.
        """
        # 1. Load current full state
        current_data = self._read_file()
        
        # 2. Update with the new dictionary (merges/overwrites keys)
        if isinstance(data_dict, dict):
            current_data.update(data_dict)
        
        # 3. Save full state back
        self._write_file(current_data)

    def clear_persistent(self):
        """
        Atomically clears the storage, rewriting the file to an empty JSON object {}.
        Blocks all readers/writers during the operation.
        """
        with open(self.filepath, 'w') as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                f.write("{}")
                f.flush()
                os.fsync(f.fileno())
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)


class ElapsedTimer:
    def __init__(self):
        self.end_time = 0

    def init_count(self, seconds):
        self.end_time = time.time() + seconds

    def time_elapsed(self):
        return time.time() >= self.end_time