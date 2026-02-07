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
import re
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
    Controlador de la Máquina de Estados del Sistema.
    
    Asegura que el sensor no intente realizar dos tareas excluyentes simultáneamente 
    (ej. calibrar mientras se ejecuta una campaña).
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
    Gestor de Sincronización entre API y Crontab.
    Garantiza exclusividad (solo 1 job) y prioridad por ID más alto.
    """
    def __init__(self, poll_interval_s, python_env=None, cmd=None, logger=None):
        self.poll_interval_ms = poll_interval_s * 1000
        self.python_env = python_env if python_env else "/usr/bin/python3"
        self.cmd = f"{self.python_env} {cmd}"
        self._log = logger if logger else logging.getLogger(__name__)

        # Configuración según entorno
        if cfg.DEVELOPMENT:
            self.debug_file = (cfg.PROJECT_ROOT / "mock_crontab.txt").absolute()
            self.debug_file.parent.mkdir(parents=True, exist_ok=True)
            if not self.debug_file.exists():
                self.debug_file.write_text("", encoding="utf-8")
            # En modo dev, podrías pasar tabfile=str(self.debug_file) a CronTab
            self.cron = CronTab(user=True)
        else:
            self.cron = CronTab(user=True)

    def _ts_to_human(self, ts_ms):
        if ts_ms is None: return "None"
        return cfg.human_readable(ts_ms, target_tz="UTC")

    def _seconds_to_cron_interval(self, seconds):
        minutes = max(int(seconds / 60), 1)
        return f"*/{minutes} * * * *"

    def _clear_all_campaign_jobs(self):
        """Limpia todos los jobs con el prefijo CAMPAIGN_."""
        jobs = list(self.cron.find_comment(re.compile(r'^CAMPAIGN_.*')))
        for job in jobs:
            self.cron.remove(job)
        if jobs:
            self._log.debug(f"🧹 Crontab cleared ({len(jobs)} jobs removed)")

    def _upsert_job(self, camp, store: ShmStore):
        """Actualiza RAM y agenda el job en el sistema operativo."""
        c_id = camp['campaign_id']
        end_ms = camp['timeframe']['end']
        
        # 1. RAM (ShmStore)
        dict_persist_params = {
            "campaign_id": c_id,
            "expires_at_ms": end_ms, # El script de RF lo usará para validarse
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
        store.update_from_dict(dict_persist_params)

        # 2. Cron
        period_s = camp['acquisition_period_s']
        schedule = self._seconds_to_cron_interval(period_s)
        job = self.cron.new(command=self.cmd, comment=f"CAMPAIGN_{c_id}")
        job.setall(schedule)

    def sync_jobs(self, campaigns: list, current_time_ms: int, store: ShmStore) -> bool:
        """
        Sincroniza y retorna True si hay una campaña activa agendada.
        """
        self._log.info("="*60)
        self._log.info(f"🔍 SYNC START | Time: {self._ts_to_human(current_time_ms)}")
        
        candidates = []
        for camp in campaigns:
            c_id = camp['campaign_id']
            status = camp['status']
            start_ms = camp['timeframe']['start']
            end_ms = camp['timeframe']['end']
            
            # Ventana con margen poll_interval_ms en ambos extremos
            window_open = start_ms - self.poll_interval_ms
            window_close = end_ms - self.poll_interval_ms
            
            is_valid = status not in ['canceled', 'error', 'finished']
            is_in_window = window_open <= current_time_ms <= window_close

            if is_valid and is_in_window:
                candidates.append(camp)
            else:
                self._log.debug(f"📋 Skip ID {c_id}: {status} / Outside Window")

        # LIMPIEZA ATÓMICA: Siempre borramos antes de decidir
        self._clear_all_campaign_jobs()

        winner = None
        if candidates:
            # Seleccionamos la de ID más alto
            winner = max(candidates, key=lambda x: x['campaign_id'])
            self._log.info(f"🏆 Winner: ID {winner['campaign_id']} (Ends: {self._ts_to_human(winner['timeframe']['end'])})")
            self._upsert_job(winner, store)
        else:
            self._log.info("ℹ️ No active candidates found.")

        # Escribir cambios al sistema
        self.cron.write()
        self._log.info("="*60)
        
        return winner is not None

class AcquireDual:
    """
    Motor de Adquisición de Datos y Limpieza Espectral.
    
    Esta clase resuelve los dos problemas principales de los SDR de bajo costo:
    1. El 'DC Spike' (pico central).
    2. La caída de amplitud en los extremos del filtro de paso bajo (roll-off).
    """
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
    
    async def raw_acquire(self, rf_params):
        """
        Adquisición con eliminación de artefacto DC.
        
        Calcula la media y desviación estándar del ruido circundante al centro
        e inyecta ruido sintético en el 0.2% central del espectro.
        """
        # 1. Perform a single standard acquisition
        data = await self._single_acquire(rf_params)
        
        try:
            pxx = np.array(data['Pxx'])
            total_bins = len(pxx)
            center_idx = total_bins // 2
            
            # 2. Define the spike width (0.2% of total bandwidth)
            # We ensure at least 1 bin is removed
            spike_width = max(1, int(total_bins * 0.002))
            half_width = spike_width // 2
            
            start_idx = center_idx - half_width
            end_idx = center_idx + half_width
            
            # 3. Define neighbor windows to sample the noise floor
            # We'll take a small sample (same size as the spike) from both sides
            sample_size = max(5, spike_width) 
            
            left_neighbor = pxx[max(0, start_idx - sample_size) : start_idx]
            right_neighbor = pxx[end_idx : min(total_bins, end_idx + sample_size)]
            
            # Concatenate neighbors to find the statistical noise floor
            neighbors = np.concatenate([left_neighbor, right_neighbor])
            noise_mean = np.mean(neighbors)
            noise_std = np.std(neighbors)
            
            # 4. Generate and inject synthetic noise
            # This replaces the spike with values that match the local noise profile
            simulated_noise = np.random.normal(noise_mean, noise_std, size=(end_idx - start_idx))
            
            # Ensure we don't produce negative values if Pxx is in linear power
            # (Optional: skip if you are working exclusively in dB)
            simulated_noise = np.clip(simulated_noise, a_min=np.min(neighbors), a_max=None)
            
            pxx[start_idx:end_idx] = simulated_noise
            
            # 5. Package and return
            data['Pxx'] = pxx.tolist()
            self._log.debug(f"DC Spike removed: {spike_width} bins replaced at center.")
            return data

        except Exception as e:
            self._log.error(f"DC spike removal failed: {e}")
            return data

    async def get_corrected_data(self, rf_params):
        """
        Adquisición mediante técnica de 'Stitching' (Cosido).
        
        Realiza una captura en la frecuencia central y otra con offset. Luego 
        aplica una rampa de corrección lineal y un fundido de coseno para 
        sustituir el centro ruidoso de la primera con datos limpios de la segunda.
        """
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