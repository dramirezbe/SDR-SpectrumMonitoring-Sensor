#!/usr/bin/env python3
import cfg
log = cfg.set_logger()
from utils import ZmqPairController
import sys
import asyncio
import matplotlib.pyplot as plt
import numpy as np

# --- Constants ---
CENTER_FREQ = int(98e6)
SAMPLE_RATE = int(10e6)
RBW_HZ = int(10e3)
OVERLAP = 0.5
WINDOW = "hamming"
SCALE = "dbm"
LNA_GAIN = 16
VGA_GAIN = 24
ANTENNA_AMP = True
ANTENNA_PORT = 1
PPM_ERROR = 0

async def _single_acquire(rf_params, controller):
    await controller.send_command(rf_params)
    log.info(f"Acquiring: {rf_params['center_freq_hz']/1e6} MHz")
    # RF engine returns: {"Pxx": [...], "start_freq_hz": int, "end_freq_hz": int}
    data = await asyncio.wait_for(controller.wait_for_data(), timeout=20)
    return data

async def acquire_campaign(rf_params, controller):
    """
    Simplified acquisition: Both captures use the same sample rate.
    We just shift the index to find the 'clean' data.
    """
    orig_cf = rf_params["center_freq_hz"]
    offset_hz = 2e6

    # 1. Primary Acquisition (e.g., 98MHz @ 20MSPS)
    data1 = await _single_acquire(rf_params.copy(), controller)
    data1_raw = {**data1, "Pxx": list(data1["Pxx"])} 
    
    # 2. Offset Acquisition (e.g., 100MHz @ 20MSPS)
    rf_params["center_freq_hz"] = orig_cf + offset_hz
    # Keep rf_params["sample_rate_hz"] the same as data1
    data2 = await _single_acquire(rf_params, controller)

    # --- DC Spike Removal Logic (Simpler) ---
    pxx1 = np.array(data1['Pxx'])
    pxx2 = np.array(data2['Pxx'])
    
    # Since sample rates are the same, df is identical
    df = (data1['end_freq_hz'] - data1['start_freq_hz']) / len(pxx1)
    bin_shift = int(offset_hz / df) # How many bins the spectrum shifted

    # Define 1MHz patch area in Data 1 (around its center)
    patch_bins = int(1e6 / df)
    center_idx = len(pxx1) // 2
    idx_start1 = center_idx - (patch_bins // 2)
    idx_end1 = center_idx + (patch_bins // 2)

    # Find the SAME frequency range in Data 2
    # Because Data 2 is shifted UP by offset_hz, 
    # the target frequency is shifted DOWN in indices.
    idx_start2 = idx_start1 - bin_shift
    idx_end2 = idx_end1 - bin_shift

    # Replacement
    log.info(f"Patching center using {bin_shift} bin offset logic.")
    pxx1[idx_start1:idx_end1] = pxx2[idx_start2:idx_end2]

    data1['Pxx'] = pxx1.tolist()
    return data1_raw, data2, data1

async def main() -> int:
    rf_cfg = {
        "rf_mode": "campaign", "method_psd": "pfb",
        "center_freq_hz": CENTER_FREQ, "sample_rate_hz": SAMPLE_RATE,
        "rbw_hz": RBW_HZ, "overlap": OVERLAP, "window": WINDOW,
        "scale": SCALE, "lna_gain": LNA_GAIN, "vga_gain": VGA_GAIN,
        "antenna_amp": ANTENNA_AMP, "antenna_port": ANTENNA_PORT,
        "span": SAMPLE_RATE, "ppm_error": PPM_ERROR,
    }

    controller = ZmqPairController(addr=cfg.IPC_ADDR, is_server=True, verbose=False)
    
    try:        
        async with controller as zmq_ctrl:
            await asyncio.sleep(0.5)

            # 1. Acquire the data
            d1_raw, d2_raw, d_final = await acquire_campaign(rf_cfg, controller)

            # 2. Create Plotting Structure
            fig, (ax_comp, ax_final) = plt.subplots(2, 1, figsize=(12, 10))
            fig.subplots_adjust(hspace=0.4)

            # --- HELPER: Calculate Frequency Array ---
            def get_f_mhz(data):
                return np.linspace(data['start_freq_hz'], data['end_freq_hz'], len(data['Pxx'])) / 1e6

            # --- PLOT 1: COMPARISON (OVERLAY) ---
            f1 = get_f_mhz(d1_raw)
            f2 = get_f_mhz(d2_raw)

            ax_comp.plot(f1, d1_raw['Pxx'], label="Original (98MHz Center)", color='red', alpha=0.6, linewidth=1)
            ax_comp.plot(f2, d2_raw['Pxx'], label="Offset (100MHz Center)", color='orange', alpha=0.8, linewidth=1)
            
            # Highlight the patch area (the 1MHz window we are fixing)
            ax_comp.axvspan(97.5, 98.5, color='gray', alpha=0.1, label="1MHz Patch Zone")
            
            ax_comp.set_title("Comparison: Original vs Offset Frequency Capture")
            ax_comp.set_ylabel("Power (dBm)")
            ax_comp.legend(loc='upper right')
            ax_comp.grid(True, alpha=0.3)

            # --- PLOT 2: FINAL RESULT ---
            f_final = get_f_mhz(d_final)
            ax_final.plot(f_final, d_final['Pxx'], color='green', linewidth=1)
            
            ax_final.set_title("Final Cleaned Spectrum (Stitched)")
            ax_final.set_xlabel("Frequency (MHz)")
            ax_final.set_ylabel("Power (dBm)")
            ax_final.grid(True, alpha=0.3)

            plt.show()

    except Exception as e:
        log.error(f"Error: {e}")
        import traceback
        log.error(traceback.format_exc())
        return 1
    return 0

if __name__ == "__main__":
    sys.exit(asyncio.run(main()))