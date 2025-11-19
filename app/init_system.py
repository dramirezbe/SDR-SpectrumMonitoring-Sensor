#!/usr/bin/env python3
"""
@file init_system.py
@brief Initialize the system.
"""
import sys
import os
from pathlib import Path
import subprocess

import cfg
log = cfg.set_logger()

CREATE_SERVICE = False #DUMMY


ORCH_SERVICE_NAME = "orchestrator"
SERVICE_USER = "javastral"
ORCH_CMD = (cfg.PROJECT_ROOT / "build" / "orchestrator").resolve()
# Define the full paths for the systemd unit files
SYSTEMD_DIR = Path("/etc/systemd/system/")
SERVICE_FILE = SYSTEMD_DIR / f"{ORCH_SERVICE_NAME}.service"
TIMER_FILE = SYSTEMD_DIR / f"{ORCH_SERVICE_NAME}.timer"

SERVICE_FILE_CONTENT = f"""
[Unit]
Description=Orchestrator Service for RF Data Acquisition
# Ensures the network is available before starting
After=network.target

[Service]
Type=simple
User={SERVICE_USER}
WorkingDirectory={cfg.PROJECT_ROOT}
ExecStart={ORCH_CMD}
Restart=on-failure
RestartSec=5s

[Install]
WantedBy=multi-user.target
"""

FIRST_TIME_RUN = 15 # seconds
RUN_EACH = 10 # seconds

TIMER_FILE_CONTENT = f"""
[Unit]
Description=Timer to run the RF Orchestrator every 10 seconds
# This timer requires the service defined above
Requires={ORCH_SERVICE_NAME}.service

[Timer]
# Run {FIRST_TIME_RUN} seconds after boot for the first time
OnBootSec={FIRST_TIME_RUN}s
# Run {RUN_EACH} seconds after the last time the unit finished
OnUnitActiveSec={RUN_EACH}s
AccuracySec=1s

[Install]
WantedBy=timers.target
"""


def main() -> int:

    if os.geteuid() != 0:
        log.error("This script must be run with root privileges. Please use 'sudo'.")
        return 1

    TMP_DIR = (cfg.PROJECT_ROOT / "tmp").resolve()

    if cfg.VERBOSE:
        log.info(f"TMP_DIR: {TMP_DIR}")
        log.info(f"SAMPLES_DIR: {cfg.SAMPLES_DIR}")
        log.info(f"LOGS_DIR: {cfg.LOGS_DIR}")
        log.info(f"QUEUE_DIR: {cfg.QUEUE_DIR}")
        log.info(f"HISTORIC_DIR: {cfg.HISTORIC_DIR}")
        log.info(f"COMPILED_PATH: {cfg.COMPILED_PATH}")
        log.info(f"LIB_LTE: {cfg.LIB_LTE}")
    
    #create directories
    cfg.SAMPLES_DIR.mkdir(parents=True, exist_ok=True)
    cfg.LOGS_DIR.mkdir(parents=True, exist_ok=True)
    cfg.QUEUE_DIR.mkdir(parents=True, exist_ok=True)
    cfg.HISTORIC_DIR.mkdir(parents=True, exist_ok=True)
    TMP_DIR.mkdir(parents=True, exist_ok=True)

    if CREATE_SERVICE:

        try:

            if not ORCH_CMD.is_file():
                log.error(f"Orchestrator binary not found, expected in: {ORCH_CMD}")
                return 1
            
            try:
                subprocess.run(f"systemctl stop {ORCH_SERVICE_NAME}.service", check=True)
                subprocess.run(f"systemctl stop {ORCH_SERVICE_NAME}.timer", check=True)
            except subprocess.CalledProcessError:
                log.warning(f"Trying to create for first time the {ORCH_SERVICE_NAME} service")

            SERVICE_FILE.write_text(SERVICE_FILE_CONTENT)
            TIMER_FILE.write_text(TIMER_FILE_CONTENT)

            subprocess.run(f"systemctl daemon-reload", check=True)
            subprocess.run(f"systemctl enable --now {ORCH_SERVICE_NAME}.timer", check=True)
            subprocess.run(f"systemctl status {ORCH_SERVICE_NAME}.timer", check=True)

        except Exception as e:
            log.error(f"Failed to create the {ORCH_SERVICE_NAME} service: {e}")
            return 1

    return 0

if __name__ == "__main__":
    rc = cfg.run_and_capture(main, cfg.LOG_FILES_NUM)
    sys.exit(rc)