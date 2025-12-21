#!/usr/bin/env python3
# init_sys.py

"""
Módulo de Inicialización Crítica del Sistema.

Este script se ejecuta durante el arranque del sistema (power-on) para garantizar 
que el entorno esté limpio y correctamente configurado antes de iniciar los servicios 
principales. Sus responsabilidades incluyen:

* Limpieza del Crontab de campañas previas.
* Vaciado de la memoria compartida persistente.
* Creación de la estructura de directorios necesaria.
* Generación dinámica de archivos de servicio (daemons) y timers de systemd.
"""

import sys
import cfg
from functions import CronSchedulerCampaign, ShmStore

# Inicialización del Logger
log = cfg.set_logger()

# Directorio donde se generarán los archivos de servicio de systemd
DAEMONS_DIR = cfg.PROJECT_ROOT / "daemons"

# --- DEFINICIONES DE DAEMONS ---
# Estas cadenas contienen las plantillas para los servicios y timers de systemd.

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

# --- LÓGICA PRINCIPAL ---

def save_daemon_file(filename: str, content: str):
    """
    Guarda el contenido de la configuración de un daemon en la carpeta de daemons.

    Toma una cadena de texto con el formato de unidad de systemd y la escribe en el 
    disco, sobreescribiendo archivos existentes si es necesario.

    Args:
        filename (str): Nombre del archivo a crear (ej. 'rf-ane2.service').
        content (str): Contenido completo de la configuración del servicio/timer.

    Raises:
        OSError: Si hay un error al escribir el archivo en el sistema de archivos.
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
    Ejecuta el flujo secuencial de inicialización del sistema.

    Sigue los pasos de creación de directorios, limpieza de tareas programadas (cron), 
    vaciado de memoria compartida y persistencia de archivos de servicio de sistema.

    Returns:
        int: Código de salida (0 para éxito, no nulo si ocurre un error crítico).
    """
    log.info("Starting System Initialization...")

    # 1. Asegurar que todos los directorios existan
    # Incluimos DAEMONS_DIR para poder escribir en él inmediatamente después
    for p in [cfg.QUEUE_DIR, cfg.LOGS_DIR, cfg.HISTORIC_DIR, DAEMONS_DIR]:
        p.mkdir(parents=True, exist_ok=True)

    # 2. Limpiar el Crontab de campañas antiguas
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

    # 3. Limpiar la memoria compartida (Shared memory)
    try:
        store = ShmStore()
        store.clear_persistent()
        log.info("Shared memory cleared.")
    except Exception as e:
        log.error(f"Error clearing Shared Memory: {e}")

    # 4. Crear archivos de daemons personalizados
    # Los nombres de archivo corresponden a los requerimientos de systemd
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