#!/usr/bin/env python3
# functions.py

import cfg
from utils import ShmStore
from enum import Enum, auto
from datetime import datetime
from crontab import CronTab
import logging

import numpy as np
from scipy.signal import windows
from scipy.linalg import solve


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
            "rf_mode": "campaign",
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

# --- HELPER FUNCTIONS ---
def format_data_for_upload(payload):
    return {
        "Pxx": payload.get("Pxx", []),
        "start_freq_hz": int(payload.get("start_freq_hz", 0)),
        "end_freq_hz": int(payload.get("end_freq_hz", 0)),
        "timestamp": cfg.get_time_ms(),
        "mac": cfg.get_mac()
    }


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