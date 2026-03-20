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
import copy

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
        self.cmd = f"{self.python_env} {cmd} 2>&1 | systemd-cat -t CAMPAIGN_RUNNER"
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
        self.BASELINE_WINDOW = 41
        self.THRESHOLD_SCALE = 3.0
        self.MAX_SEARCH_BINS = 25
        self.EXPANSION_FACTOR = 1.5
        self.MIN_HALF_WIDTH = 2
        self.MAX_HALF_WIDTH = 20
        self.SUPPORT_BINS = 10
        self.POLY_DEGREE = 2

    def _classify_spectral_occupancy(
        self,
        power_dbm: np.ndarray,
        contrast_threshold_db: float = 6.0,
        occupancy_fraction_threshold: float = 0.08,
        peak_margin_db: float = 8.0
    ):
        """
        Clasifica la PSD en dos regímenes:
        - 'occupied': hay emisiones visibles / estructura espectral
        - 'low_occupancy': espectro casi vacío o dominado por ruido
    
        La decisión se toma con métricas robustas basadas en percentiles.
    
        Parámetros
        ----------
        power_dbm : np.ndarray
            PSD en dBm.
        contrast_threshold_db : float
            Umbral mínimo de contraste robusto para considerar que hay ocupación.
        occupancy_fraction_threshold : float
            Fracción mínima de bins significativamente por encima del piso de ruido.
        peak_margin_db : float
            Margen sobre la mediana para contar bins "ocupados".
    
        Retorna
        -------
        mode : str
            'occupied' o 'low_occupancy'
        metrics : dict
            Métricas diagnósticas calculadas.
        """
        x = np.asarray(power_dbm, dtype=float)
        N = len(x)
    
        if N < 16:
            return "low_occupancy", {
                "p10_dbm": float(np.median(x)) if N > 0 else 0.0,
                "p50_dbm": float(np.median(x)) if N > 0 else 0.0,
                "p90_dbm": float(np.median(x)) if N > 0 else 0.0,
                "contrast_db": 0.0,
                "occupancy_fraction": 0.0,
                "decision": "low_occupancy_small_array"
            }
    
        # Percentiles robustos
        p10 = np.percentile(x, 10)
        p50 = np.percentile(x, 50)
        p90 = np.percentile(x, 90)
        p95 = np.percentile(x, 95)
    
        # Contraste robusto del espectro
        contrast_db = p95 - p50
    
        # Estimación de ocupación:
        # bins que están claramente por encima del piso central del espectro
        occupied_mask = x > (p50 + peak_margin_db)
        occupancy_fraction = np.mean(occupied_mask)
    
        # Decisión:
        # si hay suficiente contraste o suficiente fracción ocupada, asumimos emisiones
        if (contrast_db >= contrast_threshold_db) or (occupancy_fraction >= occupancy_fraction_threshold):
            mode = "occupied"
            decision = "occupied_by_contrast_or_fraction"
        else:
            mode = "low_occupancy"
            decision = "low_occupancy_flat_spectrum"
    
        metrics = {
            "p10_dbm": float(p10),
            "p50_dbm": float(p50),
            "p90_dbm": float(p90),
            "p95_dbm": float(p95),
            "contrast_db": float(contrast_db),
            "occupancy_fraction": float(occupancy_fraction),
            "decision": decision
        }
    
        return mode, metrics

    def _moving_average_edge(self, x: np.ndarray, window: int) -> np.ndarray:
        """
        Media móvil con padding en bordes para mantener el mismo tamaño.
        """
        x = np.asarray(x, dtype=float)

        if window < 3:
            return x.copy()

        if window % 2 == 0:
            window += 1

        pad = window // 2
        xpad = np.pad(x, pad_width=pad, mode="edge")
        kernel = np.ones(window, dtype=float) / window
        y = np.convolve(xpad, kernel, mode="same")
        return y[pad:-pad]
    
    def _robust_mad(self, x: np.ndarray) -> float:
        """
        Estimador robusto de dispersión basado en MAD.
        """
        x = np.asarray(x, dtype=float)
        med = np.median(x)
        mad = np.median(np.abs(x - med))
        return 1.4826 * mad
    
    def _remove_dc_spike_adaptive(
        self,
        power_dbm: np.ndarray,
        baseline_window: int,
        threshold_scale: float,
        max_search_bins: int,
        expansion_factor: float,
        min_half_width: int,
        max_half_width: int,
        support_bins: int,
        poly_degree: int,
        noise_std_db: float
    ):
        """
        Corrige el DC spike en el centro de la PSD usando:
        1) detección adaptativa del ancho del artefacto
        2) expansión moderada de la región detectada
        3) reconstrucción suave por ajuste polinómico local

        Retorna
        -------
        x_filtered : np.ndarray
            PSD corregida.
        center_idx : int
            Índice del bin central.
        repair_slice : tuple[int, int]
            (i0, i1) región corregida.
        baseline : np.ndarray
            Fondo local estimado.
        residual : np.ndarray
            Residuo respecto al fondo local.
        """
        x = np.asarray(power_dbm, dtype=float).copy()
        N = len(x)
        center_idx = N // 2

        # Verificación de tamaño mínimo del espectro
        if self._check_spectrum_size(N, support_bins, max_half_width):
            return x.copy(), center_idx, (center_idx, center_idx), x.copy(), np.zeros_like(x)

        # 1) Fondo local y residual
        baseline = self._moving_average_edge(x, baseline_window)
        residual = x - baseline

        # 2) Detección adaptativa del ancho del artefacto
        detected_half_width = self._detect_artifact_width(
            residual, center_idx, max_search_bins, min_half_width,
            threshold_scale
        )

        # 3) Expansión de la región detectada
        expanded_half_width = self._expand_detected_region(
            detected_half_width, expansion_factor, min_half_width, max_half_width
        )

        # 4) Definición de regiones de soporte y reparación
        i0, i1, left_support, right_support = self._define_support_regions(
            center_idx, expanded_half_width, support_bins, N
        )

        # Verificar si hay soporte suficiente
        if left_support[0] < 0 or right_support[1] >= N:
            return x.copy(), center_idx, (i0, i1), baseline, residual

        # 5) Reconstrucción polinómica
        x_filtered = self._polynomial_reconstruction(
            x, center_idx, i0, i1, left_support, right_support, poly_degree,noise_std_db=noise_std_db
        )

        return x_filtered, center_idx, (i0, i1), baseline, residual


    def _check_spectrum_size(self, N: int, support_bins: int, max_half_width: int) -> bool:
        """Verifica si el espectro tiene tamaño suficiente para la corrección."""
        return N < 2 * support_bins + 2 * max_half_width + 5


    def _detect_artifact_width(
        self,
        residual: np.ndarray,
        center_idx: int,
        max_search_bins: int,
        min_half_width: int,
        threshold_scale: float
    ) -> int:
        """
        Detecta el ancho del artefacto basado en el umbral adaptativo.
        
        Parameters
        ----------
        residual : np.ndarray
            Residuo respecto al fondo local.
        center_idx : int
            Índice del bin central.
        max_search_bins : int
            Máxima distancia de búsqueda desde el centro.
        min_half_width : int
            Ancho mínimo a considerar.
        threshold_scale : float
            Escala del umbral (multiplica la desviación robusta).
        
        Returns
        -------
        detected_half_width : int
            Semi-ancho detectado del artefacto.
        """
        N = len(residual)
        
        # Región local para calcular umbral
        local_left = max(0, center_idx - max_search_bins)
        local_right = min(N, center_idx + max_search_bins + 1)
        residual_local = residual[local_left:local_right]
        
        # Cálculo del umbral robusto
        sigma_r = self._robust_mad(residual_local)
        sigma_r = max(sigma_r, 0.15)
        threshold = threshold_scale * sigma_r
        
        # Detección del ancho
        center_amp = abs(residual[center_idx])
        
        if center_amp < threshold:
            detected_half_width = min_half_width
        else:
            # Expandir hacia la izquierda
            left = center_idx
            while left > max(0, center_idx - max_search_bins):
                if abs(residual[left - 1]) >= threshold:
                    left -= 1
                else:
                    break
            
            # Expandir hacia la derecha
            right = center_idx
            while right < min(N - 1, center_idx + max_search_bins):
                if abs(residual[right + 1]) >= threshold:
                    right += 1
                else:
                    break
            
            detected_half_width = max(center_idx - left, right - center_idx)
            detected_half_width = max(detected_half_width, min_half_width)
        
        return detected_half_width


    def _expand_detected_region(
        self,
        detected_half_width: int,
        expansion_factor: float,
        min_half_width: int,
        max_half_width: int
    ) -> int:
        """
        Expande la región detectada según el factor de expansión.
        
        Returns
        -------
        expanded_half_width : int
            Semi-ancho expandido de la región a corregir.
        """
        expanded_half_width = int(np.ceil(expansion_factor * detected_half_width))
        expanded_half_width = max(expanded_half_width, min_half_width)
        expanded_half_width = min(expanded_half_width, max_half_width)
        
        return expanded_half_width


    def _define_support_regions(
        self,
        center_idx: int,
        half_width: int,
        support_bins: int,
        N: int
    ) -> tuple:
        """
        Define las regiones de soporte y la región a reparar.
        
        Returns
        -------
        i0, i1 : int, int
            Índices de inicio y fin de la región a reparar.
        left_support : tuple[int, int]
            (inicio, fin) de la región de soporte izquierda.
        right_support : tuple[int, int]
            (inicio, fin) de la región de soporte derecha.
        """
        i0 = center_idx - half_width
        i1 = center_idx + half_width
        
        left_support_start = i0 - support_bins
        left_support_end = i0 - 1
        left_support = (left_support_start, left_support_end)
        
        right_support_start = i1 + 1
        right_support_end = i1 + support_bins
        right_support = (right_support_start, right_support_end)
        
        return i0, i1, left_support, right_support


    def generate_reconstruction_noise(self, n_samples, noise_std_db=None, rng=None):
        """
        Genera ruido aditivo para la reconstrucción en unidades de dB/dBm.
        """
        if noise_std_db is None or noise_std_db <= 0.0 or n_samples <= 0:
            return np.zeros(int(n_samples), dtype=float)

        noise_std_db = float(noise_std_db)

        if rng is None:
            rng = np.random.default_rng()

        return rng.normal(loc=0.0, scale=noise_std_db, size=int(n_samples))


    def _polynomial_reconstruction(
        self,
        x: np.ndarray,
        center_idx: int,
        i0: int,
        i1: int,
        left_support: tuple,
        right_support: tuple,
        poly_degree: int,
        noise_std_db: float,
        rng: np.random.Generator = None
    ) -> np.ndarray:
        """
        Reconstruye la región del artefacto usando ajuste polinómico
        basado en los soportes laterales y añade ruido opcional.
        
        Parameters
        ----------
        x : np.ndarray
            Señal original.
        center_idx : int
            Índice del bin central.
        i0, i1 : int
            Índices de inicio y fin de la región a reparar.
        left_support : tuple
            (inicio, fin) de la región de soporte izquierda.
        right_support : tuple
            (inicio, fin) de la región de soporte derecha.
        poly_degree : int
            Grado máximo del polinomio.
        noise_std_db : float, optional
            Desviación estándar del ruido aditivo en dB.
        rng : np.random.Generator, optional
            Generador aleatorio para reproducibilidad.
        
        Returns
        -------
        x_filtered : np.ndarray
            Señal con la región del artefacto corregida.
        """
        # Índices de soporte
        left_idx = np.arange(left_support[0], left_support[1] + 1)
        right_idx = np.arange(right_support[0], right_support[1] + 1)
        support_idx = np.concatenate([left_idx, right_idx])
        
        support_vals = x[support_idx]
        
        # Recentrar eje para estabilidad numérica (usando coordenadas absolutas)
        k_support = support_idx.astype(float)
        k_repair = np.arange(i0, i1 + 1, dtype=float)
        
        # Ajuste polinómico
        degree = min(poly_degree, len(k_support) - 1)
        degree = max(degree, 1)  # Asegurar al menos grado 1 si hay suficientes puntos
        
        coeffs = np.polyfit(k_support, support_vals, deg=degree)
        poly = np.poly1d(coeffs)
        
        # Reconstrucción
        reconstructed = poly(k_repair)
        
        # Añadir ruido si se especifica
        if noise_std_db is not None and noise_std_db > 0:
            noise = self.generate_reconstruction_noise(
                n_samples=len(reconstructed),
                noise_std_db=noise_std_db,
                rng=rng
            )
            reconstructed = reconstructed + noise
        
        # Aplicar corrección
        x_filtered = x.copy()
        x_filtered[i0:i1 + 1] = reconstructed
        
        return x_filtered
        

    def _apply_dc_correction_to_acquisition(self, acquisition_result):
        """
        Aplica corrección DC conservando el mismo formato del dict de salida.
        Se asume que la PSD está en acquisition_result['Pxx'].
    
        Mejora:
        -------
        Se clasifica la PSD en dos casos:
        1) 'occupied'       -> espectro con emisiones visibles
        2) 'low_occupancy'  -> espectro casi vacío / dominado por ruido
    
        Según el caso, se ajustan los parámetros del removedor de DC spike.
        """
        if not isinstance(acquisition_result, dict):
            raise TypeError("Se esperaba que _single_acquire devolviera un dict.")
    
        if "Pxx" not in acquisition_result:
            raise KeyError("No se encontró la llave 'Pxx' en acquisition_result.")
    
        out = copy.deepcopy(acquisition_result)
    
        pxx = np.asarray(out["Pxx"], dtype=float)

        if len(pxx) > 16385:
            NOISE_STD_DB = 0.45
        elif len(pxx) > 4096:
            NOISE_STD_DB = 0.20
        elif len(pxx) > 1024:
            NOISE_STD_DB = 0.12
        else:
            NOISE_STD_DB = 0.06
    
        # ---------------------------------------------------------
        # 0) Clasificar el régimen espectral
        # ---------------------------------------------------------
        occupancy_mode, occupancy_metrics = self._classify_spectral_occupancy(
            pxx,
            contrast_threshold_db=6.0,
            occupancy_fraction_threshold=0.08,
            peak_margin_db=8.0
        )
    
        # ---------------------------------------------------------
        # 1) Selección adaptativa de parámetros según el régimen
        # ---------------------------------------------------------
        if occupancy_mode == "occupied":
            # Modo normal / conservador:
            # protege mejor posibles emisiones cercanas al centro
            baseline_window = self.BASELINE_WINDOW
            threshold_scale = self.THRESHOLD_SCALE
            max_search_bins = self.MAX_SEARCH_BINS
            expansion_factor = self.EXPANSION_FACTOR
            min_half_width = self.MIN_HALF_WIDTH
            max_half_width = self.MAX_HALF_WIDTH
            support_bins = self.SUPPORT_BINS
            poly_degree = self.POLY_DEGREE
    
        else:  # low_occupancy
            # Modo agresivo:
            # al haber poco contenido real, conviene ampliar la reparación
            baseline_window = max(self.BASELINE_WINDOW, 51)
            threshold_scale = max(2.0, self.THRESHOLD_SCALE - 0.7)
            max_search_bins = max(self.MAX_SEARCH_BINS, 35)
            expansion_factor = max(2.0, self.EXPANSION_FACTOR)
            min_half_width = max(4, self.MIN_HALF_WIDTH)
            max_half_width = max(28, self.MAX_HALF_WIDTH)
            support_bins = max(12, self.SUPPORT_BINS)
            poly_degree = self.POLY_DEGREE
    
        # ---------------------------------------------------------
        # 2) Aplicar remoción adaptativa de DC
        # ---------------------------------------------------------
        pxx_filtered, center_idx, repair_slice, baseline, residual = \
            self._remove_dc_spike_adaptive(
                power_dbm=pxx,
                baseline_window=baseline_window,
                threshold_scale=threshold_scale,
                max_search_bins=max_search_bins,
                expansion_factor=expansion_factor,
                min_half_width=min_half_width,
                max_half_width=max_half_width,
                support_bins=support_bins,
                poly_degree=poly_degree,
                noise_std_db= NOISE_STD_DB
            )
    
        # ---------------------------------------------------------
        # 3) Determinar si realmente hubo cambio
        # ---------------------------------------------------------
        correction_changed = not np.allclose(pxx_filtered, pxx, rtol=0.0, atol=1e-12)
    
        # ---------------------------------------------------------
        # 4) Mantener mismo formato
        # ---------------------------------------------------------
        out["Pxx_raw"] = out["Pxx"]
        out["Pxx"] = pxx_filtered.tolist()
    
        # ---------------------------------------------------------
        # 5) Diagnóstico
        # ---------------------------------------------------------
        out["dc_correction"] = {
            "applied": bool(correction_changed),
            "mode": occupancy_mode,
            "center_idx": int(center_idx),
            "repair_slice": (int(repair_slice[0]), int(repair_slice[1])),
            "start_freq_hz": int(out.get("start_freq_hz", 0)),
            "end_freq_hz": int(out.get("end_freq_hz", 0)),
            "occupancy_metrics": occupancy_metrics,
            "params_used": {
                "baseline_window": int(baseline_window),
                "threshold_scale": float(threshold_scale),
                "max_search_bins": int(max_search_bins),
                "expansion_factor": float(expansion_factor),
                "min_half_width": int(min_half_width),
                "max_half_width": int(max_half_width),
                "support_bins": int(support_bins),
                "poly_degree": int(poly_degree),
            }
        }
    
        return out


    async def _single_acquire(self, rf_params):
        """Low-level acquisition with PLL cooling time."""
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
