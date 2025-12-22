#!/usr/bin/env python3
# functions.py

"""
Módulo de Funciones de Soporte y Lógica de Adquisición.

Este módulo centraliza la lógica de procesamiento de señales, la gestión de la 
máquina de estados global, la programación de tareas mediante Crontab y las 
estrategias de adquisición de datos para eliminar artefactos (picos DC).
"""

import cfg
from utils import ShmStore

from enum import Enum, auto
from crontab import CronTab
import logging
import numpy as np
import asyncio
from copy import deepcopy

class SysState(Enum):
    """
    Enumeración de los estados posibles del sistema.
    
    Attributes:
        IDLE: Sistema en espera de comandos.
        CAMPAIGN: Ejecutando una campaña programada.
        REALTIME: Modo de transmisión en tiempo real activo.
        KALIBRATING: Realizando calibración de hardware.
        ERROR: Estado de falla crítica.
    """
    IDLE = auto()
    CAMPAIGN = auto()
    REALTIME = auto()
    KALIBRATING = auto()
    ERROR = auto()

class GlobalSys:
    """
    Controlador estático del estado global del sistema.

    Proporciona métodos de clase para gestionar las transiciones de estado 
    y verificar la disponibilidad del sistema de manera centralizada.
    """
    current = SysState.IDLE
    log = cfg.set_logger()

    @classmethod
    def set(cls, new_state: SysState):
        """
        Cambia el estado actual del sistema y registra la transición.

        Args:
            new_state (SysState): El nuevo estado al que se desea transicionar.
        """
        if cls.current != new_state:
            cls.log.info(f"State Transition: {cls.current.name} -> {new_state.name}")
            cls.current = new_state

    @classmethod
    def is_idle(cls):
        """
        Verifica si el sistema está en estado de espera (IDLE).

        Returns:
            bool: True si el sistema está IDLE, False en cualquier otro caso.
        """
        return cls.current == SysState.IDLE

# --- HELPER FUNCTIONS ---
def format_data_for_upload(payload):
    """
    Estructura los datos procesados para su envío a la API.

    Añade metadatos esenciales como el timestamp del sistema y la dirección 
    MAC del dispositivo.

    Args:
        payload (dict): Diccionario con los datos espectrales (Pxx) y frecuencias.

    Returns:
        dict: Diccionario formateado listo para ser serializado como JSON.
    """
    return {
        "Pxx": payload.get("Pxx", []),
        "start_freq_hz": int(payload.get("start_freq_hz", 0)),
        "end_freq_hz": int(payload.get("end_freq_hz", 0)),
        "timestamp": cfg.get_time_ms(),
        "mac": cfg.get_mac()
    }

class CronSchedulerCampaign:
    """
    Gestor de programación de campañas basado en Crontab.

    Esta clase se encarga de traducir las ventanas de tiempo de las campañas 
    recibidas desde la API en tareas programadas del sistema operativo.
    """
    def __init__(self, poll_interval_s, python_env=None, cmd=None, logger=None):
        """
        Inicializa el programador.

        Args:
            poll_interval_s (int): Intervalo de consulta de la API en segundos.
            python_env (str, optional): Ruta al ejecutable de Python.
            cmd (str, optional): Ruta al script de ejecución de campañas.
            logger (logging.Logger, optional): Instancia de logger personalizada.
        """
        self.poll_interval_ms = poll_interval_s * 1000
        self.python_env = python_env if python_env else "/usr/bin/python3"
        self.cmd = f"{self.python_env} {cmd}"
        self.debug_file = (cfg.PROJECT_ROOT / "mock_crontab.txt").absolute()
        self._log = logger if logger else logging.getLogger(__name__)

        if cfg.DEVELOPMENT:
            self.debug_file.parent.mkdir(parents=True, exist_ok=True)
            if not self.debug_file.exists():
                self.debug_file.write_text("", encoding="utf-8")
            self.cron = CronTab(tabfile=str(self.debug_file))
        else:
            self.cron = CronTab(user=True)

    def _ts_to_human(self, ts_ms):
        """Convierte un timestamp en ms a formato legible (UTC)."""
        if ts_ms is None: return "None"
        return cfg.human_readable(ts_ms, target_tz="UTC")

    def _seconds_to_cron_interval(self, seconds):
        """Convierte segundos en una expresión de intervalo para Cron."""
        minutes = int(seconds / 60)
        if minutes < 1: minutes = 1 
        return f"*/{minutes} * * * *"

    def _job_exists(self, campaign_id):
        """Verifica si ya existe una tarea cron para el ID de campaña."""
        return any(self.cron.find_comment(f"CAMPAIGN_{campaign_id}"))

    def _remove_job(self, campaign_id):
        """Elimina la tarea cron asociada a una campaña específica."""
        if self._job_exists(campaign_id):
            self.cron.remove_all(comment=f"CAMPAIGN_{campaign_id}")
            self._log.info(f"🗑️ REMOVED Job ID {campaign_id}")

    def _upsert_job(self, camp, store: ShmStore):
        """Inserta o actualiza una tarea en el cron y actualiza la memoria compartida."""
        c_id = camp['campaign_id']
        
        # Actualización de Memoria Compartida
        dict_persist_params = {
            "campaign_id": c_id,
            "center_freq_hz": camp.get('center_freq_hz'),
            "sample_rate_hz": camp.get('sample_rate_hz'),
            "rbw_hz": camp.get('rbw_hz'),
            "span": camp.get('span'),
            "antenna_port": camp.get('antenna_port'),
            "window": camp.get('window'),
            "scale": camp.get('scale'),
            "overlap": camp.get('overlap'),
            "lna_gain": camp.get('lna_gain'),
            "vga_gain": camp.get('vga_gain'),
            "antenna_amp": camp.get('antenna_amp'),
            "filter": camp.get('filter')
        }
        try:
            store.update_from_dict(dict_persist_params)
            self._log.info(f"💾 SharedMemory UPDATED for Campaign {c_id} ({camp.get('center_freq_hz')} Hz)")
        except Exception:
            self._log.error("Failed to update store.")

        if self._job_exists(c_id): 
            return 

        period_s = camp['acquisition_period_s']
        schedule = self._seconds_to_cron_interval(period_s)
        job = self.cron.new(command=self.cmd, comment=f"CAMPAIGN_{c_id}")
        job.setall(schedule)
        self._log.info(f"🆕 ADDED Job ID {c_id} | Schedule: {schedule}")

    def sync_jobs(self, campaigns: list, current_time_ms: int, store: ShmStore) -> bool:
        """
        Sincroniza campañas con mayor verbosidad y logs legibles.
        """
        any_active = False
        now_human = cfg.human_readable(current_time_ms)
        
        self._log.info("="*60)
        self._log.info(f"🔍 SYNC START | System Time: {now_human} ({int(current_time_ms)} ms)")
        self._log.info("="*60)

        for camp in campaigns:
            c_id = camp['campaign_id']
            status = camp['status']
            
            # Tiempos de la campaña
            start_ms = camp['timeframe']['start']
            end_ms = camp['timeframe']['end']
            
            # Ventana de activación (con margen de poll_interval)
            window_open = start_ms - self.poll_interval_ms
            window_close = end_ms - self.poll_interval_ms
            
            is_in_window = window_open <= current_time_ms <= window_close

            # Formateo para logs
            start_h  = cfg.human_readable(start_ms)
            end_h    = cfg.human_readable(end_ms)
            w_open_h = cfg.human_readable(window_open)
            w_close_h= cfg.human_readable(window_close)

            self._log.info(f"📋 Campaign ID: {c_id} | Status: {status.upper()}")
            self._log.info(f"   ﹂ Timeframe: [{start_h}] TO [{end_h}]")
            self._log.info(f"   ﹂ Activation Window: {w_open_h} < [NOW] < {w_close_h}")

            # Lógica de descarte por status
            if status in ['canceled', 'error', 'finished']:
                self._log.warning(f"   ﹂ ❌ Skipping: Inactive status '{status}'")
                self._remove_job(c_id)
                continue

            # Lógica de ventana de tiempo
            if is_in_window:
                self._log.info(f"   ﹂ ✅ WITHIN WINDOW: Proceeding to upsert job.")
                self._upsert_job(camp, store)
                any_active = True
                # Break si solo se permite una campaña activa a la vez
                # break 
            else:
                reason = "Not started yet" if current_time_ms < window_open else "Already expired"
                self._log.info(f"   ﹂ ⏳ OUTSIDE WINDOW: {reason}")
                self._remove_job(c_id)

        self.cron.write()
        self._log.info("="*60)
        self._log.info(f"SYNC FINISHED | Active campaigns found: {any_active}")
        self._log.info("="*60)
        
        return any_active
    
class SimpleDCSpikeCleaner:
    """
    Algoritmo de limpieza para eliminar el pico DC del centro del espectro.

    Utiliza una técnica de interpolación lineal con ruido aleatorio basado en la 
    desviación estándar de los bins vecinos para "rellenar" la zona afectada 
    por el artefacto DC del hardware.
    """
    def __init__(self, search_frac=0.05, width_frac=0.005, neighbor_bins=20):
        """
        Args:
            search_frac (float): Fracción del espectro donde buscar el pico máximo.
            width_frac (float): Ancho de la zona a eliminar y reconstruir.
            neighbor_bins (int): Cantidad de bins laterales para estimar el ruido base.
        """
        self.search_frac = search_frac
        self.width_frac = width_frac
        self.neighbor_bins = neighbor_bins

    def clean(self, Pxx):
        """
        Aplica el algoritmo de limpieza sobre un array de densidades espectrales.

        Args:
            Pxx (np.array): Array original con los datos de potencia (PSD).

        Returns:
            np.array: Array procesado con el pico DC mitigado.
        """
        Pxx = np.asarray(Pxx, float).copy()
        n = len(Pxx)
        if n < self.neighbor_bins * 2: 
            return Pxx

        mid = n // 2
        search_radius = int(n * (self.search_frac / 2))
        s_start = max(0, mid - search_radius)
        s_end = min(n, mid + search_radius)
        peak_idx = s_start + np.argmax(Pxx[s_start:s_end])

        width_radius = max(1, int(n * (self.width_frac / 2)))
        idx0 = max(0, peak_idx - width_radius)
        idx1 = min(n - 1, peak_idx + width_radius)

        l_neighbor = Pxx[max(0, idx0 - self.neighbor_bins): idx0]
        r_neighbor = Pxx[idx1 + 1: min(n, idx1 + 1 + self.neighbor_bins)]
        neighbors = np.concatenate([l_neighbor, r_neighbor])
        
        local_sigma = np.std(neighbors) if neighbors.size > 0 else 0.0

        y0, y1 = Pxx[idx0], Pxx[idx1]
        num_points = idx1 - idx0 + 1
        linear_trend = np.linspace(y0, y1, num_points)

        safe_scale = max(0.0, local_sigma)
        noise = np.random.normal(0, safe_scale, num_points)
        
        Pxx[idx0:idx1 + 1] = linear_trend + noise
        return Pxx
    
class AcquireRealtime:
    """
    Controlador de adquisición de alta fidelidad para tiempo real.

    Implementa una técnica de desplazamiento de frecuencia (offset) y recorte 
    (crop) para mover el artefacto DC fuera de la banda de interés del usuario,
    garantizando un espectro más limpio.
    """
    def __init__(self, controller, cleaner, hardware_max_bw=20_000_000, user_safe_bw=18_000_000, log=cfg.set_logger()):
        """
        Args:
            controller (ZmqPairController): Controlador de comunicación ZMQ.
            cleaner (SimpleDCSpikeCleaner): Instancia del limpiador de picos.
            hardware_max_bw (int): Ancho de banda máximo real del hardware.
            user_safe_bw (int): Límite de ancho de banda para aplicar la técnica de offset.
        """
        self._log = log
        self.controller = controller
        self.cleaner = cleaner
        self.HW_BW = hardware_max_bw      
        self.SAFE_BW = user_safe_bw       
        self.OFFSET = 1_000_000           

    async def acquire_with_offset(self, user_config):
        """
        Adquiere datos aplicando un desplazamiento de frecuencia preventivo.

        Si el ancho de banda solicitado es <= 18MHz, desplaza la frecuencia central 
        del hardware 1MHz hacia arriba. Esto hace que el pico DC aparezca en +1MHz, 
        el cual es limpiado y posteriormente recortado para entregar exactamente 
        la frecuencia central que el usuario pidió sin el artefacto en el centro.

        Args:
            user_config (dict): Parámetros solicitados por el usuario.

        Returns:
            dict: Payload procesado, limpiado y recortado.
        """
        requested_fs = user_config.get("sample_rate_hz", 0)
        original_center = user_config.get("center_freq_hz")

        if requested_fs <= self.SAFE_BW:
            hw_config = user_config.copy()
            hw_config["sample_rate_hz"] = self.HW_BW
            hw_config["center_freq_hz"] = original_center + self.OFFSET
            
            raw_payload = await self._send_and_receive(hw_config)
            if not raw_payload: return None

            pxx = np.array(raw_payload["Pxx"])
            pxx_cleaned = self.cleaner.clean(pxx)

            final_data = self._extract_sub_region(
                pxx_cleaned, 
                hw_center=original_center + self.OFFSET,
                hw_bw=self.HW_BW,
                target_center=original_center,
                target_bw=requested_fs
            )
            return final_data
        else:
            self._log.info(f"Requested BW {requested_fs} > 18MHz. Skipping offset/crop.")
            raw_payload = await self._send_and_receive(user_config)
            if not raw_payload: return None
            
            pxx = np.array(raw_payload["Pxx"])
            raw_payload["Pxx"] = self.cleaner.clean(pxx).tolist()
            return raw_payload
        
    async def acquire_raw(self, config):
        """
        Realiza una adquisición directa sin desplazamientos ni recortes.

        Args:
            config (dict): Parámetros de configuración de radio.

        Returns:
            dict: Payload con corrección básica de picos.
        """
        payload = await self._send_and_receive(config)
        if not payload or "Pxx" not in payload:
            self._log.warning("Acquisition failed or returned empty payload.")
            return None

        pxx = np.array(payload["Pxx"])
        payload["Pxx"] = self.cleaner.clean(pxx).tolist()
        return payload

    async def _send_and_receive(self, config):
        """Envía comando al motor RF y espera la respuesta."""
        await self.controller.send_command(config)
        try:
            return await asyncio.wait_for(self.controller.wait_for_data(), timeout=10)
        except asyncio.TimeoutError:
            return None

    def _extract_sub_region(self, pxx, hw_center, hw_bw, target_center, target_bw):
        """
        Calcula y extrae los índices del array correspondientes a la sub-banda.
        """
        num_bins = len(pxx)
        hz_per_bin = hw_bw / num_bins
        hw_min_f = hw_center - (hw_bw / 2)
        target_min_f = target_center - (target_bw / 2)
        target_max_f = target_center + (target_bw / 2)

        start_idx = int((target_min_f - hw_min_f) / hz_per_bin)
        end_idx = int((target_max_f - hw_min_f) / hz_per_bin)

        start_idx = max(0, start_idx)
        end_idx = min(num_bins, end_idx)

        return {
            "Pxx": pxx[start_idx:end_idx].tolist(),
            "start_freq_hz": int(target_min_f),
            "end_freq_hz": int(target_max_f),
            "sample_rate_hz": target_bw
        }

class AcquireCampaign:
    """
    Estrategia de adquisición de grado campaña mediante costura espectral (Spectral Stitching).

    Realiza dos capturas: una en la frecuencia objetivo y otra desplazada 2MHz. 
    Posteriormente, reemplaza ("parchea") la sección central contaminada de la primera 
    captura con datos limpios de la segunda.
    """
    def __init__(self, controller, log):
        """
        Args:
            controller (ZmqPairController): Controlador de hardware.
            log (logging.Logger): Logger de sistema.
        """
        self.controller = controller
        self._log = log
        self.OFFSET_HZ = 2e6  
        self.PATCH_BW_HZ = 1e6 

    async def _single_acquire(self, rf_params):
        """Adquisición de bajo nivel con tiempo de enfriamiento para el PLL."""
        await self.controller.send_command(rf_params)
        self._log.debug(f"Acquiring CF: {rf_params['center_freq_hz']/1e6} MHz")
        data = await asyncio.wait_for(self.controller.wait_for_data(), timeout=20)
        await asyncio.sleep(0.2) 
        return data

    async def get_corrected_data(self, rf_params):
        """
        Ejecuta la doble adquisición y el parcheo quirúrgico de los datos.

        Returns:
            dict: Datos con el centro espectral reemplazado por la captura offset.
        """
        orig_params = deepcopy(rf_params)
        orig_cf = orig_params["center_freq_hz"]

        # 1. Captura primaria
        data1 = await self._single_acquire(orig_params)
        
        # 2. Captura offset (Shift +2MHz)
        offset_params = deepcopy(orig_params)
        offset_params["center_freq_hz"] = orig_cf + self.OFFSET_HZ
        data2 = await self._single_acquire(offset_params)

        try:
            pxx1 = np.array(data1['Pxx'])
            pxx2 = np.array(data2['Pxx'])
            
            df = (data1['end_freq_hz'] - data1['start_freq_hz']) / len(pxx1)
            bin_shift = int(self.OFFSET_HZ / df)

            patch_bins = int(self.PATCH_BW_HZ / df)
            center_idx = len(pxx1) // 2
            s1, e1 = center_idx - (patch_bins // 2), center_idx + (patch_bins // 2)

            s2, e2 = s1 - bin_shift, e1 - bin_shift

            if s2 >= 0 and e2 <= len(pxx2):
                pxx1[s1:e1] = pxx2[s2:e2]
                self._log.info(f"DC spike removed at {orig_cf/1e6} MHz.")
            else:
                self._log.warning("Offset capture too narrow to patch requested window.")

            data1['Pxx'] = pxx1.tolist()
            return data1

        except Exception as e:
            self._log.error(f"Failed to process DC spike correction: {e}")
            return data1