import sys
import time
import json
import numpy as np
import pandas as pd
from pathlib import Path
from dataclasses import dataclass, asdict
from scipy.signal import find_peaks, peak_widths

import cfg
log = cfg.set_logger()

from functions import AcquireDual
from utils import ZmqPairController, ServerRealtimeConfig, ShmStore

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
# ---------------------------------------------------------
# ESTRUCTURAS DE DATOS
# ---------------------------------------------------------
@dataclass
class DspPayload:
    start_freq_hz: float
    end_freq_hz: float
    Pxx: np.ndarray

@dataclass
class CalibratorConfig:
    iterations: int = 8
    prominence: float = 7.0
    min_width_hz: float = 10e3
    max_width_hz: float = 200e3
    sensibility_khz: float = 50.0
    dc_mask_hz: float = 25e3
    peak_distance_hz: float = 150e3
    max_candidates: int = 20

@dataclass
class GradientConfig(CalibratorConfig):
    max_iterations: int = 20
    patience: int = 3
    learning_rate: float = 0.8

@dataclass
class AnalysisResult:
    freqs: np.ndarray
    psd: np.ndarray
    noise_floor: float
    threshold: float
    raw_candidates: list
    validated_candidates: list
    ppm: float
    ppm_std: float

@dataclass
class OptimizationResult:
    best_correction: int
    best_error: float
    history: list
    final_analysis: AnalysisResult

# ---------------------------------------------------------
# NÚCLEO DSP Y CALIBRACIÓN
# ---------------------------------------------------------
class FmCalibrator:
    def __init__(self, config: GradientConfig):
        self.cfg = config

    @staticmethod
    def get_legal_freqs(db_path: Path, lat: float, lng: float, radius_m: float) -> list:
        try:
            df = pd.read_csv(db_path, low_memory=False)
            df = df[df["servicio"].astype(str).str.strip() == "Radiodifusión Sonora en FM"].dropna(subset=["frecuencia", "latitud_dec", "longitud_dec"])
            
            lat1, lon1 = np.radians(lat), np.radians(lng)
            lat2, lon2 = np.radians(df["latitud_dec"].to_numpy(float)), np.radians(df["longitud_dec"].to_numpy(float))
            a = np.sin((lat2-lat1)/2.0)**2 + np.cos(lat1) * np.cos(lat2) * np.sin((lon2-lon1)/2.0)**2
            dist_m = 6371000.0 * 2 * np.arcsin(np.sqrt(a))
            
            freqs = df[dist_m <= radius_m]["frecuencia"].unique().tolist()
            log.info(f"BD cargada. {len(freqs)} frecuencias legales encontradas en {radius_m}m.")
            return freqs
        except Exception as e:
            log.error(f"Error cargando BD ANE: {e}", exc_info=True)
            return []

    async def acquire_psd(self, sdr_config: ServerRealtimeConfig) -> DspPayload:
        captures = []
        try:
            controller = ZmqPairController(addr=cfg.IPC_ADDR, is_server=True, verbose=False)
            async with controller as zmq_ctrl:
                acquirer = AcquireDual(controller=zmq_ctrl, log=log)
                for i in range(self.cfg.iterations):
                    raw = await acquirer.get_corrected_data(asdict(sdr_config))
                    captures.append(raw['Pxx'])
            return DspPayload(start_freq_hz=raw['start_freq_hz'], end_freq_hz=raw['end_freq_hz'], Pxx=np.max(captures, axis=0))
        except Exception as e:
            log.error(f"Error en adquisición ZMQ: {e}", exc_info=True)
            raise

    def process_payload(self, rf: DspPayload, legal_freqs: list) -> AnalysisResult:
        freqs = np.linspace(rf.start_freq_hz, rf.end_freq_hz, len(rf.Pxx)) / 1e6
        bin_hz = (rf.end_freq_hz - rf.start_freq_hz) / len(rf.Pxx)
        
        psd_search = rf.Pxx.copy()
        c_idx = len(psd_search) // 2
        ignore_bins = int(self.cfg.dc_mask_hz / bin_hz)
        psd_search[c_idx - ignore_bins : c_idx + ignore_bins] = -np.inf
        
        nf, thres = np.percentile(rf.Pxx, 10), np.mean(rf.Pxx)
        min_dist = max(1, int(self.cfg.peak_distance_hz / bin_hz))
        
        peaks, props = find_peaks(psd_search, height=thres, prominence=self.cfg.prominence, distance=min_dist)
        widths, _, left_ips, right_ips = peak_widths(psd_search, peaks, rel_height=0.75)
        widths_hz = widths * bin_hz
        
        mask = (widths_hz > self.cfg.min_width_hz) & (widths_hz <= self.cfg.max_width_hz)
        scores = (10 ** (props['prominences'][mask] / 10)) * widths_hz[mask]
        
        valid_idx = np.where(mask)[0][np.argsort(scores)[-self.cfg.max_candidates:]]
        raw_cands = []
        for i in valid_idx:
            l_mhz = freqs[0] + (left_ips[i] * (bin_hz / 1e6))
            r_mhz = freqs[0] + (right_ips[i] * (bin_hz / 1e6))
            raw_cands.append((peaks[i], (l_mhz + r_mhz) / 2.0, l_mhz, r_mhz))
            
        validated, ppm_errors = [], []
        sens_mhz = self.cfg.sensibility_khz / 1000.0
        
        for cand in raw_cands:
            if not legal_freqs: break
            closest = min(legal_freqs, key=lambda x: abs(x - cand[1]))
            err = cand[1] - closest
            if abs(err) <= sens_mhz:
                validated.append((*cand, closest, err))
                ppm_errors.append(((cand[1] - closest) / closest) * 1e6)
                
        ppm = np.median(ppm_errors) if ppm_errors else 0.0
        ppm_std = np.std(ppm_errors) if ppm_errors else 0.0
        return AnalysisResult(freqs, rf.Pxx, nf, thres, raw_cands, validated, ppm, ppm_std)

# ---------------------------------------------------------
# OPTIMIZADOR GRADIENTE
# ---------------------------------------------------------
class FmGradientOptimizer(FmCalibrator):
    async def optimize(self, base_sdr_cfg: ServerRealtimeConfig, legal_freqs: list) -> OptimizationResult:
        log.info(f"Iniciando Descenso de Gradiente (Max {self.cfg.max_iterations} iteraciones)...")
        history = []
        current_correction, best_error, best_correction = 0.0, float('inf'), 0
        patience_counter = 0
        best_analysis, prev_error = None, None

        for i in range(self.cfg.max_iterations):
            applied_correction = int(round(current_correction))
            cfg_dict = asdict(base_sdr_cfg)
            cfg_dict['ppm_error'] = applied_correction
            
            rf = await self.acquire_psd(ServerRealtimeConfig(**cfg_dict))
            res = self.process_payload(rf, legal_freqs)
            error = res.ppm
            
            history.append({'iteration': i+1, 'correction': applied_correction, 'error': error})
            log.info(f"Iter [{i+1:02d}] -> Corr: {applied_correction} PPM | Error medido: {error:+.2f} PPM")

            if abs(error) < 1.0:
                log.info("Convergencia ideal alcanzada (< 1 PPM).")
                best_error, best_correction, best_analysis = abs(error), applied_correction, res
                break

            if abs(error) < best_error:
                best_error, best_correction, best_analysis = abs(error), applied_correction, res
                patience_counter = 0
            else:
                patience_counter += 1

            if patience_counter >= self.cfg.patience:
                log.info(f"Early stopping: Sin mejora en {self.cfg.patience} iteraciones.")
                break

            if prev_error is not None and (error * prev_error < 0):
                self.cfg.learning_rate *= 0.6
                log.debug(f"Overshoot detectado. Nuevo LR: {self.cfg.learning_rate:.2f}")

            current_correction -= self.cfg.learning_rate * error
            prev_error = error

        log.info(f"Optimización finalizada. Mejor Corrección: {best_correction} PPM (Error residual: {best_error:.2f} PPM)")
        return OptimizationResult(best_correction, best_error, history, best_analysis)


def _to_bool_flag(value, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)

    text = str(value).strip().lower()
    if text in {"true", "1", "yes", "y", "on"}:
        return True
    if text in {"false", "0", "no", "n", "off"}:
        return False
    return default


def _normalize_legal_freqs(value) -> list:
    if isinstance(value, list):
        raw = value
    elif isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            parsed = []
        raw = parsed if isinstance(parsed, list) else []
    else:
        raw = []

    normalized = []
    for item in raw:
        try:
            normalized.append(float(item))
        except (TypeError, ValueError):
            continue
    return normalized


def _is_valid_gps(lat: float, lng: float) -> bool:
    if not np.isfinite(lat) or not np.isfinite(lng):
        return False
    if not (-90.0 <= lat <= 90.0):
        return False
    if not (-180.0 <= lng <= 180.0):
        return False
    if abs(lat) < 1e-9 or abs(lng) < 1e-9:
        return False
    return True

# ---------------------------------------------------------
# PUNTO DE ENTRADA
# ---------------------------------------------------------
async def main():
    try:
        log.info("Iniciando calibración de hardware...")
        db_path = cfg.PROJECT_ROOT / "db" / "ANE_db_reference.csv"
        optimizer = FmGradientOptimizer(GradientConfig())
        
        shm = ShmStore()
        
        # Parámetros por defecto para despliegue
        if cfg.DEVELOPMENT:
            lat, lng, coverage_m = 5.0310736, -75.5894066, 15000
            if not _is_valid_gps(lat, lng):
                log.error("GPS inválido (nulo/fuera de rango/cero). No se puede calibrar.")
                return 1
            legal_freqs = optimizer.get_legal_freqs(db_path, lat, lng, coverage_m)
        else:
            
            coverage_m = 15000
            changed_gps = _to_bool_flag(shm.consult_persistent("changed_gps"), default=True)
            cached_legal_freqs = _normalize_legal_freqs(shm.consult_persistent("legal_freqs"))

            try:
                last_lat = shm.consult_persistent("last_lat")
                last_lng = shm.consult_persistent("last_lng")
                if last_lat is None or last_lng is None:
                    raise ValueError("GPS no disponible en shm")

                lat = float(last_lat)
                lng = float(last_lng)
            except (TypeError, ValueError):
                log.error("GPS inválido (nulo/no numérico). No se puede calibrar.")
                return 1

            if not _is_valid_gps(lat, lng):
                log.error("GPS inválido (fuera de rango/cero). No se puede calibrar.")
                return 1

            if changed_gps or not cached_legal_freqs:
                legal_freqs = optimizer.get_legal_freqs(db_path, lat, lng, coverage_m)
                shm.update_from_dict({"changed_gps": False, "legal_freqs": legal_freqs})
            else:
                legal_freqs = cached_legal_freqs
                log.info(f"Usando {len(legal_freqs)} frecuencias legales cacheadas en shm.")

        
        sdr_cfg = ServerRealtimeConfig(
            method_psd="welch", center_freq_hz=int(98e6), sample_rate_hz=int(20e6),
            rbw_hz=int(1e3), window="hamming", overlap=float(0.5), lna_gain=int(8),
            vga_gain=int(8), antenna_amp=bool(False), antenna_port=int(1), ppm_error=0,
        )
        
        if not legal_freqs:
            log.error("No se encontraron frecuencias legales. Abortando optimización.")
            return 1
            
        opt_res = await optimizer.optimize(sdr_cfg, legal_freqs)
        
        # Guardar en base de datos o retornar al sistema superior aquí
        log.info(f"Pipeline exitoso. Valor a persistir en BD/Config: {opt_res.best_correction} PPM.")
        shm.add_to_persistent("last_kal_ms", cfg.get_time_ms())
        shm.add_to_persistent("ppm_error", opt_res.best_correction)
        return 0

    except Exception as e:
        log.error("Fallo crítico en el pipeline de calibración principal.", exc_info=True)
        return 1

if __name__ == "__main__":
    t0 = time.perf_counter()
    rc = cfg.run_and_capture(main)
    log.info(f"Proceso de calibración finalizado en {time.perf_counter() - t0:.2f}s con código: {rc}")