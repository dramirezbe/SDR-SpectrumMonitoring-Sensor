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
import shlex
import os
import numpy as np
import re
import asyncio
from copy import deepcopy
import copy
from utils.dc_spike_removal import DCSpikeRemovalPipeline

def _parse_exec_env(exec_str: str):
    """
    Convierte una cadena de ejecución en argv + entorno.

    Soporta valores como:
      "/opt/venv/bin/python3"
      "PYTHONUNBUFFERED=1 /opt/venv/bin/python3"
    """
    tokens = shlex.split(exec_str)
    if not tokens:
        raise ValueError("python_env is empty")

    env = os.environ.copy()
    argv = []

    for tok in tokens:
        if "=" in tok and not argv:
            key, value = tok.split("=", 1)
            if key and all(ch not in key for ch in " /\\"):
                env[key] = value
                continue
        argv.append(tok)

    if not argv:
        raise ValueError(f"Could not parse executable from: {exec_str!r}")

    return argv, env


def _build_python_cmd(exec_str: str, script_name: str):
    base_argv, env = _parse_exec_env(exec_str)
    return [*base_argv, "-u", script_name], env

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
def format_data_for_upload(payload, log: logging.Logger) -> dict:
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

    # --- Impresión formateada del payload ---
    log.debug("\n--- Payload ready to post ---")
    for key, value in post_dict.items():
        if key == "Pxx":
            pxx_list = list(value)
            # Trunca a 5 elementos para la consola
            pxx_preview = pxx_list[:5] + ["..."] if len(pxx_list) > 5 else pxx_list
            log.debug(f"{key}: {pxx_preview}")
        else:
            # Imprime el resto de las claves normalmente (mac, frecuencias, etc.)
            log.debug(f"{key}: {value}")
    print("---------------------------------\n")
    # ----------------------------------------

    return post_dict

class CronSchedulerCampaign:
    """
    Gestor de Sincronización entre API y Crontab.
    Garantiza exclusividad (solo 1 job) y prioridad por ID más alto.
    """
    def __init__(self, poll_interval_s, python_env=None, cmd=None, logger=None):
        self.poll_interval_ms = poll_interval_s * 1000
        self.python_env = python_env if python_env else "/usr/bin/python3"
        if cmd is None:
            raise ValueError("campaign runner script path is required")
        campaign_argv, _ = _build_python_cmd(self.python_env, cmd)
        self.cmd = f"systemd-cat -t CAMPAIGN_RUNNER {shlex.join(campaign_argv)}"
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
            "cooldown_request": float(camp.get('cooldown_request', 1.0)),
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
        self.BASELINE_WINDOW = 41
        self.THRESHOLD_SCALE = 3.0
        self.MAX_SEARCH_BINS = 25
        self.EXPANSION_FACTOR = 1.5
        self.MIN_HALF_WIDTH = 2
        self.MAX_HALF_WIDTH = 20
        self.SUPPORT_BINS = 10
        self.POLY_DEGREE = 2
        

    def _apply_dc_correction_to_acquisition(self, acquisition_result):
        """
        Aplica corrección DC usando el nuevo pipeline adaptativo.
        Mantiene el mismo formato del dict de salida.
        """
        if not isinstance(acquisition_result, dict):
            raise TypeError("Se esperaba que _single_acquire devolviera un dict.")
        
        if "Pxx" not in acquisition_result:
            raise KeyError("No se encontró la llave 'Pxx' en acquisition_result.")
        
        # Copia profunda para no modificar original
        out = copy.deepcopy(acquisition_result)
        pxx = np.asarray(out["Pxx"], dtype=float)
        
        # Determinar noise_std_db basado en tamaño de FFT
        noise_std_db = self._get_noise_std_db(len(pxx))
        
        # Configurar parámetros para la detección de baja ocupación
        # Estos valores pueden ajustarse según necesidades específicas
        low_content_params = {
            "enable_low_content_expansion": True,
            "low_content_center_fraction": 0.10,
            "low_content_exclusion_multiplier": 2.5,
            "low_content_expand_factor": 3.0,
            "low_content_mean_median_max_diff_db": 0.11,
            "low_content_high_tail_sigma_factor": 2.5,
            "low_content_max_high_tail_fraction": 0.025
        }
        
        # Aplicar el nuevo pipeline de remoción de DC spike
        try:
            pxx_filtered, center_idx, repair_slice, debug_info = (
                DCSpikeRemovalPipeline.remove_dc_spike_adaptive_symmetric(
                    power_dbm=pxx,
                    analysis_fraction=0.05,  # Fracción centrada para análisis
                    smooth_window=9,          # Suavizado inicial
                    slope_smooth_window=7,    # Suavizado para pendientes
                    support_bins=14,          # Bins laterales para reconstrucción
                    poly_degree=2,            # Grado del polinomio
                    min_half_width=2,         # Semi-ancho mínimo
                    debug=False,              # Deshabilitar debug en producción
                    noise_std_db=noise_std_db,
                    **low_content_params
                )
            )
            
            # Determinar si realmente hubo cambio
            correction_changed = not np.allclose(pxx_filtered, pxx, rtol=0.0, atol=1e-12)
            
            # Clasificar el modo de ocupación basado en low_content_info
            occupancy_mode = self._classify_occupancy_from_debug_info(debug_info)
            
            # Extraer métricas de ocupación para diagnóstico
            occupancy_metrics = self._extract_occupancy_metrics(debug_info, pxx)
            
        except Exception as e:
            # Si falla la corrección, loguear y retornar datos originales
            self._log.error(f"DC spike removal failed: {e}", exc_info=True)
            return out
        
        # Actualizar resultado manteniendo formato
        out["Pxx_raw"] = out["Pxx"]
        out["Pxx"] = pxx_filtered.tolist()
        
        # Añadir metadatos de corrección
        out["dc_correction"] = self._build_correction_metadata(
            correction_changed=correction_changed,
            occupancy_mode=occupancy_mode,
            center_idx=center_idx,
            repair_slice=repair_slice,
            debug_info=debug_info,
            params_used={
                "analysis_fraction": 0.05,
                "smooth_window": 9,
                "slope_smooth_window": 7,
                "support_bins": 14,
                "poly_degree": 2,
                "min_half_width": 2,
                "noise_std_db": noise_std_db,
                **low_content_params
            },
            out=out
        )
        
        return out

    def _get_noise_std_db(self, pxx_length):
        """
        Determina la desviación estándar del ruido según el tamaño de FFT.
        """
            if pxx_length > 16385:
                return 0.63
            elif pxx_length > 4096:
                return 0.52
            elif pxx_length > 1024:
                return 0.25
            elif pxx_length > 512:
                return 0.11
            else:
                return 0.08

    def _classify_occupancy_from_debug_info(self, debug_info):
        """
        Clasifica el régimen espectral basado en la información de debug.
        """
        low_content_info = debug_info.get("low_content_info", {})
        
        # Verificar si se detectó baja ocupación
        if low_content_info and isinstance(low_content_info, dict):
            reason = low_content_info.get("reason", "")
            if reason and "low spectral content" in reason:
                return "low_occupancy"
        
        # Verificar si se aplicó expansión por baja ocupación
        if debug_info.get("low_content_expansion_applied", False):
            return "low_occupancy"
        
        # Por defecto, considerar como ocupado
        return "occupied"

    def _extract_occupancy_metrics(self, debug_info, pxx):
        """
        Extrae métricas de ocupación para diagnóstico.
        """
        low_content_info = debug_info.get("low_content_info", {})
        detect_info = debug_info.get("detect_info", {})
        
        metrics = {
            "original_detected_half_width": debug_info.get("original_detected_half_width", 0),
            "final_half_width": debug_info.get("final_half_width", 0),
            "low_content_expansion_applied": debug_info.get("low_content_expansion_applied", False),
            "termination_mode": debug_info.get("termination_mode", "unknown"),
            "reconstruction_mode": debug_info.get("reconstruction_mode", "unknown"),
            "detection_reason": detect_info.get("reason", "unknown")
        }
        
        # Agregar métricas de baja ocupación si existen
        if low_content_info:
            metrics.update({
                "low_content_reason": low_content_info.get("reason", "N/A"),
                "mean_minus_median": low_content_info.get("mean_minus_median", None),
                "high_tail_fraction": low_content_info.get("high_tail_fraction", None)
            })
        
        return metrics

    def _build_correction_metadata(self, correction_changed, occupancy_mode, 
                                center_idx, repair_slice, debug_info, 
                                params_used, out):
        """
        Construye el diccionario de metadatos de corrección.
        """
        # Extraer información relevante del debug_info
        detect_info = debug_info.get("detect_info", {})
        low_content_info = debug_info.get("low_content_info", {})
        
        metadata = {
            "applied": bool(correction_changed),
            "mode": occupancy_mode,
            "center_idx": int(center_idx),
            "repair_slice": (int(repair_slice[0]), int(repair_slice[1])),
            "start_freq_hz": int(out.get("start_freq_hz", 0)),
            "end_freq_hz": int(out.get("end_freq_hz", 0)),
            "params_used": params_used,
            
            # Métricas detalladas del nuevo pipeline
            "detection_metrics": {
                "original_half_width": debug_info.get("original_detected_half_width", 0),
                "final_half_width": debug_info.get("final_half_width", 0),
                "termination_mode": debug_info.get("termination_mode", "unknown"),
                "detection_reason": detect_info.get("reason", "unknown"),
                "peak_value_db": detect_info.get("peak_value_db", None),
                "peak_snr_db": detect_info.get("peak_snr_db", None)
            },
            
            "reconstruction_metrics": {
                "reconstruction_mode": debug_info.get("reconstruction_mode", "unknown"),
                "support_bins_used": params_used.get("support_bins", 14),
                "poly_degree_used": params_used.get("poly_degree", 2)
            },
            
            "low_content_metrics": {
                "expansion_applied": debug_info.get("low_content_expansion_applied", False),
                "expansion_factor": debug_info.get("low_content_expand_factor", 1.0),
                "reason": low_content_info.get("reason", "N/A") if low_content_info else "N/A"
            }
        }
        
        # Agregar métricas de baja ocupación si existen
        if low_content_info:
            metadata["low_content_metrics"].update({
                "mean_minus_median": low_content_info.get("mean_minus_median", None),
                "high_tail_fraction": low_content_info.get("high_tail_fraction", None)
            })
        
        return metadata


    async def _single_acquire(self, rf_params):
        """Low-level acquisition with PLL cooling time."""
        rf_params = dict(rf_params)
        if rf_params.get("cooldown_request") is None:
            rf_params["cooldown_request"] = 1.0
        else:
            rf_params["cooldown_request"] = float(rf_params["cooldown_request"])
        await self.controller.send_command(rf_params)
        self._log.debug(f"Acquiring CF: {rf_params['center_freq_hz']/1e6} MHz")
        data = await asyncio.wait_for(self.controller.wait_for_data(), timeout=10)
        # PLL/Hardware settle time
        await asyncio.sleep(0.05) 
        return data
    

    async def just_acquire(self, rf_params):
        """
        Adquisición pura. Retorna los datos crudos del SDR 
        sin aplicar ninguna corrección espectral.
        """
        acquisition_result = await self._single_acquire(rf_params)
        return acquisition_result
    
    async def get_corrected_data(self, rf_params):
        """
        Adquisición con corrección adaptativa de DC spike.
        Devuelve el mismo formato que _single_acquire, pero con Pxx corregido.
        """
        data1 = await self._single_acquire(rf_params)
        try:
            data1 = self._apply_dc_correction_to_acquisition(data1)
            return data1

        except Exception as e:
            self._log.error(f"Spectral correction failed: {e}")
            return data1
