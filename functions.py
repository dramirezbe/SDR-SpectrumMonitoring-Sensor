#functions.py

import cfg
from utils import ShmStore
from enum import Enum, auto
from datetime import datetime
from crontab import CronTab
import logging

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
        self.python_env = python_env if python_env else "usr/bin/python3"
        self.cmd = f"{self.python_env} {cmd}"
        self.debug_file = (cfg.PROJECT_ROOT / "mock_crontab.txt").absolute()
        self._log = logger if logger else logging.getLogger(__name__)

        if cfg.DEVELOPMENT:
            self.debug_file.parent.mkdir(parents=True, exist_ok=True)
            self.cron = CronTab(tabfile=str(self.debug_file))
        else:
            self.cron = CronTab(user=True)

    def _ts_to_human(self, ts_ms):
        return datetime.fromtimestamp(ts_ms / 1000).strftime('%Y-%m-%d %H:%M:%S')

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
        if self._job_exists(c_id): return 

        period_s = camp['acquisition_period_s']
        schedule = self._seconds_to_cron_interval(period_s)
        job = self.cron.new(command=self.cmd, comment=f"CAMPAIGN_{c_id}")
        job.setall(schedule)
        self._log.info(f"🆕 ADDED Job ID {c_id} | Schedule: {schedule}")

        # Update persistent store logic...
        dict_persist_params = {
            "rf_mode": "campaign",
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
        except Exception:
            pass

    def sync_jobs(self, campaigns: list, current_time_ms: float, store: ShmStore) -> bool:
        """
        Returns True if ANY campaign is currently active (inside window).
        Returns False if all campaigns are finished, pending, or error.
        """
        any_active = False
        
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
            is_in_window = window_open <= current_time_ms <= window_close
            
            if is_in_window:
                self._upsert_job(camp, store)
                any_active = True # We are busy!
            else:
                self._remove_job(c_id)
        
        self.cron.write()
        return any_active

# --- HELPER FUNCTIONS ---
def format_data_for_upload(payload):
    # (Same as before)
    return {
        "Pxx": payload.get("Pxx", []),
        "start_freq_hz": int(payload.get("start_freq_hz", 0)),
        "end_freq_hz": int(payload.get("end_freq_hz", 0)),
        "timestamp": cfg.get_time_ms(),
        "mac": cfg.get_mac()
    }