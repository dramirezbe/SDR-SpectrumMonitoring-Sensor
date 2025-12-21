#!/usr/bin/env python3
import asyncio
import numpy as np
from copy import deepcopy

class AcquireCampaign:
    """
    Production-grade class to acquire RF data and remove DC spike artifacts 
    via spectral stitching with an offset capture.
    """
    def __init__(self, controller, log):
        self.controller = controller
        self._log = log
        # Constants for patching
        self.OFFSET_HZ = 2e6  # Frequency shift for secondary capture
        self.PATCH_BW_HZ = 1e6 # Width of the center to replace

    async def _single_acquire(self, rf_params):
        """Internal low-level acquisition."""
        await self.controller.send_command(rf_params)
        self._log.debug(f"Acquiring CF: {rf_params['center_freq_hz']/1e6} MHz")
        
        # Wait for engine response
        data = await asyncio.wait_for(self.controller.wait_for_data(), timeout=20)
        
        # Hardware cooldown to prevent PLL locking issues or buffer overlaps
        await asyncio.sleep(0.2) 
        return data

    async def get_corrected_data(self, rf_params):
        """
        Performs dual-acquisition and returns a single dictionary 
        with the DC spike removed.
        """
        orig_params = deepcopy(rf_params)
        orig_cf = orig_params["center_freq_hz"]

        # 1. Primary Acquisition (Target Frequency)
        data1 = await self._single_acquire(orig_params)
        
        # 2. Offset Acquisition (Same sample rate, shifted CF)
        offset_params = deepcopy(orig_params)
        offset_params["center_freq_hz"] = orig_cf + self.OFFSET_HZ
        data2 = await self._single_acquire(offset_params)

        # --- Efficient Patching Logic ---
        try:
            pxx1 = np.array(data1['Pxx'])
            pxx2 = np.array(data2['Pxx'])
            
            # Calculate resolution (bins per Hz)
            # Both captures have same SR, so df is identical
            df = (data1['end_freq_hz'] - data1['start_freq_hz']) / len(pxx1)
            bin_shift = int(self.OFFSET_HZ / df)

            # Define the patch indices in the primary array (center)
            patch_bins = int(self.PATCH_BW_HZ / df)
            center_idx = len(pxx1) // 2
            s1, e1 = center_idx - (patch_bins // 2), center_idx + (patch_bins // 2)

            # Locate the clean data in the second array
            # (Shifted down because the second capture CF was shifted up)
            s2, e2 = s1 - bin_shift, e1 - bin_shift

            # Perform the surgical replacement
            if s2 >= 0 and e2 <= len(pxx2):
                pxx1[s1:e1] = pxx2[s2:e2]
                self._log.info(f"DC spike removed at {orig_cf/1e6} MHz.")
            else:
                self._log.warning("Offset capture too narrow to patch requested window.")

            # Update the original dict with cleaned data
            data1['Pxx'] = pxx1.tolist()
            return data1

        except Exception as e:
            self._log.error(f"Failed to process DC spike correction: {e}")
            return data1 # Fallback to raw data if logic fails