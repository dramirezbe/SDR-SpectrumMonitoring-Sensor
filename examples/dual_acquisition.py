import asyncio
import logging
import sys
import time
import numpy as np
import matplotlib.pyplot as plt
from copy import deepcopy
import cfg

# Set up logging
log = cfg.set_logger()

from functions import format_data_for_upload
from utils import ZmqPairController

class AcquireDual:
    def __init__(self, controller, log):
        self.controller = controller
        self._log = log
        # Default values (will be overridden dynamically)
        self.OFFSET_HZ = 2e6  
        self.PATCH_BW_HZ = 1e6 

    def _update_stitching_params(self, sample_rate_hz):
        """Adjusts the offset and patch width based on total bandwidth."""
        if sample_rate_hz >= 4_000_000:
            self.OFFSET_HZ = 2_000_000
            self.PATCH_BW_HZ = 1_000_000
            self._log.info(f"Using Wide-Band Logic: Offset 2MHz, Patch 1MHz")
        else:
            # For 2MHz BW, a 0.5MHz offset keeps the patch in a very clean zone
            self.OFFSET_HZ = 500_000
            self.PATCH_BW_HZ = 200_000 
            self._log.info(f"Using Narrow-Band Logic: Offset 0.5MHz, Patch 0.2MHz")

    async def _single_acquire(self, rf_params):
        """Low-level acquisition with PLL cooling time."""
        await self.controller.send_command(rf_params)
        self._log.debug(f"Acquiring CF: {rf_params['center_freq_hz']/1e6} MHz")
        data = await asyncio.wait_for(self.controller.wait_for_data(), timeout=20)
        await asyncio.sleep(0.05) 
        return data

    async def get_corrected_data(self, rf_params):
        """
        Modified to calculate OFFSET and PATCH dynamically before acquiring.
        """
        orig_params = deepcopy(rf_params)
        sr = orig_params["sample_rate_hz"]
        
        # --- DYNAMIC PARAMETER UPDATE ---
        self._update_stitching_params(sr)
        
        orig_cf = orig_params["center_freq_hz"]

        data1 = await self._single_acquire(orig_params)
        offset_params = deepcopy(orig_params)
        offset_params["center_freq_hz"] = orig_cf + self.OFFSET_HZ
        data2 = await self._single_acquire(offset_params)

        try:
            pxx1, pxx2 = np.array(data1['Pxx']), np.array(data2['Pxx'])
            df = (data1['end_freq_hz'] - data1['start_freq_hz']) / len(pxx1)
            bin_shift = int(self.OFFSET_HZ / df)
            
            center_idx = len(pxx1) // 2
            half_patch = int((self.PATCH_BW_HZ / df) // 2)
            s1, e1 = center_idx - half_patch, center_idx + half_patch
            s2 = s1 - bin_shift
            actual_len = e1 - s1

            if s2 < 0 or (s2 + actual_len) > len(pxx2):
                self._log.warning("Calculated offset out of bounds.")
                return data1

            # Boundary Alignment
            k = 5 
            ref_val1 = np.mean(pxx1[s1-k : s1])
            ref_val2 = np.mean(pxx2[s2-k : s2])
            db_offset = ref_val1 - ref_val2
            
            pxx2_patch = pxx2[s2 : s2 + actual_len] + db_offset

            # Alpha Blending
            mask = np.ones(actual_len)
            blend_width = max(1, int(actual_len * 0.1)) 
            ramp = np.linspace(0, 1, blend_width)
            mask[:blend_width], mask[-blend_width:] = ramp, ramp[::-1]

            pxx1[s1:e1] = (pxx1[s1:e1] * (1 - mask)) + (pxx2_patch * mask)
            data1['Pxx'] = pxx1.tolist()
            return data1

        except Exception as e:
            self._log.error(f"Correction failed: {e}")
            return data1

def plot_dual_results(raw_data1, raw_data2, final_data, params, timings, patch_bounds, active_offset):
    """
    Plots comparing the raw overlap and the final adaptive stitch.
    """
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 10))

    f1 = np.linspace(raw_data1['start_freq_hz'], raw_data1['end_freq_hz'], len(raw_data1['Pxx'])) / 1e6
    f2 = np.linspace(raw_data2['start_freq_hz'], raw_data2['end_freq_hz'], len(raw_data2['Pxx'])) / 1e6
    
    ax1.plot(f1, raw_data1['Pxx'], label='Acquisition 1', alpha=0.7)
    ax1.plot(f2, raw_data2['Pxx'], label=f'Acquisition 2 (+{active_offset/1e3}kHz Offset)', alpha=0.7)
    ax1.set_title(f"Raw Overlap (Sample Rate: {params['sample_rate_hz']/1e6} MHz)", fontweight='bold')
    ax1.set_ylabel("PSD (dB)")
    ax1.legend()
    ax1.grid(True, alpha=0.3)

    ff = np.linspace(final_data['start_freq_hz'], final_data['end_freq_hz'], len(final_data['Pxx'])) / 1e6
    ax2.plot(ff, final_data['Pxx'], color='tab:blue', label='Adaptive Stitched Result')
    
    # Highlight Patch
    ax2.axvspan(patch_bounds[0]/1e6, patch_bounds[1]/1e6, color='yellow', alpha=0.3, label='Adaptive Patch')
    
    title_str = (
        f"Final Result | Offset: {active_offset/1e3}kHz | Patch: {(patch_bounds[1]-patch_bounds[0])/1e3}kHz\n"
        f"T1: {timings['t1']:.1f}ms | Gap: {timings['gap']:.1f}ms | T2: {timings['t2']:.1f}ms | Total: {timings['total']:.1f}ms"
    )
    ax2.set_title(title_str, fontsize=11)
    ax2.set_xlabel("Frequency (MHz)")
    ax2.set_ylabel("PSD (dB)")
    ax2.legend()
    ax2.grid(True, alpha=0.3)

    plt.tight_layout()
    plt.show()

async def run_standalone_acquisition() -> int:
    log.info(f"Starting adaptive acquisition...")
    controller = ZmqPairController(addr=cfg.IPC_ADDR, is_server=True)
    acquirer = AcquireDual(controller=controller, log=log)
    timings = {}

    try:
        async with controller:
            start_bench = time.perf_counter()

            # 1. Update Params and Acquire Data 1
            acquirer._update_stitching_params(RF_PARAMS["sample_rate_hz"])
            t1_start = time.perf_counter()
            data1 = await acquirer._single_acquire(RF_PARAMS)
            timings['t1'] = (time.perf_counter() - t1_start) * 1000

            # 2. Gap
            gap_start = time.perf_counter()
            await asyncio.sleep(0.5) 
            timings['gap'] = (time.perf_counter() - gap_start) * 1000

            # 3. Acquire Data 2 with the dynamic offset
            t2_start = time.perf_counter()
            offset_params = deepcopy(RF_PARAMS)
            offset_params["center_freq_hz"] += acquirer.OFFSET_HZ
            data2 = await acquirer._single_acquire(offset_params)
            timings['t2'] = (time.perf_counter() - t2_start) * 1000
            
            raw1, raw2 = deepcopy(data1), deepcopy(data2)

            # 4. Stitching Calculation
            pxx1, pxx2 = np.array(data1['Pxx']), np.array(data2['Pxx'])
            df = (data1['end_freq_hz'] - data1['start_freq_hz']) / len(pxx1)
            bin_shift = int(acquirer.OFFSET_HZ / df)
            center_idx = len(pxx1) // 2
            half_patch = int((acquirer.PATCH_BW_HZ / df) // 2)
            s1, e1 = center_idx - half_patch, center_idx + half_patch
            s2 = s1 - bin_shift
            
            patch_bounds = (data1['start_freq_hz'] + s1*df, data1['start_freq_hz'] + e1*df)

            k = 5
            ref_val1, ref_val2 = np.mean(pxx1[s1-k:s1]), np.mean(pxx2[s2-k:s2])
            db_offset = ref_val1 - ref_val2
            
            pxx2_patch = pxx2[s2 : s2 + (e1-s1)] + db_offset
            mask = np.ones(e1-s1)
            blend_width = max(1, int(len(mask) * 0.1))
            ramp = np.linspace(0, 1, blend_width)
            mask[:blend_width], mask[-blend_width:] = ramp, ramp[::-1]
            pxx1[s1:e1] = (pxx1[s1:e1] * (1 - mask)) + (pxx2_patch * mask)
            
            data1['Pxx'] = pxx1.tolist()
            timings['total'] = (time.perf_counter() - start_bench) * 1000

            final_data = format_data_for_upload(data1)
            plot_dual_results(raw1, raw2, final_data, RF_PARAMS, timings, patch_bounds, acquirer.OFFSET_HZ)
            
            return 0 

    except Exception as e:
        log.error(f"Error: {e}")
        return 1

# --- CONFIGURATION (Try changing sample_rate_hz to 20_000_000 to see the logic switch) ---
RF_PARAMS = {
    "center_freq_hz": 103_700_000,  
    "sample_rate_hz": 2_000_000, # Try 2M vs 20M
    "rbw_hz": 1_000,
    "window": "hann",
    "overlap": 0.5,
    "lna_gain": 16,
    "vga_gain": 16,
    "antenna_amp": True,
    "antenna_port": 1,
    "method_psd": "pfb"
}

if __name__ == "__main__":
    rc = cfg.run_and_capture(run_standalone_acquisition)
    sys.exit(rc)