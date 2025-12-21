#!/usr/bin/env python3
# campaign_runner.py

"""
Módulo Ejecutor de Campañas (Campaign Runner).

Este script realiza un ciclo único de adquisición de datos de radiofrecuencia (RF) 
asociado a una campaña programada. Se encarga de leer los parámetros de hardware 
desde la memoria compartida, coordinar la captura con el motor RF, intentar la 
subida de datos a la API y gestionar el almacenamiento local (Cola de reintentos 
e Histórico) según el estado del disco.
"""

import cfg
import sys
import json
import asyncio
import time
from pathlib import Path
from utils import atomic_write_bytes, RequestClient, StatusDevice, ShmStore, ZmqPairController
from functions import format_data_for_upload, AcquireCampaign

# Configuración del registrador de eventos
log = cfg.set_logger()

class CampaignRunner:
    """
    Controlador para la ejecución de una tarea de adquisición de campaña.

    Atributos:
        status_obj (StatusDevice): Herramienta para monitorear el estado del hardware (disco, etc).
        cli (RequestClient): Cliente para realizar peticiones HTTP a la API central.
        store (ShmStore): Interfaz de acceso a la memoria compartida del sistema.
        campaign_id (int/str): Identificador de la campaña actual obtenido de la persistencia.
    """

    def __init__(self):
        """Inicializa los componentes de red, estado y acceso a memoria compartida."""
        self.status_obj = StatusDevice(logs_dir=cfg.LOGS_DIR, logger=log)
        self.cli = RequestClient(cfg.API_URL, mac_wifi=cfg.get_mac(), timeout=(5, 15), verbose=cfg.VERBOSE, logger=log)
        self.store = ShmStore()
        self.campaign_id = self.store.consult_persistent("campaign_id")

    def _get_rf_params(self) -> dict:
        """
        Recupera los parámetros de configuración de RF desde la memoria compartida.

        Returns:
            dict: Diccionario con parámetros como frecuencia central, span, ganancias, etc.
                  Retorna un diccionario vacío si ocurre un error de lectura.
        """
        keys = ["center_freq_hz", "span", "sample_rate_hz", "rbw_hz", "overlap", 
                "window", "scale", "lna_gain", "vga_gain", "antenna_amp", 
                "antenna_port", "ppm_error", "filter"]
        try:
            return {k: self.store.consult_persistent(k) for k in keys}
        except Exception as e:
            log.error(f"Error reading rf params: {e}")
            return {}

    def _get_disk_usage(self) -> float:
        """
        Calcula el porcentaje de uso actual del disco.

        Returns:
            float: Valor entre 0.0 y 1.0 que representa la ocupación del almacenamiento.
        """
        use = float(self.status_obj.get_disk().get("disk_mb", 0))
        total = float(self.status_obj.get_total_disk().get("disk_mb", 1))
        return use / total

    def _cleanup_disk(self, target_dir: Path, to_delete: int = 10):
        """
        Elimina archivos JSON antiguos para liberar espacio en disco.

        Args:
            target_dir (Path): Directorio donde se realizará la limpieza.
            to_delete (int): Cantidad de archivos a eliminar (los más antiguos primero).
        """
        try:
            files = sorted([p for p in target_dir.iterdir() if p.suffix == ".json"], 
                           key=lambda p: int(p.stem) if p.stem.isdigit() else 0)
            for f in files[:to_delete]:
                f.unlink()
        except Exception as e:
            log.error(f"Disk cleanup failed: {e}")

    def _save_data(self, data: dict, target_dir: Path) -> bool:
        """
        Guarda los datos adquiridos en un archivo JSON de forma atómica.

        Args:
            data (dict): El payload de datos a persistir.
            target_dir (Path): Directorio de destino (Queue o Historic).

        Returns:
            bool: True si el guardado fue exitoso, False de lo contrario.
        """
        try:
            timestamp = cfg.get_time_ms()
            json_bytes = json.dumps(data, separators=(",", ":")).encode("utf-8")
            target_path = target_dir / f"{timestamp}.json"
            atomic_write_bytes(target_path, json_bytes)
            return True
        except Exception as e:
            log.error(f"Save failed: {e}")
            return False

    async def acquire_payload(self, rf_cfg: dict):
        """
        Gestiona la comunicación ZMQ con el motor RF para obtener la muestra.

        Utiliza una estrategia de adquisición de campaña que realiza doble captura 
        para eliminar el pico DC (spectral stitching).

        Args:
            rf_cfg (dict): Configuración de radiofrecuencia a aplicar en el hardware.

        Returns:
            dict/None: El payload con los datos espectrales corregidos o None si falla.
        """
        self.store.add_to_persistent("campaign_runner_running", True)
        try:
            async with ZmqPairController(addr=cfg.IPC_ADDR, is_server=True) as zmq_ctrl:
                await asyncio.sleep(0.5)
                # AcquireCampaign aplica la lógica de "patching" para corregir el centro
                acquirer = AcquireCampaign(zmq_ctrl, log)
                log.info(f"Starting Campaign Acquisition ID: {self.campaign_id}")
                return await acquirer.get_corrected_data(rf_cfg)
        except OSError as e:
            if "Address already in use" in str(e):
                log.warning("⚠️ ZMQ Socket busy. Skipping.")
            return None
        finally:
            self.store.add_to_persistent("campaign_runner_running", False)

    async def run(self) -> int:
        """
        Orquestador principal del flujo de ejecución del runner.

        Pasos del flujo:
            1. Carga de parámetros.
            2. Adquisición de datos mediante ZMQ.
            3. Intento de carga a la API.
            4. Gestión de almacenamiento: Si falla la red, guarda en cola; si tiene éxito, 
               gestiona el histórico y limpia el disco si es necesario.

        Returns:
            int: Código de salida (0 éxito, 1 fallo).
        """
        # 1. Preparación de parámetros
        rf_cfg = self._get_rf_params()
        if not rf_cfg:
            return 1

        # 2. Adquisición de hardware
        raw_payload = await self.acquire_payload(rf_cfg)
        if not raw_payload:
            return 1

        data_dict = format_data_for_upload(raw_payload)
        data_dict["campaign_id"] = self.campaign_id or 0

        # 3. Intento de carga a la nube
        start_t = time.perf_counter()
        rc, _ = self.cli.post_json(cfg.DATA_URL, data_dict)
        delta_t_ms = int((time.perf_counter() - start_t) * 1000)

        # 4. Gestión de Post-procesamiento
        if rc != 0:
            # Si falla la carga, intentamos guardar en la cola de reintentos
            if len(list(cfg.QUEUE_DIR.iterdir())) < 50:
                self._save_data(data_dict, cfg.QUEUE_DIR)
            return 1

        # Si la carga es exitosa, registramos la latencia
        self.store.add_to_persistent("delta_t_ms", delta_t_ms)
        
        # Gestión inteligente del disco
        usage = self._get_disk_usage()
        if usage > 0.8:
            log.info("Disk usage high. Triggering cleanup of Historic logs.")
            self._cleanup_disk(cfg.HISTORIC_DIR)
        
        # Guardado en histórico si hay espacio suficiente
        if usage < 0.9:
            self._save_data(data_dict, cfg.HISTORIC_DIR)

        return 0

if __name__ == "__main__":
    runner = CampaignRunner()
    rc = cfg.run_and_capture(runner.run)
    sys.exit(rc)