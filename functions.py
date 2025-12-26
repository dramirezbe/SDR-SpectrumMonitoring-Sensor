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

class SimpleDCSpikeCleaner:
    """
    Limpia el artefacto DC reemplazando el 2% central con una línea 
    que une los extremos, añadiendo ruido local que se adapta a 
    la potencia de los vecinos.
    """
    def __init__(self, width_frac=0.02, neighbor_bins=20):
        self.width_frac = width_frac
        self.neighbor_bins = neighbor_bins
        self._eps_mw = 1e-18

    def _dbm_to_mw(self, x_dbm):
        return 10.0 ** (np.asarray(x_dbm, float) / 10.0)

    def _mw_to_dbm(self, x_mw):
        x = np.maximum(np.asarray(x_mw, float), self._eps_mw)
        return 10.0 * np.log10(x)

    def _get_local_stats(self, data_mw):
        """Calcula mediana y sigma robusta."""
        if data_mw.size == 0:
            return 0.0, 0.0
        med = np.median(data_mw)
        # Sigma basada en MAD (Median Absolute Deviation)
        mad = np.median(np.abs(data_mw - med))
        sigma = 1.4826 * mad
        return med, sigma

    def clean(self, Pxx):
        # 1) Preparación y conversión a mW
        Pxx_mw = self._dbm_to_mw(Pxx)
        n = len(Pxx_mw)
        mid = n // 2
        
        # Definir el rango del 2% central
        half_width = max(1, int(n * (self.width_frac / 2)))
        idx0 = mid - half_width
        idx1 = mid + half_width
        
        if idx0 <= self.neighbor_bins or idx1 >= n - self.neighbor_bins:
            return Pxx # Array muy pequeño para procesar

        # 2) Analizar vecinos a la izquierda y derecha para detectar cambios de potencia
        l_neighbor = Pxx_mw[idx0 - self.neighbor_bins : idx0]
        r_neighbor = Pxx_mw[idx1 + 1 : idx1 + 1 + self.neighbor_bins]
        
        val_l, sig_l = self._get_local_stats(l_neighbor)
        val_r, sig_r = self._get_local_stats(r_neighbor)

        # 3) Reconstrucción
        num_points = idx1 - idx0 + 1
        
        # Generar la línea base (la pendiente entre extremos)
        # Usamos los valores exactos de los bordes para que no haya saltos
        edge_l = Pxx_mw[idx0 - 1]
        edge_r = Pxx_mw[idx1 + 1]
        interp_line = np.linspace(edge_l, edge_r, num_points)
        
        # Generar un perfil de ruido que transiciona de sig_l a sig_r
        # Esto hace que si un lado es más "ruidoso" que el otro, se note el cambio
        sig_profile = np.linspace(sig_l, sig_r, num_points)
        noise = np.random.normal(0, 1, num_points) * sig_profile
        
        # Combinar y asegurar que no haya valores negativos antes de volver a dBm
        fill = interp_line + noise
        fill = np.maximum(fill, self._eps_mw)
        
        # 4) Aplicar corrección
        Pxx_mw[idx0 : idx1 + 1] = fill
        
        return self._mw_to_dbm(Pxx_mw)
    
class AcquireRealtime:
    def __init__(self, controller, cleaner, hardware_max_bw=20_000_000, user_safe_bw=18_000_000, log=None):
        self._log = log
        self.controller = controller
        self.cleaner = cleaner
        self.HW_BW = hardware_max_bw      
        self.SAFE_BW = user_safe_bw       
        self.OFFSET = 1_000_000           

    async def acquire_with_offset(self, user_config):
        """
        Aplica Offset + Limpieza Central + Recorte.
        """
        requested_fs = user_config.get("sample_rate_hz", 0)
        original_center = user_config.get("center_freq_hz")

        # Caso A: BW pequeño permite mover el DC fuera del centro del usuario
        if requested_fs <= self.SAFE_BW:
            hw_config = user_config.copy()
            hw_config["sample_rate_hz"] = self.HW_BW
            # Desplazamos el hardware para que su centro (y su spike) no coincida con el del usuario
            hw_config["center_freq_hz"] = original_center + self.OFFSET
            
            raw_payload = await self._send_and_receive(hw_config)
            if not raw_payload: return None

            # 1. Convertimos a array
            pxx_raw = np.array(raw_payload["Pxx"])
            
            # 2. LIMPIEZA: El nuevo cleaner actúa sobre el 2% central del array de 20MHz.
            # Aquí es donde se elimina el spike DC físico del hardware.
            pxx_cleaned = self.cleaner.clean(pxx_raw)

            # 3. RECORTE: Extraemos la zona que el usuario pidió del array ya limpio.
            # IMPORTANTE: Usamos pxx_cleaned, no pxx_raw.
            final_data = self._extract_sub_region(
                pxx_cleaned, 
                hw_center=original_center + self.OFFSET,
                hw_bw=self.HW_BW,
                target_center=original_center,
                target_bw=requested_fs
            )
            return final_data

        # Caso B: BW grande, solo podemos limpiar el centro y entregar todo
        else:
            if self._log: self._log.info(f"BW {requested_fs}Hz muy grande. Solo limpieza central.")
            raw_payload = await self._send_and_receive(user_config)
            if not raw_payload: return None
            
            pxx = np.array(raw_payload["Pxx"])
            # Limpia el spike en el centro exacto del espectro solicitado
            raw_payload["Pxx"] = self.cleaner.clean(pxx).tolist()
            return raw_payload

    async def acquire_raw(self, config):
        """Adquisición estándar con limpieza."""
        payload = await self._send_and_receive(config)
        if not payload or "Pxx" not in payload:
            return None

        pxx = np.array(payload["Pxx"])
        payload["Pxx"] = self.cleaner.clean(pxx).tolist()
        return payload

    async def _send_and_receive(self, config):
        await self.controller.send_command(config)
        try:
            return await asyncio.wait_for(self.controller.wait_for_data(), timeout=10)
        except asyncio.TimeoutError:
            return None

    def _extract_sub_region(self, pxx, hw_center, hw_bw, target_center, target_bw):
        """Extrae la sub-banda del array ya procesado."""
        num_bins = len(pxx)
        hz_per_bin = hw_bw / num_bins
        hw_min_f = hw_center - (hw_bw / 2)
        
        target_min_f = target_center - (target_bw / 2)
        target_max_f = target_center + (target_bw / 2)

        start_idx = int((target_min_f - hw_min_f) / hz_per_bin)
        end_idx = int((target_max_f - hw_min_f) / hz_per_bin)

        # Clamp de índices
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
        Smarter acquisition using Level Normalization and Alpha Blending
        to eliminate patching artifacts and DC spikes.
        """
        orig_params = deepcopy(rf_params)
        orig_cf = orig_params["center_freq_hz"]

        # 1. Double Acquisition
        data1 = await self._single_acquire(orig_params)
        offset_params = deepcopy(orig_params)
        offset_params["center_freq_hz"] = orig_cf + self.OFFSET_HZ
        await asyncio.sleep(0.5)
        data2 = await self._single_acquire(offset_params)

        try:
            pxx1 = np.array(data1['Pxx'])
            pxx2 = np.array(data2['Pxx'])
            
            df = (data1['end_freq_hz'] - data1['start_freq_hz']) / len(pxx1)
            bin_shift = int(self.OFFSET_HZ / df)
            
            # 1. Define the center and half-width
            center_idx = len(pxx1) // 2
            half_patch = int((self.PATCH_BW_HZ / df) // 2)

            # 2. Calculate indices for Capture 1 (The target)
            s1, e1 = center_idx - half_patch, center_idx + half_patch
            
            # 3. Calculate indices for Capture 2 (The source)
            s2, e2 = s1 - bin_shift, e1 - bin_shift

            # 4. DETERMINE ACTUAL SLICE LENGTH (This prevents the 818 vs 819 error)
            actual_len = e1 - s1 

            if s2 < 0 or e2 > len(pxx2):
                self._log.warning("Offset capture indices out of range.")
                return data1

            # --- STEP 1: LEVEL MATCHING ---
            guard_bins = int(100e3 / df)
            ref_s, ref_e = s1 - guard_bins, s1
            
            if ref_s > 0:
                level1 = np.median(pxx1[ref_s:ref_e])
                level2 = np.median(pxx2[ref_s - bin_shift : ref_e - bin_shift])
                gain_corr = level1 / level2
                # Use actual_len to slice Capture 2
                pxx2_patch = pxx2[s2 : s2 + actual_len] * gain_corr
            else:
                pxx2_patch = pxx2[s2 : s2 + actual_len]

            # --- STEP 2: ALPHA BLENDING ---
            # Create mask based on ACTUAL length of the slice
            mask = np.ones(actual_len)
            blend_width = max(1, int(actual_len * 0.1)) 
            
            ramp = np.linspace(0, 1, blend_width)
            mask[:blend_width] = ramp
            mask[-blend_width:] = ramp[::-1]

            # Now all arrays are guaranteed to be (actual_len,)
            pxx1[s1:e1] = (pxx1[s1:e1] * (1 - mask)) + (pxx2_patch * mask)

            self._log.info(f"Smart DC correction applied at {orig_cf/1e6} MHz. Slice size: {actual_len}")
            data1['Pxx'] = pxx1.tolist()
            return data1

        except Exception as e:
            self._log.error(f"Failed smart DC spike correction: {e}")
            return data1