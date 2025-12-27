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
import subprocess

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
    post_dict = {
        "Pxx": payload.get("Pxx", []),
        "start_freq_hz": int(payload.get("start_freq_hz", 0)),
        "end_freq_hz": int(payload.get("end_freq_hz", 0)),
        "timestamp": cfg.get_time_ms(),
        "mac": cfg.get_mac()
    }

    if payload.get("excursion_hz", 0) != 0:
        post_dict.update({"excursion_hz": int(payload.get("excursion_hz"))})

    if payload.get("depth", 0) != 0:
        post_dict.update({"depth": int(payload.get("depth"))})

    return post_dict

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
            self.cron = CronTab(user=True)
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
            "antenna_port": camp.get('antenna_port'),
            "window": camp.get('window'),
            "overlap": camp.get('overlap'),
            "lna_gain": camp.get('lna_gain'),
            "vga_gain": camp.get('vga_gain'),
            "antenna_amp": camp.get('antenna_amp'),
            "filter": camp.get('filter'),
            "method_psd": "pfb"
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

class AcquireDual:
    def __init__(self, controller, log):
        self.controller = controller
        self._log = log
        # These are initialized as defaults but updated dynamically
        self.OFFSET_HZ = 2e6  
        self.PATCH_BW_HZ = 1e6 

    def _update_stitching_params(self, sample_rate_hz):
        """
        Dynamically adjusts stitching constants based on hardware bandwidth.
        Prevents using an offset larger than the available Nyquist zone.
        """
        if sample_rate_hz >= 4_000_000:
            self.OFFSET_HZ = 2_000_000
            self.PATCH_BW_HZ = 1_000_000
            self._log.info(f"Stitching: Wide-Band Logic (Offset 2MHz, Patch 1MHz)")
        else:
            # For lower rates like 2MHz, a smaller offset is required to stay in-band
            self.OFFSET_HZ = 500_000
            self.PATCH_BW_HZ = 200_000 
            self._log.info(f"Stitching: Narrow-Band Logic (Offset 0.5MHz, Patch 0.2MHz)")

    async def _single_acquire(self, rf_params):
        """Low-level acquisition with PLL cooling time."""
        await self.controller.send_command(rf_params)
        self._log.debug(f"Acquiring CF: {rf_params['center_freq_hz']/1e6} MHz")
        data = await asyncio.wait_for(self.controller.wait_for_data(), timeout=10)
        # PLL/Hardware settle time
        await asyncio.sleep(0.05) 
        return data

    async def get_corrected_data(self, rf_params):
        sr = rf_params.get("sample_rate_hz", 8e6)
        self._update_stitching_params(sr)
        
        orig_params = deepcopy(rf_params)
        orig_cf = orig_params["center_freq_hz"]

        # 1. Adquisiciones
        data1 = await self._single_acquire(orig_params)
        offset_params = deepcopy(orig_params)
        offset_params["center_freq_hz"] = orig_cf + self.OFFSET_HZ
        data2 = await self._single_acquire(offset_params)

        try:
            pxx1 = np.array(data1['Pxx'])
            pxx2 = np.array(data2['Pxx'])
            total_bins = len(pxx1)
            
            df = (data1['end_freq_hz'] - data1['start_freq_hz']) / total_bins
            bin_shift = int(self.OFFSET_HZ / df)
            
            center_idx = total_bins // 2
            half_patch = int((self.PATCH_BW_HZ / df) // 2)
            s1, e1 = center_idx - half_patch, center_idx + half_patch
            
            s2 = s1 - bin_shift
            e2 = s2 + (e1 - s1)

            # --- MEJORA: VENTANA DE REFERENCIA POR PORCENTAJE (0.5%) ---
            
            # Calculamos k como el 0.5% del total de bins del espectro
            k = max(1, int(total_bins * 0.005))
            self._log.debug(f"Stitching: Usando ventana de referencia de {k} puntos ({0.5}%)")
            
            # Validación de límites para evitar IndexError en los bordes del array
            idx_start_min = max(0, s1 - k)
            idx_end_max = min(total_bins, e1 + k)

            # Calculamos el delta al inicio del parche (usando mediana para robustez)
            ref_start1 = np.median(pxx1[idx_start_min : s1])
            ref_start2 = np.median(pxx2[s2 - (s1 - idx_start_min) : s2])
            delta_start = ref_start1 - ref_start2
            
            # Calculamos el delta al final del parche
            ref_end1 = np.median(pxx1[e1 : idx_end_max])
            ref_end2 = np.median(pxx2[e2 : e2 + (idx_end_max - e1)])
            delta_end = ref_end1 - ref_end2
            
            # --- ALINEACIÓN DE PENDIENTE Y BLENDING ---
            
            # Rampa de corrección lineal para unir ambos deltas
            correction_slope = np.linspace(delta_start, delta_end, (e1 - s1))
            pxx2_patch = pxx2[s2:e2] + correction_slope

            actual_len = e1 - s1
            blend_width = max(2, int(actual_len * 0.15)) 
            mask = np.ones(actual_len)
            ramp = 0.5 * (1 - np.cos(np.pi * np.linspace(0, 1, blend_width)))
            mask[:blend_width] = ramp
            mask[-blend_width:] = ramp[::-1]

            # Inserción del parche corregido
            pxx1[s1:e1] = (pxx1[s1:e1] * (1 - mask)) + (pxx2_patch * mask)

            data1['Pxx'] = pxx1.tolist()
            return data1

        except Exception as e:
            self._log.error(f"Spectral correction failed: {e}")
            return data1