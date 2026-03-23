from pathlib import Path
import sys
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from scipy.signal import find_peaks, peak_widths
from dataclasses import dataclass, asdict
import time

# Patch notebook jupyter
PROJECT_ROOT = Path.cwd().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
import nest_asyncio
nest_asyncio.apply()

import cfg
log = cfg.set_logger()

from functions import AcquireDual
from utils import ZmqPairController, ServerRealtimeConfig


# ---------------------------------------------------------
# ESTRUCTURAS DE DATOS Y CONFIGURACIÓN
# ---------------------------------------------------------
@dataclass
class DspPayload:
    start_freq_hz: float
    end_freq_hz: float
    Pxx: np.ndarray

    @classmethod
    def from_dict(cls, data: dict):
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})

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
class AnalysisResult:
    freqs: np.ndarray
    psd: np.ndarray
    noise_floor: float
    threshold: float
    mean_power: float
    raw_candidates: list
    validated_candidates: list
    ppm: float
    ppm_std: float


# ---------------------------------------------------------
# CLASE NÚCLEO (LÓGICA DSP Y NEGOCIO)
# ---------------------------------------------------------
class FmCalibrator:
    def __init__(self, config: CalibratorConfig):
        self.cfg = config

    @staticmethod
    def get_legal_freqs(db_path: Path, lat: float, lng: float, radius_m: float) -> list:
        """Carga y filtra espacialmente la base de datos de la ANE."""
        df = pd.read_csv(db_path, low_memory=False)
        df = df[df["servicio"].astype(str).str.strip() == "Radiodifusión Sonora en FM"].dropna(subset=["frecuencia", "latitud_dec", "longitud_dec"])
        
        lat1, lon1 = np.radians(lat), np.radians(lng)
        lat2, lon2 = np.radians(df["latitud_dec"].to_numpy(float)), np.radians(df["longitud_dec"].to_numpy(float))
        a = np.sin((lat2-lat1)/2.0)**2 + np.cos(lat1) * np.cos(lat2) * np.sin((lon2-lon1)/2.0)**2
        dist_m = 6371000.0 * 2 * np.arcsin(np.sqrt(a))
        
        return df[dist_m <= radius_m]["frecuencia"].unique().tolist()

    async def acquire_psd(self, sdr_config: ServerRealtimeConfig) -> DspPayload:
        """Adquiere N iteraciones y retorna el Max Hold estabilizado."""
        captures = []
        controller = ZmqPairController(addr=cfg.IPC_ADDR, is_server=True, verbose=False)
        
        async with controller as zmq_ctrl:
            acquirer = AcquireDual(controller=zmq_ctrl, log=log)
            for i in range(self.cfg.iterations):
                log.info(f"Capturing PSD [[{i + 1}/{self.cfg.iterations}]]")
                raw = await acquirer.get_corrected_data(asdict(sdr_config))
                captures.append(raw['Pxx'])
                
        return DspPayload(start_freq_hz=raw['start_freq_hz'], end_freq_hz=raw['end_freq_hz'], Pxx=np.max(captures, axis=0))

    def process_payload(self, rf: DspPayload, legal_freqs: list) -> AnalysisResult:
        """Pipeline DSP: Enmascarado, Umbrales, Picos y Validación."""
        freqs = np.linspace(rf.start_freq_hz, rf.end_freq_hz, len(rf.Pxx)) / 1e6
        bin_hz = (rf.end_freq_hz - rf.start_freq_hz) / len(rf.Pxx)
        
        # Enmascarar DC
        psd_search = rf.Pxx.copy()
        c_idx = len(psd_search) // 2
        ignore_bins = int(self.cfg.dc_mask_hz / bin_hz)
        psd_search[c_idx - ignore_bins : c_idx + ignore_bins] = -np.inf
        
        # Umbrales
        nf, thres = np.percentile(rf.Pxx, 10), np.mean(rf.Pxx)
        
        # Picos
        min_dist = max(1, int(self.cfg.peak_distance_hz / bin_hz))
        peaks, props = find_peaks(psd_search, height=thres, prominence=self.cfg.prominence, distance=min_dist)
        widths, _, left_ips, right_ips = peak_widths(psd_search, peaks, rel_height=0.75)
        widths_hz = widths * bin_hz
        
        mask = (widths_hz > self.cfg.min_width_hz) & (widths_hz <= self.cfg.max_width_hz)
        scores = (10 ** (props['prominences'][mask] / 10)) * widths_hz[mask]
        
        # Extraer candidatos top filtrados dinámicamente
        valid_idx = np.where(mask)[0][np.argsort(scores)[-self.cfg.max_candidates:]]
        raw_cands = []
        for i in valid_idx:
            l_mhz = freqs[0] + (left_ips[i] * (bin_hz / 1e6))
            r_mhz = freqs[0] + (right_ips[i] * (bin_hz / 1e6))
            raw_cands.append((peaks[i], (l_mhz + r_mhz) / 2.0, l_mhz, r_mhz))
            
        # Validación
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
        
        log.info(f"Análisis Completo - PPM: {ppm:.1f} | Std: {ppm_std:.1f} | Match: {len(validated)}/{len(raw_cands)}")
        
        return AnalysisResult(freqs, rf.Pxx, nf, thres, thres, raw_cands, validated, ppm, ppm_std)

    async def validate_correction(self, base_sdr_cfg: ServerRealtimeConfig, target_ppm: float, legal_freqs: list) -> float:
        """Aplica corrección sugerida y verifica si el error disminuye."""
        log.info(f"Validando corrección de Hardware con ppm_error={-int(round(target_ppm))}...")
        new_cfg = asdict(base_sdr_cfg)
        new_cfg['ppm_error'] = -int(round(target_ppm))
        
        rf = await self.acquire_psd(ServerRealtimeConfig(**new_cfg))
        res = self.process_payload(rf, legal_freqs)
        
        if abs(res.ppm) < abs(target_ppm): log.info("¡Validación exitosa! Error disminuyó.")
        else: log.warning("La corrección SDR NO mejoró el error (Posible inestabilidad local).")
        return res.ppm


# ---------------------------------------------------------
# FLUJO PRINCIPAL Y DIBUJO
# ---------------------------------------------------------
async def main():
    log.info("Starting production FM calibration engine...")
    
    # Configuraciones
    db_path = cfg.PROJECT_ROOT / "db" / "ANE_db_reference.csv"
    lat, lng, coverage_m = 5.0310736, -75.5894066, 15000
    
    sdr_cfg = ServerRealtimeConfig(
        method_psd="welch", center_freq_hz=int(98e6), sample_rate_hz=int(20e6),
        rbw_hz=int(1e3), window="hamming", overlap=float(0.5), lna_gain=int(8),
        vga_gain=int(8), antenna_amp=bool(False), antenna_port=int(1), ppm_error=0,
    )
    
    calibrator = FmCalibrator(CalibratorConfig())
    legal_freqs = calibrator.get_legal_freqs(db_path, lat, lng, coverage_m)
    
    # 1. Adquisición y Análisis Base
    rf_payload = await calibrator.acquire_psd(sdr_cfg)
    res = calibrator.process_payload(rf_payload, legal_freqs)
    
    # 2. Intento de validación
    await calibrator.validate_correction(sdr_cfg, res.ppm, legal_freqs)
    
    # 3. Plotting
    plt.figure(figsize=(20, 8))
    plt.plot(res.freqs, res.psd, color='darkorange', linewidth=1.2, label='PSD Max Hold (Real-Time)')
    plt.axhline(res.noise_floor, color='blue', linestyle='--', alpha=0.5, label=f'Noise Floor ({res.noise_floor:.1f} dBm)')
    plt.axhline(res.threshold, color='green', linestyle='-.', alpha=0.7, label=f'Threshold ({res.threshold:.1f} dBm)')
    
    # Legales Visibles
    for i, f_legal in enumerate([f for f in legal_freqs if res.freqs.min() <= f <= res.freqs.max()]):
        plt.axvline(f_legal, color='cyan', linestyle='--', alpha=0.6, label='Legal FM (DB)' if i == 0 else "")
        plt.text(f_legal, plt.ylim()[0] + 2, f"{f_legal}", color='cyan', rotation=90, fontsize=8, va='bottom')

    # Descartados vs Validados
    all_raw_indices = [item[0] for item in res.raw_candidates]
    val_indices = [item[0] for item in res.validated_candidates]
    discarded = [idx for idx in all_raw_indices if idx not in val_indices]
    
    if all_raw_indices: plt.plot(res.freqs[all_raw_indices], res.psd[all_raw_indices], "kx", markersize=6, label='All Candidates')
    if discarded: plt.plot(res.freqs[discarded], res.psd[discarded], "^", color='orange', markersize=10, markerfacecolor='none', markeredgewidth=1.5, label='Discarded Peaks')
    
    if res.validated_candidates:
        plt.plot(res.freqs[val_indices], res.psd[val_indices], "rs", markersize=12, markerfacecolor='none', markeredgewidth=2, label='Validated Peaks')
        for idx, centroid_mhz, left_mhz, right_mhz, _, error_mhz in res.validated_candidates:
            plt.axvline(centroid_mhz, color='red', linestyle=':', alpha=0.8)
            plt.axvspan(left_mhz, right_mhz, color='yellow', alpha=0.2)
            plt.text(centroid_mhz, res.psd[idx] + 1, f"{centroid_mhz:.3f}\n(E: {error_mhz*1000:.0f} kHz)", color='red', weight='bold', ha='center', fontsize=9)

    plt.xlabel("Frequency (MHz)")
    plt.ylabel("Power Spectral Density (dBm)")
    plt.title(f"FM Calibration Results (PPM Error: {res.ppm:.1f})")
    plt.legend(loc='upper right')
    plt.grid(True, alpha=0.2)
    plt.tight_layout()
    plt.show()

    return 0

if __name__ == "__main__":
    now = time.perf_counter()
    rc = cfg.run_and_capture(main)
    log.info(f"Execution completed in {time.perf_counter() - now:.2f}s with return code: {rc}")