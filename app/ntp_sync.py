#!/usr/bin/env python3
"""@file ntp_sync.py
@brief Forces system clock sync with pool.ntp.org every run.
"""

import ntplib
import subprocess
import shutil
from datetime import datetime, timezone, timedelta
import sys
from typing import Optional

import cfg
log = cfg.set_logger()


def get_system_time_utc() -> datetime:
    return datetime.now(timezone.utc)


def get_ntp_time(server=cfg.NTP_SERVER) -> datetime:
    client = ntplib.NTPClient()
    response = client.request(server, version=4, timeout=5)
    return datetime.fromtimestamp(response.tx_time, tz=timezone.utc)


def to_colombia(dt_utc: datetime) -> datetime:
    return dt_utc - timedelta(hours=5)


def disable_auto_sync():
    if shutil.which("timedatectl"):
        subprocess.run(["timedatectl", "set-ntp", "false"], capture_output=True)
        log.info("Disabled automatic time sync.")


def enable_auto_sync():
    if shutil.which("timedatectl"):
        subprocess.run(["timedatectl", "set-ntp", "true"], capture_output=True)
        log.info("Re-enabled automatic time sync.")


def set_system_time(
    dt_utc: Optional[datetime],
    sys_time: Optional[datetime],
    ntp_time: Optional[datetime],
) -> None:
    if dt_utc is None:
        log.error("No datetime provided to set_system_time. " + error_log(sys_time, ntp_time, None))
        sys.exit(1)

    formatted_time = dt_utc.strftime("%Y-%m-%d %H:%M:%S")
    cmd = (
        ["timedatectl", "set-time", formatted_time]
        if shutil.which("timedatectl")
        else ["date", "-u", "-s", formatted_time]
    )
    log.info(f"Running: {' '.join(cmd)}")
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode == 0:
        log.info("System time updated successfully.")
    else:
        log.error(
            f"Failed to set system time rc={proc.returncode} "
            f"sys_time={sys_time.isoformat() if sys_time else 'None'} "
            f"ntp_time={(ntp_time.isoformat() if ntp_time else 'None')} "
            f"stderr={proc.stderr.strip()}"
        )


def error_log(sys_time, ntp_time, colombia_time):
    def none_log(param):
        return param.isoformat() if param else 'None'
    return f"sys_time={none_log(sys_time)} ntp_time={none_log(ntp_time)} colombia_time={none_log(colombia_time)}"


def main() -> int:
    sys_time = ntp_time = colombia_time = None

    try:
        sys_time = get_system_time_utc()
        log.info(f"System time (UTC): {sys_time.isoformat()}")
    except Exception as e:
        log.error(f"System time error: {e} " + error_log(sys_time, ntp_time, colombia_time))
        return 1

    try:
        ntp_time = get_ntp_time()
    except Exception as e:
        log.error(f"NTP fetch error: {e} " + error_log(sys_time, ntp_time, colombia_time))
        return 1

    try:
        colombia_time = to_colombia(ntp_time)
    except Exception as e:
        log.error(f"Colombia time conversion error: {e} " + error_log(sys_time, ntp_time, colombia_time))
        colombia_time = None

    log.info(f"NTP time (UTC): {ntp_time.isoformat()}")
    log.info(f"Colombia time (UTC-5): {(colombia_time.isoformat() if colombia_time else 'None')}")

    try:
        disable_auto_sync()
        set_system_time(colombia_time, sys_time, ntp_time)
        enable_auto_sync()
    except Exception as e:
        log.error(f"Set time error: {e} " + error_log(sys_time, ntp_time, colombia_time))
        return 1

    return 0


if __name__ == "__main__":
    rc = cfg.run_and_capture(main, cfg.LOG_FILES_NUM)
    sys.exit(rc)
