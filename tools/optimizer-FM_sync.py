from pathlib import Path
import sys
import numpy as np
import pandas as pd
from scipy.signal import find_peaks, peak_widths
from dataclasses import dataclass, asdict
import time
import itertools
import pyqtgraph as pg
from PyQt5 import QtWidgets
import psutil
import os
import csv
import asyncio

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
# WATCHDOG DE SISTEMA
# ---------------------------------------------------------
async def system_watchdog(limit_pct=98.0): # <-- Cambiar a 98.0 aquí
    """Monitorea recursos y mata el proceso si se llenan."""
    while True:
        ram = psutil.virtual_memory().percent
        swap = psutil.swap_memory().percent
        disk = psutil.disk_usage('/').percent
        
        if max(ram, swap, disk) >= limit_pct:
            log.error(f"¡RECURSOS CRÍTICOS! RAM:{ram}% SWAP:{swap}% DISCO:{disk}%. ABORTANDO.")
            os._exit(1)
            
        await asyncio.sleep(2)

# ---------------------------------------------------------
# CLASES Y ADQUISICIÓN DE DATOS (Sin cambios)
# ---------------------------------------------------------
@dataclass
class DspPayload:
    start_freq_hz: float
    end_freq_hz: float
    Pxx: np.ndarray

    @classmethod
    def from_dict(cls, data: dict):
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})

async def get_rf_payload(config: ServerRealtimeConfig) -> DspPayload:
    controller = ZmqPairController(addr=cfg.IPC_ADDR, is_server=True, verbose=False)
    async with controller as zmq_ctrl:
        acquirer = AcquireDual(controller=zmq_ctrl, log=log)
        raw_data = await acquirer.get_corrected_data(asdict(config))
        return DspPayload.from_dict(raw_data)

async def get_representative_psd(config: ServerRealtimeConfig, iterations: int = 10) -> DspPayload:
    psd_captures = []
    for i in range(iterations):
        latest_payload = await get_rf_payload(config)
        psd_captures.append(latest_payload.Pxx)
    psd_matrix = np.array(psd_captures)
    psd_max_hold = np.max(psd_matrix, axis=0)
    return DspPayload(start_freq_hz=latest_payload.start_freq_hz, end_freq_hz=latest_payload.end_freq_hz, Pxx=psd_max_hold)

# ---------------------------------------------------------
# FUNCIONES DE PROCESAMIENTO Y DB (Sin cambios)
# ---------------------------------------------------------
def mask_dc(psd_array: np.ndarray, bin_size_hz: float) -> np.ndarray:
    psd_search = psd_array.copy()
    center_idx = len(psd_search) // 2
    ignore_bins = int(25e3 / bin_size_hz)
    psd_search[center_idx - ignore_bins : center_idx + ignore_bins] = -np.inf
    return psd_search

def floor_and_thres(psd_array: np.ndarray) -> tuple:
    return np.percentile(psd_array, 10), np.mean(psd_array), np.mean(psd_array)

def find_candidates_FM(psd_search, psd_real, freq, bin_size_hz, threshold, number_candidates=20, prominence_val=4.0, min_width_hz=25e3, max_width_hz=275e3):
    min_dist_bins = max(1, int(150e3 / bin_size_hz))
    peaks, props = find_peaks(psd_search, height=threshold, prominence=prominence_val, distance=min_dist_bins)
    widths, _, left_ips, right_ips = peak_widths(psd_search, peaks, rel_height=0.75)
    widths_hz = widths * bin_size_hz
    valid_mask = (widths_hz > min_width_hz) & (widths_hz <= max_width_hz)
    orig_indices = np.where(valid_mask)[0]
    power_lin = 10 ** (props['prominences'][valid_mask] / 10) 
    signal_scores = power_lin * widths_hz[valid_mask]
    top_sort = np.argsort(signal_scores)[-number_candidates:]
    
    results = []
    for orig_idx in orig_indices[top_sort]:
        idx = peaks[orig_idx]
        left_mhz = freq[0] + (left_ips[orig_idx] * (bin_size_hz / 1e6))
        right_mhz = freq[0] + (right_ips[orig_idx] * (bin_size_hz / 1e6))
        results.append((idx, (left_mhz + right_mhz) / 2.0, left_mhz, right_mhz))
    return results

def validate_candidates_FM(candidates, legal_freqs, sensibility_khz=100):
    validated = []
    sensibility_mhz = sensibility_khz / 1000.0
    for idx, f_center, left_mhz, right_mhz in candidates:
        if not legal_freqs: continue
        closest_legal = min(legal_freqs, key=lambda x: abs(x - f_center))
        error_mhz = f_center - closest_legal
        if abs(error_mhz) <= sensibility_mhz:
            validated.append((idx, f_center, left_mhz, right_mhz, closest_legal, error_mhz))
    return validated

def calculate_global_ppm(validated_candidates):
    if not validated_candidates: return np.nan
    return np.median([((c[1] - c[4]) / c[4]) * 1e6 for c in validated_candidates])

def haversine_m(lat1, lon1, lat2, lon2):
    R = 6371000.0 
    lat1, lon1, lat2, lon2 = map(np.radians, [lat1, lon1, lat2, lon2])
    a = np.sin((lat2-lat1)/2.0)**2 + np.cos(lat1) * np.cos(lat2) * np.sin((lon2-lon1)/2.0)**2
    return R * 2 * np.arcsin(np.sqrt(a))

def find_coverage_FM(ane_db, coverage_area_m, lat, lng):
    df = ane_db[ane_db["servicio"].astype(str).str.strip() == "Radiodifusión Sonora en FM"].dropna(subset=["frecuencia", "latitud_dec", "longitud_dec"])
    df["dist_m"] = haversine_m(lat, lng, df["latitud_dec"].to_numpy(float), df["longitud_dec"].to_numpy(float))
    return df[df["dist_m"] <= coverage_area_m]["frecuencia"].unique().tolist()

# ---------------------------------------------------------
# INTERFAZ EN TIEMPO REAL
# ---------------------------------------------------------
def setup_realtime_ui():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    win = pg.GraphicsLayoutWidget(show=True, title="HackRF DSP Optimizer")
    win.resize(1200, 800)
    psd_plot = win.addPlot(title="PSD en Tiempo Real", row=0, col=0)
    psd_curve = psd_plot.plot(pen=pg.mkPen('y', width=1.5))
    peaks_scatter = pg.ScatterPlotItem(size=12, pen=pg.mkPen(None), brush=pg.mkBrush(255, 0, 0, 200), symbol='s')
    psd_plot.addItem(peaks_scatter)
    ppm_plot = win.addPlot(title="Estabilidad del Error PPM", row=1, col=0)
    
    # IMPORTANTE: Retornar 'win'
    return app, win, psd_curve, peaks_scatter, ppm_plot

# ---------------------------------------------------------
# LÓGICA DE EVALUACIÓN
# ---------------------------------------------------------
async def evaluate_params_over_time(config_obj, legal_freqs, params, time_trials, ui_elements, current_ppm_line):
    # IMPORTANTE: Desempaquetar 'win'
    app, win, psd_curve, peaks_scatter, ppm_plot = ui_elements
    ppm_history = []
    
    for trial in range(time_trials):
        rf = await get_representative_psd(config_obj, iterations=params['iterations'])
        freq = np.linspace(rf.start_freq_hz, rf.end_freq_hz, len(rf.Pxx)) / 1e6
        bin_size_hz = (rf.end_freq_hz - rf.start_freq_hz) / len(rf.Pxx)
        
        psd_search = mask_dc(rf.Pxx, bin_size_hz)
        _, threshold, _ = floor_and_thres(rf.Pxx)
        
        raw_candidates = find_candidates_FM(
            psd_search, rf.Pxx, freq, bin_size_hz, threshold, 20,
            prominence_val=params['prominence_val'],
            min_width_hz=params['min_width_hz'],
            max_width_hz=params['max_width_hz']
        )
        validated_data = validate_candidates_FM(raw_candidates, legal_freqs, sensibility_khz=50)
        global_ppm = calculate_global_ppm(validated_data)
        ppm_history.append(global_ppm)
        
        psd_curve.setData(freq, rf.Pxx)
        if validated_data:
            peaks_scatter.setData([freq[p[0]] for p in validated_data], [rf.Pxx[p[0]] for p in validated_data])
        else:
            peaks_scatter.setData([], []) 
            
        valid_history = np.array(ppm_history, dtype=float)
        mask = ~np.isnan(valid_history)
        current_ppm_line.setData(np.arange(1, len(valid_history) + 1)[mask], valid_history[mask])
        app.processEvents()

    valid_ppms = [p for p in ppm_history if not np.isnan(p)]
    return ppm_history, np.mean(valid_ppms) if len(valid_ppms) > 1 else np.nan, np.std(valid_ppms) if len(valid_ppms) > 1 else np.inf

# ---------------------------------------------------------
# ORQUESTADOR (GRID SEARCH + CSV)
# ---------------------------------------------------------
async def run_grid_search(config_obj, legal_freqs):
    param_grid = {
        'iterations': [3, 5, 8, 10],
        'prominence_val': [3.0, 5.0, 7.0],
        'min_width_hz': [10e3, 15e3, 25e3],
        'max_width_hz': [200e3, 250e3, 300e3]
    }
    
    combinations = [dict(zip(param_grid.keys(), prod)) for prod in itertools.product(*param_grid.values())]
    ui_elements = setup_realtime_ui()
    
    # IMPORTANTE: Recibir 'win'
    app, win, _, _, ppm_plot = ui_elements
    results = []
    
    # Preparar CSV
    csv_file = open("optimization_results.csv", "w", newline='')
    csv_writer = csv.writer(csv_file)
    csv_writer.writerow(list(param_grid.keys()) + ["mean_ppm", "std_ppm"])
    
    for i, params in enumerate(combinations):
        try:
            current_ppm_line = ppm_plot.plot(pen=pg.mkPen(pg.intColor(i, hues=len(combinations), alpha=150), width=2), symbol='o', symbolSize=5)
            
            history, mean_ppm, std_ppm = await evaluate_params_over_time(config_obj, legal_freqs, params, 5, ui_elements, current_ppm_line)
            
            # Guardar resultados en CSV iteración a iteración
            csv_writer.writerow(list(params.values()) + [mean_ppm, std_ppm])
            csv_file.flush()
            
            results.append({'params': params, 'std_ppm': std_ppm, 'line_item': current_ppm_line})
        except RuntimeError:
            log.info("Ventana cerrada. Abortando Grid Search.")
            break
        
    csv_file.close()

    valid_results = [r for r in results if r['std_ppm'] != np.inf]
    if valid_results:
        best_result = min(valid_results, key=lambda x: x['std_ppm'])
        for r in results: r['line_item'].setPen(pg.mkPen((100, 100, 100, 50), width=1))
        best_result['line_item'].setPen(pg.mkPen('w', width=4))
        best_result['line_item'].setSymbolBrush('r')
        log.info(f"🏆 MEJOR CONFIGURACIÓN: {best_result['params']}")
        app.processEvents()

# ---------------------------------------------------------
# FLUJO PRINCIPAL DE EJECUCIÓN
# ---------------------------------------------------------
async def main():
    asyncio.create_task(system_watchdog()) # Iniciar watchdog
    
    ane_db = pd.read_csv(cfg.PROJECT_ROOT / "db" / "ANE_db_reference.csv", low_memory=False)
    legal_freqs = find_coverage_FM(ane_db, 15000, 5.0310736, -75.5894066)

    config_obj = ServerRealtimeConfig(
        method_psd="welch", center_freq_hz=int(98e6), sample_rate_hz=int(20e6),
        rbw_hz=int(1e3), window="hamming", overlap=float(0.5), lna_gain=int(8),
        vga_gain=int(8), antenna_amp=bool(False), antenna_port=int(1), ppm_error=0,
    )

    await run_grid_search(config_obj, legal_freqs)
    QtWidgets.QApplication.instance().exec_()

if __name__ == "__main__":
    cfg.run_and_capture(main)




# iteration 1:
#🏆 MEJOR CONFIGURACIÓN: {'iterations': 8, 'prominence_val': 7.0, 'min_width_hz': 10000.0, 'max_width_hz': 200000.0}