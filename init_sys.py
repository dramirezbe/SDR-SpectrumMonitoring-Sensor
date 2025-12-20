#!/usr/bin/env python3
# init_sys.py

"""
TODO When system power on

* Ensure cleaning Crontab
* Ensure cleaning Shared memory
* Create personalized daemons files
* Ensure all directories exist
* Retry queue first time
* Kalibrate first time
"""

import sys
import cfg
from functions import CronSchedulerCampaign, ShmStore

# Initialize Logger
log = cfg.set_logger()

# Define where the daemon files will be generated
DAEMONS_DIR = cfg.PROJECT_ROOT / "daemons"

# --- DAEMON DEFINITIONS ---

RF_APP_DAEMON = f"""
[Unit]
Description=RF service ANE2
After=network.target

[Service]
User=anepi
WorkingDirectory={str(cfg.PROJECT_ROOT)}
ExecStart={str(cfg.PROJECT_ROOT)}/rf_app

Restart=always
RestartSec=5

StandardOutput=syslog
StandardError=syslog
SyslogIdentifier=rf-ane2

[Install]
WantedBy=multi-user.target
"""

LTEGPS_DAEMON = f"""
[Unit]
Description=LTE/GPS Service
After=network.target

[Service]
User=anepi
WorkingDirectory={str(cfg.PROJECT_ROOT)}
ExecStart={str(cfg.PROJECT_ROOT)}/ltegps_app

Restart=always
RestartSec=5

StandardOutput=syslog
StandardError=syslog
SyslogIdentifier=ltegps-ane2

[Install]
WantedBy=multi-user.target
"""

ORCHESTRATOR_DAEMON = f"""
[Unit]
Description=Orchestrator Service ANE2
Wants=network-online.target
After=network-online.target

[Service]
User=anepi
# Orchestrator is a continuous process
Restart=always
RestartSec=5

WorkingDirectory={str(cfg.PROJECT_ROOT)}
ExecStart={cfg.PYTHON_ENV_STR} orchestrator.py

StandardOutput=syslog
StandardError=syslog
SyslogIdentifier=orchestrator-ane2

[Install]
WantedBy=multi-user.target
"""

STATUS_DAEMON = f"""
[Unit]
Description=Status Service ANE2
Wants=network-online.target
After=network-online.target

[Service]
User=anepi
Type=oneshot

WorkingDirectory={str(cfg.PROJECT_ROOT)}

ExecStart={cfg.PYTHON_ENV_STR} status.py

StandardOutput=syslog
StandardError=syslog
SyslogIdentifier=status-ane2
"""

STATUS_DAEMON_TIMER = f"""
[Unit]
Description=Timer Status Service ANE2

[Timer]
OnBootSec=1min
OnUnitInactiveSec={cfg.INTERVAL_STATUS_S}s
AccuracySec=1s

[Install]
WantedBy=timers.target
"""

QUEUE_DAEMON = f"""
[Unit]
Description=Retry Queue Service ANE2
Wants=network-online.target
After=network-online.target

[Service]
User=anepi
Type=oneshot

WorkingDirectory={str(cfg.PROJECT_ROOT)}

ExecStart={cfg.PYTHON_ENV_STR} retry_queue.py

StandardOutput=syslog
StandardError=syslog
SyslogIdentifier=retry-queue-ane2
"""

QUEUE_DAEMON_TIMER = f"""
[Unit]
Description=Timer Retry Queue Service ANE2

[Timer]
OnBootSec=1min
OnUnitInactiveSec={cfg.INTERVAL_RETRY_QUEUE_S}s
AccuracySec=1s

[Install]
WantedBy=timers.target
"""

# --- MAIN LOGIC ---

def save_daemon_file(filename: str, content: str):
    """Writes the daemon string to the daemons/ folder"""
    file_path = DAEMONS_DIR / filename
    try:
        with open(file_path, "w") as f:
            f.write(content.strip() + "\n")
        log.info(f"Generated daemon file: {file_path}")
    except Exception as e:
        log.error(f"Failed to write {filename}: {e}")

def main() -> int:
    log.info("Starting System Initialization...")

    # 1. Ensure all directories exist
    # We include DAEMONS_DIR here so we can write to it immediately after
    for p in [cfg.QUEUE_DIR, cfg.LOGS_DIR, cfg.HISTORIC_DIR, DAEMONS_DIR]:
        p.mkdir(parents=True, exist_ok=True)

    # 2. Ensure cleaning Crontab
    try:
        init_scheduler = CronSchedulerCampaign(
            poll_interval_s=cfg.INTERVAL_REQUEST_CAMPAIGNS_S,
            python_env=cfg.PYTHON_ENV_STR,
            cmd=str((cfg.PROJECT_ROOT / "campaign_runner.py").absolute()),
            logger=log
        )
        init_scheduler.cron.remove_all(comment=lambda c: c.startswith("CAMPAIGN_"))
        init_scheduler.cron.write()
        log.info("Crontab cleaned successfully.")
    except Exception as e:
        log.error(f"Error cleaning Crontab: {e}")

    # 3. Ensure cleaning Shared memory
    try:
        store = ShmStore()
        store.clear_persistent()
        log.info("Shared memory cleared.")
    except Exception as e:
        log.error(f"Error clearing Shared Memory: {e}")

    # 4. Create personalized daemons files
    # The filenames match the specific request
    save_daemon_file("rf-ane2.service", RF_APP_DAEMON)
    save_daemon_file("ltegps-ane2.service", LTEGPS_DAEMON)
    save_daemon_file("orchestrator-ane2.service", ORCHESTRATOR_DAEMON)

    save_daemon_file("retry-queue-ane2.service", QUEUE_DAEMON)
    save_daemon_file("retry-queue-ane2.timer", QUEUE_DAEMON_TIMER)

    save_daemon_file("status-ane2.service", STATUS_DAEMON)
    save_daemon_file("status-ane2.timer", STATUS_DAEMON_TIMER)

    log.info("System Initialization Complete.")
    return 0

if __name__ == "__main__":
    rc = cfg.run_and_capture(main)
    sys.exit(rc)