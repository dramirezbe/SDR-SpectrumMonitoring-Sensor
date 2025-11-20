#!/usr/bin/env python3
"""
ntp_sync.py

Force a system clock sync with an NTP server (default: cfg.NTP_SERVER).
Uses a small class-based structure, clearer flow, and updates a persistent
variable on success.

Behavior:
 - Query local system time (UTC) and NTP time (UTC).
 - Convert NTP time to Colombia local time (UTC-5).
 - Temporarily disable automatic sync (timedatectl), set the system clock,
   then re-enable automatic sync.
 - If setting the clock succeeds, call modify_persist("last_npt_ms", cfg.get_time_ms())
   and validate its return code == 0 before logging success.
 - On NTP fetch failure, only log an error and exit non-zero (no modify_persist call).
"""

from __future__ import annotations
import subprocess
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

import ntplib

import cfg
from utils import modify_persist

log = cfg.set_logger()


@dataclass
class NTPSyncResult:
    success: bool
    sys_time_utc: Optional[datetime] = None
    ntp_time_utc: Optional[datetime] = None
    local_time: Optional[datetime] = None
    error: Optional[str] = None


class NTPResync:
    NTP_TIMEOUT = 5  # seconds

    def __init__(self, server: str = cfg.NTP_SERVER):
        self.server = server

    @staticmethod
    def _now_utc() -> datetime:
        return datetime.now(timezone.utc)

    def _fetch_ntp(self) -> datetime:
        client = ntplib.NTPClient()
        resp = client.request(self.server, version=4, timeout=self.NTP_TIMEOUT)
        return datetime.fromtimestamp(resp.tx_time, tz=timezone.utc)

    @staticmethod
    def _to_colombia(utc_dt: datetime) -> datetime:
        # Colombia is UTC-5 without DST
        return (utc_dt + timedelta(hours=-5)).replace(tzinfo=None)

    @staticmethod
    def _run_cmd(cmd: list[str]) -> subprocess.CompletedProcess:
        return subprocess.run(cmd, capture_output=True, text=True)

    @staticmethod
    def _set_system_time(local_dt: datetime) -> subprocess.CompletedProcess:
        formatted = local_dt.strftime("%Y-%m-%d %H:%M:%S")
        if shutil.which("timedatectl"):
            cmd = ["timedatectl", "set-time", formatted]
        else:
            # Use date -s for systems without timedatectl (expects local time)
            cmd = ["date", "-s", formatted]
        return NTPResync._run_cmd(cmd)

    @staticmethod
    def _disable_auto_sync() -> None:
        if shutil.which("timedatectl"):
            subprocess.run(["timedatectl", "set-ntp", "false"], capture_output=True)

    @staticmethod
    def _enable_auto_sync() -> None:
        if shutil.which("timedatectl"):
            subprocess.run(["timedatectl", "set-ntp", "true"], capture_output=True)

    def run(self) -> NTPSyncResult:
        result = NTPSyncResult(success=False)
        try:
            result.sys_time_utc = self._now_utc()
            log.info(f"System time (UTC): {result.sys_time_utc.isoformat()}")
        except Exception as exc:
            result.error = f"Failed to read system time: {exc}"
            log.error(result.error)
            return result

        try:
            result.ntp_time_utc = self._fetch_ntp()
            log.info(f"NTP time (UTC): {result.ntp_time_utc.isoformat()}")
        except Exception as exc:
            result.error = f"NTP fetch error: {exc}"
            log.error(result.error)
            return result

        try:
            # Convert to Colombia local time (naive local time: no tzinfo)
            result.local_time = self._to_colombia(result.ntp_time_utc)
            log.info(f"Colombia time (UTC-5): {result.local_time.isoformat()}")
        except Exception as exc:
            result.error = f"Local time conversion error: {exc}"
            log.error(result.error)
            # continue: we may still attempt to set if local_time exists
            return result

        # Try set system time
        try:
            self._disable_auto_sync()
            proc = self._set_system_time(result.local_time)
            if proc.returncode == 0:
                log.info("System time updated successfully.")
                # Persist the timestamp only on success
                try:
                    rc = modify_persist("last_npt_ms", cfg.get_time_ms(), cfg.PERSIST_FILE)
                    if rc == 0:
                        log.info("Persisted last_npt_ms successfully.")
                    else:
                        log.error(f"modify_persist returned non-zero rc={rc}")
                except Exception as exc:
                    log.error(f"modify_persist call failed: {exc}")
                result.success = True
            else:
                stderr = proc.stderr.strip() if proc.stderr else ""
                result.error = (
                    f"Failed to set system time rc={proc.returncode} stderr={stderr}"
                )
                log.error(result.error)
        except Exception as exc:
            result.error = f"Setting system time error: {exc}"
            log.error(result.error)
        finally:
            try:
                self._enable_auto_sync()
            except Exception:
                # best-effort re-enable; don't override earlier errors
                log.warning("Failed to re-enable automatic time sync (best-effort).")

        return result


def main() -> int:
    res = NTPResync().run()
    if res.success:
        return 0
    else:
        # already logged error details inside run(); return non-zero
        return 1


if __name__ == "__main__":
    rc = cfg.run_and_capture(main, cfg.LOG_FILES_NUM)
    sys.exit(rc)
