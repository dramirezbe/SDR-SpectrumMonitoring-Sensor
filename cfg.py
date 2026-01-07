#!/usr/bin/env python3
# cfg.py

"""
Módulo de Configuración y Gestión de Entorno.

Centraliza las constantes de la API, rutas de directorios y la lógica de 
identificación del sensor. Incluye un sistema de logging especializado que 
minimiza el riesgo de corrupción de datos en sistemas embebidos.
"""

from __future__ import annotations
import logging
import pathlib
import time
import sys
import traceback
import os
import asyncio 
import inspect 
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Callable, Union, Coroutine, Any
from contextlib import redirect_stderr
from dotenv import load_dotenv

# Importación de utilidad atómica
from utils import atomic_write_bytes

load_dotenv()

# =============================
# 2. CONFIGURACIÓN (API y Red)
# =============================

#: URL base de la API del sensor
API_URL = os.getenv("API_URL", "https://rsm.ane.gov.co:12443/api/sensor")
#: Modo de depuración detallado (Controla salida a consola)
VERBOSE = os.getenv("VERBOSE", "false").lower() == "true"
#: Entorno de desarrollo (usa DUMMY_MAC)
DEVELOPMENT = os.getenv("DEVELOPMENT", "false").lower() == "true"

#logging level to info in /Logs folder
DEBUG = os.getenv("DEBUG", "false").lower() == "true"

DUMMY_MAC = os.getenv("DUMMY_MAC", "d0:65:78:9c:dd:d0")

# Endpoints de la API
DATA_URL = os.getenv("DATA_URL", "/data")
STATUS_URL = os.getenv("STATUS_URL", "/status")
CAMPAIGN_URL = os.getenv("CAMPAIGN_URL", "/campaigns")
REALTIME_URL = os.getenv("REALTIME_URL", "/realtime")
GPS_URL = os.getenv("GPS_URL", "/gps")

#: Dirección del socket IPC para comunicación con el motor RF
IPC_ADDR = os.getenv("IPC_ADDR", "ipc:///tmp/rf_engine")

# Intervalos de tiempo
INTERVAL_REQUEST_CAMPAIGNS_S = int(os.getenv("INTERVAL_REQUEST_CAMPAIGNS_S", "60"))
INTERVAL_REQUEST_REALTIME_S = int(os.getenv("INTERVAL_REQUEST_REALTIME_S", "5"))
INTERVAL_STATUS_S = int(os.getenv("INTERVAL_STATUS_S", "30"))
INTERVAL_RETRY_QUEUE_S = int(os.getenv("INTERVAL_RETRY_QUEUE_S", "300"))

# =============================
# 3. RUTAS Y LOGGING CONFIG
# =============================

_THIS_FILE = pathlib.Path(__file__).resolve()
PROJECT_ROOT = _THIS_FILE.parent
QUEUE_DIR = PROJECT_ROOT / "Queue"
LOGS_DIR = PROJECT_ROOT / "Logs"
HISTORIC_DIR = PROJECT_ROOT / "Historic"

# Asegurar existencia de directorios base
for folder in [QUEUE_DIR, LOGS_DIR, HISTORIC_DIR]:
    folder.mkdir(parents=True, exist_ok=True)

#: Ruta al ejecutable de Python dentro del entorno virtual
PYTHON_ENV = (PROJECT_ROOT / "venv"/ "bin"/ "python3").absolute()
PYTHON_ENV_STR = str(PYTHON_ENV)

# Configuración de rotación
LOG_FILES_NUM = int(os.getenv("LOG_FILES_NUM", "10"))
LOG_ROTATION_LINES = int(os.getenv("LOG_ROTATION_LINES", "100"))

# =============================
# 4. HELPERS
# =============================

def get_time_ms() -> int:
    """
    Obtiene el tiempo actual en milisegundos.
    
    Ajusta el timestamp de Unix al desfase horario de Colombia (UTC-5) para 
    mantener consistencia con el servidor central.
    
    Returns:
        int: Timestamp en milisegundos.
    """
    return int(time.time() * 1000) - (5 * 60 * 60 * 1000)

def human_readable(ts_ms: int, target_tz: str = "UTC") -> str:
    """Convierte un timestamp ms a cadena legible."""
    dt_utc = datetime.fromtimestamp(ts_ms / 1000, tz=ZoneInfo("UTC"))
    dt_local = dt_utc.astimezone(ZoneInfo(target_tz))
    return dt_local.strftime('%Y-%m-%d %H:%M:%S')

def get_mac() -> str:
    """
    Escanea las interfaces de red del sistema para obtener la MAC física.
    
    Ignora interfaces virtuales (docker, loopback, tun) y prioriza 'wlan' 
    para asegurar una identificación única del hardware del sensor.
    
    Returns:
        str: Dirección MAC en formato 'xx:xx:xx:xx:xx:xx'.
    """
    if DEVELOPMENT: return DUMMY_MAC
    try:
        interfaces = os.listdir("/sys/class/net")
        interfaces.sort(key=lambda x: (not x.startswith("wlan"), x))
        for iface in interfaces:
            if iface.startswith(("lo", "docker", "veth", "br", "sit", "tun")): continue
            try:
                with open(f"/sys/class/net/{iface}/address") as f:
                    mac = f.read().strip()
                if mac and mac != "00:00:00:00:00:00": return mac
            except OSError: continue
    except Exception: pass
    return "00:00:00:00:00:00"

# =============================
# 5. LOGGING (ATOMIC & SD PROTECT)
# =============================

class AtomicRotator:
    """
    Manejador de archivos de log con rotación y escritura segura.
    
    Monitorea el número de líneas escritas y gestiona un ciclo de vida de 
    archivos (N archivos máximos) para evitar el llenado del almacenamiento.
    """
    def __init__(self, module_name: str, max_lines: int, max_files: int):
        self.module_name = module_name
        self.max_lines = max_lines
        self.max_files = max_files
        self.current_lines = 0
        self.current_file = self._generate_path()

    def _generate_path(self) -> pathlib.Path:
        """
        Genera la ruta del archivo con formato: DD-MM-YYYY_HH:MM:SS_module.log
        Mantiene consistencia con get_time_ms() sin modificar la función original.
        """
        ts_ms = get_time_ms()
        # Replicamos la lógica de human_readable pero con el formato solicitado
        dt = datetime.fromtimestamp(ts_ms / 1000, tz=ZoneInfo("UTC"))
        timestamp_str = dt.strftime('%d-%m-%Y_%H:%M:%S')
        
        return LOGS_DIR / f"{timestamp_str}_{self.module_name}.log"

    def _cleanup(self):
        try:
            logs = sorted(list(LOGS_DIR.glob("*.log")), key=lambda x: x.stat().st_mtime)
            while len(logs) >= self.max_files:
                logs.pop(0).unlink(missing_ok=True)
        except Exception: pass

    def write(self, data: str):
        if not data: return
        self.current_lines += data.count('\n')
        if self.current_lines >= self.max_lines:
            self._cleanup()
            self.current_file = self._generate_path()
            self.current_lines = 0
        try:
            content = self.current_file.read_bytes() if self.current_file.exists() else b""
            atomic_write_bytes(self.current_file, content + data.encode('utf-8'))
        except Exception: pass

    def flush(self): pass

class Tee:
    def __init__(self, primary, manager: AtomicRotator | None):
        self.primary = primary
        self.manager = manager

    def write(self, data):
        self.primary.write(data)
        if self.manager: self.manager.write(data)

    def flush(self): self.primary.flush()

class SimpleFormatter(logging.Formatter):
    def format(self, record):
        if record.exc_info: record.levelname = "EXCEPTION"
        record.levelname = f"{record.levelname:<9}"
        return super().format(record)

def set_logger(rotator: AtomicRotator | None = None) -> logging.Logger:
    """
    Configura logger asimétrico.
    Si DEBUG es True, ambos canales (consola y archivo) muestran nivel DEBUG.
    """
    try: name = pathlib.Path(sys.argv[0]).stem.upper()
    except: name = "SENSOR"

    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG) # Permitir que todo fluya hacia los handlers
    
    fmt = SimpleFormatter("%(asctime)s[%(name)s]%(levelname)s %(message)s", "%d-%b-%y(%H:%M:%S)")

    # --- Lógica de Niveles ---
    if DEBUG:
        # Si DEBUG está activo, máxima verbosidad en todo
        console_level = logging.DEBUG
        file_level = logging.DEBUG
    else:
        # Si no, usamos VERBOSE para consola y WARNING (por defecto) para archivos
        console_level = logging.INFO if VERBOSE else logging.ERROR
        file_level = logging.WARNING

    # 1. Configurar Consola
    if not any(isinstance(h, logging.StreamHandler) and not hasattr(h, 'is_rotator') for h in logger.handlers):
        c_handler = logging.StreamHandler(sys.stdout)
        c_handler.setLevel(console_level)
        c_handler.setFormatter(fmt)
        logger.addHandler(c_handler)

    # 2. Configurar Archivo (Rotator)
    if rotator and not any(hasattr(h, 'is_rotator') for h in logger.handlers):
        f_handler = logging.StreamHandler(rotator)
        f_handler.is_rotator = True 
        f_handler.setLevel(file_level)
        f_handler.setFormatter(fmt)
        logger.addHandler(f_handler)

    return logger

# =============================
# 6. CAPTURA DE EJECUCIÓN
# =============================

TargetFunc = Union[Callable[[], int], Callable[[], Coroutine[Any, Any, int]]]

def run_and_capture(func: TargetFunc, num_files=LOG_FILES_NUM) -> int:
    """
    Wrapper de ejecución segura para puntos de entrada (main).
    
    Configura automáticamente el logger del módulo, inicializa el rotador 
    atómico y captura cualquier excepción no controlada o señal de 
    interrupción (Ctrl+C) para cerrar el programa limpiamente.

    Args:
        func: Función o Corrutina a ejecutar.
        num_files: Cantidad máxima de archivos de log a conservar.

    Returns:
        int: Código de salida (0 para éxito, 1 para error).
    """
    try: module = pathlib.Path(sys.argv[0]).stem
    except: module = "app"
    
    rotator = AtomicRotator(module, LOG_ROTATION_LINES, num_files)
    # logger se configura internamente usando el rotator
    set_logger(rotator)
    
    orig_err = sys.stderr
    rc = 1

    try:
        with redirect_stderr(Tee(orig_err, rotator)):
            if inspect.iscoroutinefunction(func):
                rc = asyncio.run(func())
            else:
                rc = func()
    except KeyboardInterrupt:
        rc = 0
    except SystemExit as e:
        rc = e.code if isinstance(e.code, int) else 1
    except Exception:
        traceback.print_exc()
        rc = 1
    
    return int(rc if rc is not None else 0)

if __name__ == "__main__":
    def debug_test():
        l = set_logger()
        l.info("Info test - Visible in file if DEBUG is True")
        l.warning("Warning test - Always visible in file")
        return 0
    sys.exit(run_and_capture(debug_test))