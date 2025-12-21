#!/usr/bin/env python3
# cfg.py

"""
Módulo de Configuración y Utilidades de Sistema.

Este módulo centraliza todas las constantes, variables de entorno y herramientas 
base del proyecto. Gestiona la configuración de red, rutas de archivos, 
manipulación de marcas de tiempo (timestamps) y un sistema avanzado de logging 
con rotación automática de archivos y duplicación de flujo (Tee).
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
from contextlib import redirect_stdout, redirect_stderr
from dotenv import load_dotenv

load_dotenv()

# =============================
# 2. CONFIGURACIÓN (Valores por defecto)
# =============================

#: URL base de la API del sensor
API_URL = os.getenv("API_URL", "https://rsm.ane.gov.co:12443/api/sensor")
#: Modo de depuración detallado
VERBOSE = os.getenv("VERBOSE", "false").lower() == "true"
#: Indica si el sistema corre en entorno de desarrollo (usa DUMMY_MAC)
DEVELOPMENT = os.getenv("DEVELOPMENT", "false").lower() == "true"
#: Dirección MAC de respaldo para pruebas
DUMMY_MAC = os.getenv("DUMMY_MAC", "d0:65:78:9c:dd:d0")
#: Número máximo de archivos de log a conservar
LOG_FILES_NUM = int(os.getenv("LOG_FILES_NUM", "10"))
#: Cantidad de líneas antes de rotar un archivo de log
LOG_ROTATION_LINES = int(os.getenv("LOG_ROTATION_LINES", "50"))

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

# Rutas del Sistema
_THIS_FILE = pathlib.Path(__file__).resolve()
PROJECT_ROOT = _THIS_FILE.parent
QUEUE_DIR = PROJECT_ROOT / "Queue"
LOGS_DIR = PROJECT_ROOT / "Logs"
HISTORIC_DIR = PROJECT_ROOT / "Historic"

#: Ruta al ejecutable de Python dentro del entorno virtual
PYTHON_ENV = (PROJECT_ROOT / "venv"/ "bin"/ "python3").absolute()
PYTHON_ENV_STR = str(PYTHON_ENV)

# =============================
# 3. HELPERS
# =============================

def get_time_ms() -> int:
    """
    Obtiene el timestamp actual en milisegundos ajustado a Colombia (UTC-5).

    Returns:
        int: Tiempo transcurrido en milisegundos desde la época Unix, 
             restando el desfase de 5 horas.
    """
    return int(time.time() * 1000) - (5 * 60 * 60 * 1000)

def human_readable(ts_ms: int, target_tz: str = "UTC") -> str:
    """
    Convierte un timestamp en milisegundos a una cadena legible.

    Args:
        ts_ms (int): Timestamp en milisegundos.
        target_tz (str): Zona horaria de destino (ej. 'America/Bogota').

    Returns:
        str: Fecha y hora formateada como 'AAAA-MM-DD HH:MM:SS'.
    """
    dt_utc = datetime.fromtimestamp(ts_ms / 1000, tz=ZoneInfo("UTC"))
    dt_local = dt_utc.astimezone(ZoneInfo(target_tz))
    return dt_local.strftime('%Y-%m-%d %H:%M:%S')

def get_mac() -> str:
    """
    Intenta obtener la dirección MAC física del dispositivo.

    Prioriza las interfaces inalámbricas (wlan) y descarta interfaces virtuales 
    o de loopback. En modo DEVELOPMENT, retorna la DUMMY_MAC.

    Returns:
        str: Dirección MAC formateada (ej. '00:11:22:33:44:55').
    """
    if DEVELOPMENT: return DUMMY_MAC
    try:
        interfaces = os.listdir("/sys/class/net")
        interfaces.sort(key=lambda x: (not x.startswith("wlan"), x))
        for iface in interfaces:
            if iface.startswith(("lo", "sit", "docker", "veth", "vir", "br", "tun", "wg")):
                continue
            try:
                with open(f"/sys/class/net/{iface}/address") as f:
                    mac = f.read().strip()
                if mac and mac != "00:00:00:00:00:00": return mac
            except OSError: continue
    except Exception: pass
    return "00:00:00:00:00:00"

# =============================
# 4. IMPLEMENTACIÓN DE LOGGING
# =============================

class _CurrentStreamProxy:
    """
    Proxy para los flujos estándar de salida.
    
    Asegura que las llamadas a write() se redirijan al stream actual 
    de sys.stdout o sys.stderr.
    """
    def __init__(self, stream_name: str):
        self._stream_name = stream_name
    def _get_current_stream(self):
        return getattr(sys, self._stream_name)
    def write(self, data):
        return self._get_current_stream().write(data)
    def flush(self):
        return self._get_current_stream().flush()
    def __getattr__(self, name):
        return getattr(self._get_current_stream(), name)

class Tee:
    """
    Clase para duplicar flujos de salida.
    
    Permite escribir simultáneamente en un flujo primario (usualmente la consola) 
    y en un flujo secundario (un archivo).
    """
    def __init__(self, primary, secondary):
        """
        Args:
            primary: Flujo principal (stdout/stderr).
            secondary: Flujo secundario (archivo).
        """
        self.primary = primary
        self.secondary = secondary
        self.encoding = getattr(primary, "encoding", "utf-8")

    def write(self, data):
        if data is None: return 0
        s = str(data)
        try: self.primary.write(s)
        except: pass
        try: 
            self.secondary.write(s)
            self.secondary.flush() 
        except: pass
        return len(s)

    def flush(self):
        try: self.primary.flush()
        except: pass
        try: self.secondary.flush()
        except: pass

    def fileno(self):
        return self.primary.fileno()

    def isatty(self):
        return getattr(self.primary, "isatty", lambda: False)()

class SmartRotatingFile:
    """
    Gestor de archivos de log con rotación basada en líneas.
    
    Monitorea la cantidad de líneas escritas y, al superar un umbral, 
    cierra el archivo actual y abre uno nuevo, manteniendo el límite 
    máximo de archivos configurado en el sistema.
    """
    def __init__(self, module_name: str, max_lines: int, max_files: int):
        """
        Args:
            module_name (str): Nombre del módulo que genera los logs.
            max_lines (int): Límite de líneas para disparar la rotación.
            max_files (int): Cantidad máxima de archivos históricos permitidos.
        """
        self.module_name = module_name
        self.max_lines = max_lines
        self.max_files = max_files
        self.current_lines = 0
        self.file_handle = None
        self._open_new_file()

    def _cleanup_old_logs(self):
        """Elimina los archivos de log más antiguos para respetar el límite de almacenamiento."""
        try:
            logs = sorted([p for p in LOGS_DIR.glob("*.log")], key=lambda p: p.stat().st_mtime)
            while len(logs) >= self.max_files:
                oldest = logs.pop(0)
                try: oldest.unlink(missing_ok=True)
                except Exception: pass
        except Exception: 
            pass

    def _open_new_file(self):
        """Abre un nuevo archivo de log utilizando un timestamp como nombre único."""
        if self.file_handle:
            try:
                self.file_handle.write("\n[[LOG ROTATED]]\n")
                self.file_handle.close()
            except: pass

        self._cleanup_old_logs()
        timestamp = get_time_ms()
        filename = LOGS_DIR / f"{timestamp}_{self.module_name}.log"
        self.file_handle = open(filename, "w", encoding="utf-8", buffering=1)
        self.current_lines = 0

    def write(self, data):
        """Escribe datos y evalúa la necesidad de rotar el archivo."""
        if not self.file_handle: return
        s = str(data)
        self.file_handle.write(s)
        self.file_handle.flush()
        
        if self.max_lines > 0:
            self.current_lines += s.count('\n')
            if self.current_lines >= self.max_lines:
                self._open_new_file()

    def flush(self):
        if self.file_handle:
            self.file_handle.flush()

    def close(self):
        """Cierra el archivo y registra una marca de finalización exitosa si estaba vacío."""
        if self.file_handle:
            if self.current_lines == 0:
                self.file_handle.write("\n[[OK]]\n")
            self.file_handle.close()
            self.file_handle = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

class SimpleFormatter(logging.Formatter):
    """Formateador de logs personalizado para alinear niveles de severidad."""
    def format(self, record):
        if record.exc_info: record.levelname = "EXCEPTION"
        record.levelname = f"{record.levelname:<9}"
        return super().format(record)

def set_logger() -> logging.Logger:
    """
    Configura e inicializa el logger para el módulo que lo invoca.

    Establece el nivel de consola según el modo VERBOSE y aplica el 
    SimpleFormatter.

    Returns:
        logging.Logger: Instancia configurada del logger.
    """
    try: name = pathlib.Path(sys.argv[0]).stem.upper()
    except: name = "SENSOR"

    logger = logging.getLogger(name)
    if logger.hasHandlers(): return logger

    logger.setLevel(logging.DEBUG)
    formatter = SimpleFormatter(
        "%(asctime)s[%(name)s]%(levelname)s %(message)s", 
        datefmt="%d-%b-%y(%H:%M:%S)"
    )

    stdout_proxy = _CurrentStreamProxy('stdout')
    handler = logging.StreamHandler(stdout_proxy)
    handler.setLevel(logging.INFO if VERBOSE else logging.WARNING)
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    return logger

# =============================
# 5. CAPTURA DE EJECUCIÓN
# =============================

TargetFunc = Union[Callable[[], int], Callable[[], Coroutine[Any, Any, int]]]

def run_and_capture(func: TargetFunc, num_files=LOG_FILES_NUM) -> int:
    """
    Envuelve la ejecución de una función (síncrona o asíncrona) capturando su salida.

    Redirige stdout y stderr a través de un objeto Tee hacia un SmartRotatingFile. 
    Esto garantiza que todo lo que se imprima o se registre mediante logs se guarde 
    físicamente con rotación automática.

    Args:
        func (TargetFunc): Función o corrutina a ejecutar.
        num_files (int): Límite de archivos de log para esta ejecución.

    Returns:
        int: Código de retorno (RC) de la función ejecutada.
    """
    try: module = pathlib.Path(sys.argv[0]).stem
    except: module = "app"
    
    rc = 1
    orig_out, orig_err = sys.stdout, sys.stderr
    
    rotating_log = SmartRotatingFile(
        module_name=module, 
        max_lines=LOG_ROTATION_LINES, 
        max_files=num_files
    )

    try:
        with rotating_log as f:
            tee_out = Tee(orig_out, f)
            tee_err = Tee(orig_err, f)
            
            with redirect_stdout(tee_out), redirect_stderr(tee_err):
                logging.getLogger().info(f"Log system started. Rotation limit: {LOG_ROTATION_LINES} lines.")
                
                try:
                    if inspect.iscoroutinefunction(func):
                        rc = asyncio.run(func())
                    else:
                        rc = func()
                        
                except KeyboardInterrupt:
                    logging.getLogger().warning("Received KeyboardInterrupt. Exiting...")
                    rc = 0
                except SystemExit as e:
                    rc = e.code if isinstance(e.code, int) else 1
                except asyncio.CancelledError:
                    logging.getLogger().warning("Async task cancelled.")
                    rc = 1
                except Exception:
                    traceback.print_exc()
                    rc = 1
            
    finally:
        sys.stdout, sys.stderr = orig_out, orig_err

    if rc is None: return 0
    if isinstance(rc, bool): return int(rc)
    return int(rc)

def debug() -> int:
    """Función de prueba para validar la rotación de logs y configuración."""
    log = set_logger()
    log.info("--- cfg.py debug ---")
    log.info(f"PROJECT_ROOT: {PROJECT_ROOT}")
    log.info(f"LOGS_DIR: {LOGS_DIR}")
    log.info(f"LOG_ROTATION_LINES: {LOG_ROTATION_LINES}")
    log.info("Simulating loop for rotation test...")
    
    for i in range(1, 150):
        log.info(f"Log Line {i} - testing rotation")
        time.sleep(0.01)

    log.info("--- cfg.py debug end ---")
    return 0

if __name__ == "__main__":
    rc = run_and_capture(debug)
    sys.exit(rc)