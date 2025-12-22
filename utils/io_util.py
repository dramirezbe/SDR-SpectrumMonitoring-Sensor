# utils/io_util.py
"""
Módulo de Utilidades de E/S e Integridad de Datos.

Este módulo provee herramientas para el manejo seguro de archivos y persistencia:
1. **Escritura Atómica**: Evita la corrupción de archivos en caso de fallos.
2. **ShmStore**: Almacenamiento basado en RAM (/dev/shm) con bloqueo de archivos 
   (file locking) para comunicación segura entre procesos.
3. **Temporizadores**: Control de flujo basado en tiempo.
"""

from __future__ import annotations
from pathlib import Path
import tempfile
import os
import logging
import json
import time
import fcntl
from typing import Any 
from typing import Optional

# Configuración del logger local
log = logging.getLogger(__name__)

def atomic_write_bytes(target_path: Path, data: bytes) -> None:
    """
    Escribe datos en una ruta de forma atómica.

    Para evitar que un archivo quede corrupto o a medias tras un fallo de energía 
    o del sistema, esta función escribe primero en un archivo temporal y luego 
    reemplaza el archivo destino en una sola operación del sistema operativo.

    

    Args:
        target_path (Path): Ruta del archivo final.
        data (bytes): Contenido binario a escribir.

    Raises:
        Exception: Si ocurre un error durante la escritura, sincronización 
                   o reemplazo.
    """
    target_dir = target_path.parent
    target_dir.mkdir(parents=True, exist_ok=True)

    tmp_name: Optional[Path] = None
    try:
        # Creamos el temporal en el mismo directorio para asegurar que el replace() 
        # sea atómico (dentro del mismo sistema de archivos).
        with tempfile.NamedTemporaryFile(dir=str(target_dir), delete=False) as tmpf:
            tmp_name = Path(tmpf.name)
            tmpf.write(data)
            tmpf.flush()
            # Forzamos al kernel a escribir los datos físicamente en el disco/RAM
            os.fsync(tmpf.fileno())

        # Operación atómica de reemplazo
        if tmp_name:
            tmp_name.replace(target_path)

    except Exception as e:
        if tmp_name and tmp_name.exists():
            try:
                tmp_name.unlink(missing_ok=True)
            except Exception:
                log.warning("Error al limpiar archivo temporal %s: %s", tmp_name, e)
        raise


class ShmStore:
    """
    Almacenamiento de persistencia rápida en memoria compartida (RAM).

    Utiliza el sistema de archivos `/dev/shm` de Linux para almacenar un objeto 
    JSON. Es ideal para compartir variables de estado entre el motor de RF y 
    los scripts de Python sin desgastar la tarjeta SD.

    

    Atributos:
        filepath (str): Ruta completa al archivo en la memoria compartida.
    """

    def __init__(self, filename: str = "persistent.json"):
        """
        Inicializa el almacenamiento en RAM.

        Args:
            filename (str): Nombre del archivo JSON persistente.
        """
        self.filepath = os.path.join("/dev/shm", filename)
        
        # Inicializa el archivo si no existe (ej. tras un reinicio del sistema)
        if not os.path.exists(self.filepath):
            self._write_file({})

    def _read_file(self) -> dict:
        """
        Lee el contenido JSON de forma segura con un bloqueo compartido.

        Utiliza `fcntl.LOCK_SH` para permitir múltiples lectores simultáneos 
        pero bloquear a cualquier escritor.

        Returns:
            dict: Datos cargados del archivo o diccionario vacío si hay error.
        """
        if not os.path.exists(self.filepath):
            return {}
            
        try:
            with open(self.filepath, 'r') as f:
                # Espera permiso de lectura (bloqueo compartido)
                fcntl.flock(f, fcntl.LOCK_SH) 
                try:
                    return json.load(f)
                finally:
                    fcntl.flock(f, fcntl.LOCK_UN)
        except (json.JSONDecodeError, IOError):
            return {}

    def _write_file(self, data: dict):
        """
        Escribe datos JSON de forma segura con un bloqueo exclusivo.

        Utiliza `fcntl.LOCK_EX` para evitar que otros procesos lean o escriban 
        mientras se actualiza el archivo.

        Args:
            data (dict): Diccionario de datos a persistir.
        """
        with open(self.filepath, 'w') as f:
            fcntl.flock(f, fcntl.LOCK_EX) # Bloqueo exclusivo
            try:
                json.dump(data, f)
                f.flush()
                os.fsync(f.fileno()) # Persistencia inmediata en RAM
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)

    def add_to_persistent(self, key: str, value: Any):
        """
        Actualiza una clave específica sin afectar al resto de los datos.

        Args:
            key (str): Nombre de la clave.
            value (Any): Valor a almacenar.
        """
        current_data = self._read_file()
        current_data[key] = value
        self._write_file(current_data)

    def consult_persistent(self, key: str) -> Optional[Any]:
        """
        Consulta el valor de una clave.

        Args:
            key (str): Clave a buscar.

        Returns:
            Any | None: El valor encontrado o None si la clave no existe.
        """
        current_data = self._read_file()
        return current_data.get(key, None)
    
    def update_from_dict(self, data_dict: dict):
        """
        Actualiza múltiples valores de forma atómica mediante un diccionario.

        Args:
            data_dict (dict): Conjunto de pares clave-valor a actualizar.
        """
        current_data = self._read_file()
        if isinstance(data_dict, dict):
            current_data.update(data_dict)
        self._write_file(current_data)

    def clear_persistent(self):
        """Limpia todo el almacenamiento, dejándolo como un objeto vacío `{}`."""
        self._write_file({})


class ElapsedTimer:
    """
    Temporizador simple de cuenta regresiva.

    Permite verificar si ha transcurrido un intervalo de tiempo determinado 
    sin bloquear el hilo de ejecución.
    """
    def __init__(self):
        """Inicializa el tiempo final en cero."""
        self.end_time = 0

    def init_count(self, seconds: float):
        """
        Inicia la cuenta regresiva.

        Args:
            seconds (float): Segundos a esperar desde este momento.
        """
        self.end_time = time.time() + seconds

    def time_elapsed(self) -> bool:
        """
        Verifica si el tiempo ya transcurrió.

        Returns:
            bool: True si el tiempo actual superó el tiempo objetivo, False si no.
        """
        return time.time() >= self.end_time