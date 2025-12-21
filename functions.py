#!/usr/bin/env python3
# functions.py
import cfg
from utils import ShmStore

from enum import Enum, auto
from crontab import CronTab
import logging
import numpy as np
import asyncio
from copy import deepcopy

class SysState(Enum):
    IDLE = auto()
    CAMPAIGN = auto()
    REALTIME = auto()
    KALIBRATING = auto()
    ERROR = auto()

class GlobalSys:
    current = SysState.IDLE
    log = cfg.set_logger()

    @classmethod
    def set(cls, new_state: SysState):
        if cls.current != new_state:
            cls.log.info(f"State Transition: {cls.current.name} -> {new_state.name}")
            cls.current = new_state

    @classmethod
    def is_idle(cls):
        return cls.current == SysState.IDLE

# --- HELPER FUNCTIONS ---
def format_data_for_upload(payload):
    return {
        "Pxx": payload.get("Pxx", []),
        "start_freq_hz": int(payload.get("start_freq_hz", 0)),
        "end_freq_hz": int(payload.get("end_freq_hz", 0)),
        "timestamp": cfg.get_time_ms(),
        "mac": cfg.get_mac()
    }

class CronSchedulerCampaign:
    def __init__(self, poll_interval_s, python_env=None, cmd=None, logger=None):
        self.poll_interval_ms = poll_interval_s * 1000
        self.python_env = python_env if python_env else "/usr/bin/python3"
        self.cmd = f"{self.python_env} {cmd}"
        self.debug_file = (cfg.PROJECT_ROOT / "mock_crontab.txt").absolute()
        self._log = logger if logger else logging.getLogger(__name__)

        if cfg.DEVELOPMENT:
            # Ensure directory exists
            self.debug_file.parent.mkdir(parents=True, exist_ok=True)

            # ✅ Ensure file exists
            if not self.debug_file.exists():
                self.debug_file.write_text("", encoding="utf-8")

            self.cron = CronTab(tabfile=str(self.debug_file))
        else:
            self.cron = CronTab(user=True)

    def _ts_to_human(self, ts_ms):
        """Converts milliseconds timestamp to human-readable string"""
        if ts_ms is None: return "None"
        return cfg.human_readable(ts_ms, target_tz="UTC")

    def _seconds_to_cron_interval(self, seconds):
        minutes = int(seconds / 60)
        if minutes < 1: minutes = 1 
        return f"*/{minutes} * * * *"

    def _job_exists(self, campaign_id):
        return any(self.cron.find_comment(f"CAMPAIGN_{campaign_id}"))

    def _remove_job(self, campaign_id):
        if self._job_exists(campaign_id):
            self.cron.remove_all(comment=f"CAMPAIGN_{campaign_id}")
            self._log.info(f"🗑️ REMOVED Job ID {campaign_id}")

    def _upsert_job(self, camp, store: ShmStore):
        c_id = camp['campaign_id']
        
        # 1. ALWAYS Update Shared Memory (Critical!)
        dict_persist_params = {
            "campaign_id": c_id,
            "center_freq_hz": camp.get('center_freq_hz'),
            "sample_rate_hz": camp.get('sample_rate_hz'),
            "rbw_hz": camp.get('rbw_hz'),
            "span": camp.get('span'),
            "antenna_port": camp.get('antenna_port'),
            "window": camp.get('window'),
            "scale": camp.get('scale'),
            "overlap": camp.get('overlap'),
            "lna_gain": camp.get('lna_gain'),
            "vga_gain": camp.get('vga_gain'),
            "antenna_amp": camp.get('antenna_amp'),
            "filter": camp.get('filter')
        }
        try:
            store.update_from_dict(dict_persist_params)
            self._log.info(f"💾 SharedMemory UPDATED for Campaign {c_id} ({camp.get('center_freq_hz')} Hz)")
        except Exception:
            self._log.error("Failed to update store.")

        # 2. Setup Cron only if missing
        if self._job_exists(c_id): 
            return 

        period_s = camp['acquisition_period_s']
        schedule = self._seconds_to_cron_interval(period_s)
        job = self.cron.new(command=self.cmd, comment=f"CAMPAIGN_{c_id}")
        job.setall(schedule)
        self._log.info(f"🆕 ADDED Job ID {c_id} | Schedule: {schedule}")

    def sync_jobs(self, campaigns: list, current_time_ms: float, store: ShmStore) -> bool:
        """
        Returns True if ANY campaign is currently active (inside window).
        Returns False if all campaigns are finished, pending, or error.
        """
        any_active = False
        
        # DEBUG: Human Readable Now
        now_human = self._ts_to_human(current_time_ms)
        self._log.info(f"🕒 SYNC CHECK | Current Time: {now_human} ({int(current_time_ms)})")

        for camp in campaigns:
            c_id = camp['campaign_id']
            status = camp['status']
            start_ms = camp['timeframe']['start']
            end_ms = camp['timeframe']['end']
            
            if status in ['canceled', 'error', 'finished']:
                self._remove_job(c_id)
                continue

            # Time Window Logic
            window_open = start_ms - self.poll_interval_ms
            window_close = end_ms - self.poll_interval_ms
            
            # DEBUG: Human Readable Ranges
            start_human = self._ts_to_human(start_ms)
            end_human = self._ts_to_human(end_ms)
            win_open_human = self._ts_to_human(window_open)
            win_close_human = self._ts_to_human(window_close)

            is_in_window = window_open <= current_time_ms <= window_close
            
            self._log.info(f"🔎 CHECK Camp {c_id} | Status: {status}")
            self._log.info(f"   📅 Range : {start_human} -> {end_human}")
            self._log.info(f"   🪟 Window: {win_open_human} -> {win_close_human}")
            self._log.info(f"   🎯 Active? {'YES' if is_in_window else 'NO'}")

            if is_in_window:
                self._upsert_job(camp, store)
                any_active = True # We are busy!
                # Break ensures we don't overwrite SharedMemory with a subsequent (inactive) campaign
                break
            else:
                self._remove_job(c_id)
        
        self.cron.write()
        return any_active
    
class SimpleDCSpikeCleaner:
    def __init__(self, search_frac=0.05, width_frac=0.005, neighbor_bins=20):
        """
        search_frac: Region to search for the spike.
        width_frac: Width of the removal zone.
        neighbor_bins: Number of bins to sample for noise estimation.
        """
        self.search_frac = search_frac
        self.width_frac = width_frac
        self.neighbor_bins = neighbor_bins

    def clean(self, Pxx):
        Pxx = np.asarray(Pxx, float).copy()
        n = len(Pxx)
        if n < self.neighbor_bins * 2: 
            return Pxx

        # 1. Locate the peak in the center region
        mid = n // 2
        search_radius = int(n * (self.search_frac / 2))
        s_start = max(0, mid - search_radius)
        s_end = min(n, mid + search_radius)
        peak_idx = s_start + np.argmax(Pxx[s_start:s_end])

        # 2. Define the removal zone
        width_radius = max(1, int(n * (self.width_frac / 2)))
        idx0 = max(0, peak_idx - width_radius)
        idx1 = min(n - 1, peak_idx + width_radius)

        # 3. Sample neighbors for noise statistics
        l_neighbor = Pxx[max(0, idx0 - self.neighbor_bins): idx0]
        r_neighbor = Pxx[idx1 + 1: min(n, idx1 + 1 + self.neighbor_bins)]
        
        neighbors = np.concatenate([l_neighbor, r_neighbor])
        
        # 4. Calculate local noise statistics
        # We ensure local_sigma is at least 0 to prevent the 'scale < 0' error
        local_sigma = np.std(neighbors) if neighbors.size > 0 else 0.0

        # 5. Create the linear trend (The "Line")
        y0, y1 = Pxx[idx0], Pxx[idx1]
        num_points = idx1 - idx0 + 1
        linear_trend = np.linspace(y0, y1, num_points)

        # 6. Generate and add noise to the trend
        # FIX: Ensure scale is non-negative using max(0, ...)
        # We use local_sigma directly to match the surrounding noise floor
        safe_scale = max(0.0, local_sigma)
        noise = np.random.normal(0, safe_scale, num_points)
        
        Pxx[idx0:idx1 + 1] = linear_trend + noise

        return Pxx
    
class AcquireRealtime:
    def __init__(self, controller, cleaner, hardware_max_bw=20_000_000, user_safe_bw=18_000_000, log=cfg.set_logger()):
        self._log = log
        self.controller = controller
        self.cleaner = cleaner
        self.HW_BW = hardware_max_bw      # The 20MHz we actually use
        self.SAFE_BW = user_safe_bw       # The 18MHz limit
        self.OFFSET = 1_000_000           # 1MHz shift to move the DC spike

    async def acquire_with_offset(self, user_config):
        """
        Interacts with RF-Engine and returns the final cleaned/cropped dict.
        """
        requested_fs = user_config.get("sample_rate_hz", 0)
        original_center = user_config.get("center_freq_hz")

        # --- VALIDATION: Logic for 18MHz or less ---
        if requested_fs <= self.SAFE_BW:
            # 1. Prepare SECRET hardware config
            # We shift center freq UP by 1MHz so the DC spike is at +1MHz
            hw_config = user_config.copy()
            hw_config["sample_rate_hz"] = self.HW_BW
            hw_config["center_freq_hz"] = original_center + self.OFFSET
            
            # 2. Acquire 20MHz
            raw_payload = await self._send_and_receive(hw_config)
            if not raw_payload: return None

            # 3. Clean Spikes on the 20MHz data
            pxx = np.array(raw_payload["Pxx"])
            pxx_cleaned = self.cleaner.clean(pxx)

            # 4. CROP to the original requested BW
            # Because we shifted center UP by 1MHz, the original target 
            # is now at -1MHz relative to the current center.
            final_data = self._extract_sub_region(
                pxx_cleaned, 
                hw_center=original_center + self.OFFSET,
                hw_bw=self.HW_BW,
                target_center=original_center,
                target_bw=requested_fs
            )
            return final_data

        else:
            # --- FALLBACK: If user wants > 18MHz, do nothing but clean ---
            self._log.info(f"Requested BW {requested_fs} > 18MHz. Skipping offset/crop.")
            raw_payload = await self._send_and_receive(user_config)
            if not raw_payload: return None
            
            pxx = np.array(raw_payload["Pxx"])
            raw_payload["Pxx"] = self.cleaner.clean(pxx).tolist()
            return raw_payload
        
    async def acquire_raw(self, config):
        """
        Performs a direct acquisition using the provided config.
        Applies spike correction (cleaning) but performs NO frequency 
        offsets or cropping.
        """
        # 1. Send the command exactly as provided and wait for data
        payload = await self._send_and_receive(config)
        
        if not payload or "Pxx" not in payload:
            self._log.warning("Acquisition failed or returned empty payload.")
            return None

        # 2. Convert to numpy for the cleaner, then back to list for JSON compatibility
        pxx = np.array(payload["Pxx"])
        payload["Pxx"] = self.cleaner.clean(pxx).tolist()

        return payload

    async def _send_and_receive(self, config):
        await self.controller.send_command(config)
        try:
            return await asyncio.wait_for(self.controller.wait_for_data(), timeout=10)
        except asyncio.TimeoutError:
            return None

    def _extract_sub_region(self, pxx, hw_center, hw_bw, target_center, target_bw):
        """
        Math to find where the original 18MHz (or less) sits inside the 20MHz capture.
        """
        num_bins = len(pxx)
        hz_per_bin = hw_bw / num_bins
        
        # Calculate start/end of the hardware span
        hw_min_f = hw_center - (hw_bw / 2)
        
        # Calculate start/end of the user's requested span
        target_min_f = target_center - (target_bw / 2)
        target_max_f = target_center + (target_bw / 2)

        # Map frequencies to array indices
        start_idx = int((target_min_f - hw_min_f) / hz_per_bin)
        end_idx = int((target_max_f - hw_min_f) / hz_per_bin)

        # Ensure we stay within array bounds
        start_idx = max(0, start_idx)
        end_idx = min(num_bins, end_idx)

        return {
            "Pxx": pxx[start_idx:end_idx].tolist(),
            "start_freq_hz": int(target_min_f),
            "end_freq_hz": int(target_max_f),
            "sample_rate_hz": target_bw
        }


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