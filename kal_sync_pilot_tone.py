#!/usr/bin/env python3
from utils import ShmStore
import cfg
log = cfg.set_logger()

import subprocess, os
import numpy as np
import pandas as pd
import scipy.signal as sig

SR = 20_000_000
FC = 98_000_000
DEC_SR = 200_000
IQ_FILE = "oneshot.bin"

def refine_peak(f, p):
    k = np.argmax(p)
    if k == 0 or k == len(p) - 1: return f[k]
    y1, y2, y3 = p[k-1], p[k], p[k+1]
    d = y1 - 2*y2 + y3
    if abs(d) < 1e-10: return f[k]
    return f[k] + 0.5 * (y1 - y3) / d * (f[1] - f[0])

def main():
    shm = ShmStore()

    subprocess.run([
        "hackrf_transfer", "-f", str(FC), "-s", str(SR), 
        "-n", str(SR), "-a", "1", "-l", "32", "-g", "32", "-r", IQ_FILE
    ], capture_output=True)

    raw = np.fromfile(IQ_FILE, dtype=np.int8)
    os.remove(IQ_FILE)
    iq = raw[0::2].astype(np.float32) + 1j * raw[1::2].astype(np.float32)

    f, p = sig.welch(iq, fs=SR, nperseg=65536, return_onesided=False)
    f = np.fft.fftshift(f)
    p = 10 * np.log10(np.fft.fftshift(p))

    peaks, _ = sig.find_peaks(p, height=np.median(p) + 5, distance=300)
    top_peaks = sorted(peaks, key=lambda x: p[x], reverse=True)[:6]
    cands = sorted([(FC + f[k], p[k]) for k in top_peaks])

    log.info("Strong FM candidates from sweep:")
    for i, (freq, pwr) in enumerate(cands, 1):
        log.info(f"{i:2d}.  {freq/1e6:8.3f} MHz    sweep power = {pwr:5.2f} dB")

    log.info("\nChecking stereo pilot presence...\n")
    results = []
    t = np.arange(len(iq)) / SR

    for freq, pwr in cands:
        freq_mhz = freq / 1e6
        log.info(f"Testing {freq_mhz:.3f} MHz ...")
        
        offset = freq - FC
        iq_bb = iq * np.exp(-1j * 2 * np.pi * offset * t)
        iq_dec = sig.decimate(sig.decimate(iq_bb, 10), 10)
        
        audio = np.angle(iq_dec[1:] * np.conj(iq_dec[:-1]))
        f_a, P_a = sig.welch(audio, fs=DEC_SR, nperseg=65536)
        
        mask = (f_a >= 18000) & (f_a <= 20000)
        f_win, P_win = f_a[mask], P_a[mask]
        
        pilot = refine_peak(f_win, P_win)
        snr = 10 * np.log10(np.max(P_win) / np.median(P_win))
        stereo = snr > 8.0
        df_hz = DEC_SR / 65536
        
        log.info(f"  {freq_mhz:.3f} MHz | pilot={pilot:10.3f} Hz | SNR={snr:6.2f} dB | stereo={stereo} | df={df_hz:.3f} Hz")
        
        results.append({
            "freq_mhz": freq_mhz,
            "sweep_power_db": pwr,
            "pilot_freq_hz": pilot,
            "pilot_snr_db": snr,
            "stereo_detected": stereo,
            "welch_bin_spacing_hz": df_hz
        })

    log.info("\n================ Ranked Results ================\n")
    df = pd.DataFrame(results).sort_values(
        by=["stereo_detected", "pilot_snr_db", "sweep_power_db"], 
        ascending=[False, False, False]
    ).reset_index(drop=True)
    log.info(df.to_string(index=False))

    stereo_df = df[df["stereo_detected"] == True]
    if len(stereo_df) == 0:
        log.info("\nNo stereo FM station found with sufficient pilot SNR.")
        return

    best = stereo_df.iloc[0]
    best_freq = best["freq_mhz"] * 1e6
    
    offset = best_freq - FC
    iq_bb = iq * np.exp(-1j * 2 * np.pi * offset * t)
    iq_dec = sig.decimate(sig.decimate(iq_bb, 10), 10)

    f_bb, P_bb = sig.welch(iq_dec, fs=DEC_SR, nperseg=16384, return_onesided=False)
    f_bb, P_bb = np.fft.fftshift(f_bb), np.fft.fftshift(P_bb.real)

    deltas = np.linspace(-15000, 15000, 1000)
    u = np.linspace(30000, 90000, 1000)
    costs = [np.mean(((np.interp(d - u, f_bb, P_bb) - np.interp(d + u, f_bb, P_bb))**2) / 
             np.maximum((np.interp(d - u, f_bb, P_bb) + np.interp(d + u, f_bb, P_bb))**2, 1e-20)) 
             for d in deltas]
    
    fine_offset = deltas[np.argmin(costs)]
    ppm_error = (fine_offset / best_freq) * 1e6
    sug_ppm = -ppm_error

    log.info("\n================ Best Station ==================\n")
    log.info(f"Best stereo station: {best['freq_mhz']:.6f} MHz")
    log.info(f"Sweep power        : {best['sweep_power_db']:.2f} dB")
    log.info(f"Pilot frequency    : {best['pilot_freq_hz']:.3f} Hz")
    log.info(f"Pilot SNR          : {best['pilot_snr_db']:.2f} dB")

    log.info("\n================ RF PPM Estimate ==============\n")
    log.info(f"Reference station tuned frequency : {best_freq/1e6:.6f} MHz")
    log.info(f"Estimated RF center offset        : {fine_offset:+.3f} Hz")
    log.info(f"Estimated RF ppm error            : {ppm_error:+.6f} ppm")
    log.info("Suggested correction to apply:")
    log.info(f"  Frequency shift to apply        : {-fine_offset:+.3f} Hz")
    log.info(f"  Approximate ppm adjustment      : {sug_ppm:+.6f} ppm")

    ppm_to_store = int(round(sug_ppm))
    shm.update_from_dict({"ppm_error": ppm_to_store, "last_kal_ms": cfg.get_time_ms()})
    log.info(f"Persisted calibration in shm: ppm_error={ppm_to_store}")

if __name__ == "__main__":
    main()