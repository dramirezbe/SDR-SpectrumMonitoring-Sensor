#!/usr/bin/env python3
# init_sys.py

"""
Módulo de Inicialización y Provisionamiento del Sistema.

Este script configura el entorno de ejecución del sensor, creando la estructura
de directorios necesaria, limpiando estados previos (Cron y SHM) y generando
los archivos de configuración para systemd.
"""

import sys
import cfg
from functions import CronSchedulerCampaign, ShmStore

log = cfg.set_logger()
DAEMONS_DIR = cfg.PROJECT_ROOT / "daemons"

# --- DEFINICIONES DE DAEMONS ---

RF_APP_DAEMON = f"""
[Unit]
Description=RF service ANE2
After=network.target

[Service]
User=anepi
WorkingDirectory={str(cfg.PROJECT_ROOT)}
# flock asegura instancia única usando un archivo lock temporal
ExecStart=/usr/bin/flock -n /tmp/rf_app.lock {str(cfg.PROJECT_ROOT)}/rf_app
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
ExecStart=/usr/bin/flock -n /tmp/ltegps_app.lock {str(cfg.PROJECT_ROOT)}/ltegps_app
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
Restart=always
RestartSec=5
WorkingDirectory={str(cfg.PROJECT_ROOT)}
ExecStartPre=/usr/bin/ping -c 1 -w 5 google.com
ExecStart=/usr/bin/flock -n /tmp/orchestrator.lock {cfg.PYTHON_ENV_STR} orchestrator.py
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
ExecStartPre=/usr/bin/ping -c 1 -w 5 google.com
ExecStart=/usr/bin/flock -n /tmp/status.lock {cfg.PYTHON_ENV_STR} status.py
StandardOutput=syslog
StandardError=syslog
SyslogIdentifier=status-ane2

[Install]
WantedBy=multi-user.target
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
ExecStartPre=/usr/bin/ping -c 1 -w 5 google.com
ExecStart=/usr/bin/flock -n /tmp/retry_queue.lock {cfg.PYTHON_ENV_STR} retry_queue.py
StandardOutput=syslog
StandardError=syslog
SyslogIdentifier=retry-queue-ane2

[Install]
WantedBy=multi-user.target
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

def save_daemon_file(filename: str, content: str):
    """
    Escribe el contenido de un archivo de unidad systemd en el directorio de daemons.

    Args:
        filename (str): Nombre del archivo (ej. 'rf-ane2.service').
        content (str): Texto plano con la configuración del servicio.
    """
    file_path = DAEMONS_DIR / filename
    try:
        with open(file_path, "w") as f:
            f.write(content.strip() + "\n")
        log.info(f"Generated daemon file: {file_path}")
    except Exception as e:
        log.error(f"Failed to write {filename}: {e}")

def main() -> int:
    """
    Flujo principal de inicialización.

    1. Crea directorios de trabajo (logs, queue, etc.).
    2. Limpia las campañas antiguas del Crontab del usuario.
    3. Reinicia la memoria compartida persistente.
    4. Genera los archivos .service y .timer basados en las plantillas 
       definidas y las rutas del archivo `cfg`.

    Returns:
        int: 0 si la inicialización fue exitosa, 1 si ocurrió un error crítico.
    """
    log.info("Starting System Initialization...")

    for p in [cfg.QUEUE_DIR, cfg.LOGS_DIR, cfg.HISTORIC_DIR, DAEMONS_DIR]:
        p.mkdir(parents=True, exist_ok=True)

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

    try:
        store = ShmStore()
        store.clear_persistent()
        log.info("Shared memory cleared.")
    except Exception as e:
        log.error(f"Error clearing Shared Memory: {e}")

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