#!/usr/bin/env python3
"""
@file orchestrator.py
@brief Retrieves campaign configurations from API and synchronizes them with cron.
"""
from __future__ import annotations
import sys
from dataclasses import dataclass
from typing import Any, Dict, Optional, List

import cfg
from utils import RequestClient, CronHandler, modify_persist, get_persist_var

log = cfg.set_logger()
RUNNER_PATH = (cfg.APP_DIR / "campaign_runner.py").resolve()


@dataclass
class Campaign:
    campaign_id: int
    status: str
    start_freq_hz: int
    end_freq_hz: int
    resolution_hz: int
    antenna_port: int
    acquisition_period_s: int
    timeframe: Dict[str, Any]
    start: int
    end: int
    
    # New fields
    window: str
    overlap: float
    sample_rate_hz: int
    lna_gain: int
    vga_gain: int
    antenna_amp: bool

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Campaign":
        cid = d.get("campaign_id")
        status = d.get("status")
        if cid is None or status is None:
            raise ValueError("Campaign ID and Status are required")

        def req(key: str, type_cast=int):
            v = d.get(key)
            if v is None:
                raise ValueError(f"{key} is required")
            return type_cast(v)

        # Parse basic params
        start_freq_hz = req("start_freq_hz")
        end_freq_hz = req("end_freq_hz")
        resolution_hz = req("resolution_hz")
        antenna_port = req("antenna_port")
        acquisition_period_s = req("acquisition_period_s")

        # Parse detailed stats
        window = str(d.get("window", "hamming"))
        overlap = float(d.get("overlap", 0.5))
        sample_rate_hz = int(d.get("sample_rate_hz", 20000000))
        lna_gain = int(d.get("lna_gain", 0))
        vga_gain = int(d.get("vga_gain", 0))
        antenna_amp = bool(d.get("antenna_amp", False))

        # Parse timeframe
        timeframe = d.get("timeframe")
        if not isinstance(timeframe, dict):
            raise ValueError("Timeframe must be a dictionary")
        
        start = int(timeframe.get("start", 0))
        end = int(timeframe.get("end", 0))
        if start == 0 or end == 0:
            raise ValueError("Valid start and end timestamps are required")

        return cls(
            campaign_id=int(cid),
            status=str(status),
            start_freq_hz=start_freq_hz,
            end_freq_hz=end_freq_hz,
            resolution_hz=resolution_hz,
            antenna_port=antenna_port,
            acquisition_period_s=acquisition_period_s,
            timeframe=timeframe,
            start=start,
            end=end,
            window=window,
            overlap=overlap,
            sample_rate_hz=sample_rate_hz,
            lna_gain=lna_gain,
            vga_gain=vga_gain,
            antenna_amp=antenna_amp,
        )


@dataclass
class JobsOrchestrator:
    client: RequestClient
    _log: Any = log
    jobs_ep: str = cfg.JOBS_URL

    def fetch_campaigns(self) -> Optional[List[Dict[str, Any]]]:
        """Fetches the campaigns list from the API."""
        rc, resp = self.client.get(self.jobs_ep)
        if rc != 0 or resp is None:
            self._log.error(f"Failed to fetch jobs: rc={rc}")
            return None

        try:
            payload = resp.json()
            self._log.info("Received payload: %s", payload)
            return payload.get("campaigns", [])
        except Exception:
            self._log.exception("Failed to parse JSON response")
            return None

    def orchestrate(self) -> int:
        """Main execution flow."""
        campaign_list = self.fetch_campaigns()
        
        # If API failure (None), exit with error
        if campaign_list is None:
            return 1
            
        # If list is empty, treat as empty list to ensure IDLE logic runs
        if not campaign_list:
            self._log.info("IDLE: No campaigns found in API.")
            return self.sync_campaigns([])

        return self.sync_campaigns(campaign_list)

    def sync_campaigns(self, campaigns_data: List[Dict[str, Any]]) -> int:
        """
        Syncs the valid campaigns with the CronHandler.
        Updates persistent mode based on scheduled jobs.
        """
        cron = CronHandler(
            logger=self._log,
            verbose=cfg.VERBOSE,
            get_time_ms=cfg.get_time_ms
        )
        
        if cron.cron is None:
            self._log.error("Failed to load Crontab")
            return 1

        # Check Global Mode
        mode_str = get_persist_var("current_mode", cfg.PERSIST_FILE)
        in_realtime = (mode_str == "realtime")
        
        scheduled_count = 0
        error_count = 0

        for camp_dict in campaigns_data:
            try:
                campaign = Campaign.from_dict(camp_dict)
                scheduled = self._process_single_campaign(campaign, cron, in_realtime)
                if scheduled:
                    scheduled_count += 1
            except ValueError as e:
                self._log.error(f"Skipping invalid campaign data: {e}")
                error_count += 1
                continue

        # Save changes to disk
        save_rc = cron.save()
        if save_rc != 0:
            return save_rc

        # Update System Mode based on outcomes
        # Logic: "if the sensor is idle and cron add command, so force campaign mode."
        if not in_realtime:
            if scheduled_count > 0:
                # If we scheduled at least one campaign, we force CAMPAIGN mode.
                # This covers switching from IDLE -> CAMPAIGN.
                self._log.info(f"Setting mode to CAMPAIGN ({scheduled_count} active)")
                modify_persist("current_mode", "campaign", cfg.PERSIST_FILE)
            else:
                # Logic: "if all status of campaigns are in cancelled or programmed, the sensor must be idle."
                # If scheduled_count is 0, it means no campaigns were active/in-timeframe.
                self._log.info("Setting mode to IDLE (No active campaigns)")
                modify_persist("current_mode", "idle", cfg.PERSIST_FILE)

        return 1 if error_count == len(campaigns_data) and len(campaigns_data) > 0 else 0

    def _process_single_campaign(self, campaign: Campaign, cron: CronHandler, in_realtime: bool) -> bool:
        """
        Decides whether to add or erase a specific campaign from cron.
        Returns True if the campaign was successfully added to cron.
        """
        id_val = campaign.campaign_id
        id_str = str(id_val)
        
        # 1. Clean State: Always erase first to ensure clean update or removal
        # This handles removing campaigns that became 'programmed', 'canceled', or entered 'realtime' mode.
        cron.erase(comment=id_str)

        # 2. Strict Status Check
        # Only "active" campaigns are candidates for execution.
        # "programmed", "canceled", "finished", "error" are all ignored.
        if campaign.status != "active":
            self._log.info(f"[SKIP] Campaign {id_str} is '{campaign.status}' (not active)")
            return False

        # 3. Time Check
        # "if are campaigns in timeframe interval"
        if not cron.is_in_activate_time(start=campaign.start, end=campaign.end):
            self._log.info(f"[SKIP] Campaign {id_str} is not in active time window")
            return False

        # 4. Realtime Guard
        # Logic: "are programmed if mode is idle or campaign"
        # If current_mode is 'realtime', we simply skip scheduling.
        if in_realtime:
            self._log.info(f"[SKIP] Campaign {id_str} ignored due to REALTIME mode")
            return False

        # 5. Generate and Add Command
        # If we reach here, we are IDLE or CAMPAIGN, and the campaign is ACTIVE and IN TIME.
        cmd = (
            f"{cfg.PYTHON_EXEC} "
            f"{RUNNER_PATH} "
            f"-f1 {campaign.start_freq_hz} "
            f"-f2 {campaign.end_freq_hz} "
            f"-w {campaign.resolution_hz} "
            f"-p {campaign.antenna_port} "
            f"-wi {campaign.window} "
            f"-o {campaign.overlap} "
            f"-fs {campaign.sample_rate_hz} "
            f"-l {campaign.lna_gain} "
            f"-g {campaign.vga_gain} "
            f"-a {campaign.antenna_amp}"
        )
        
        minutes = int(campaign.acquisition_period_s / 60)
        if minutes < 1: 
            minutes = 1 # safety for cron

        self._log.info(f"[ADD] Scheduling active campaign {id_str}")
        cron.add(command=cmd, comment=id_str, minutes=minutes)
        
        # Update persistent ID only if we are actually scheduling it
        modify_persist("campaign_id", id_val, cfg.PERSIST_FILE)
        
        return True


def main():
    # Initialize client
    client = RequestClient(
        cfg.API_URL, 
        timeout=(5, 15), 
        verbose=cfg.VERBOSE, 
        logger=log, 
        api_key=cfg.get_mac()
    )
    
    orch_obj = JobsOrchestrator(client=client, _log=log, jobs_ep=cfg.JOBS_URL)

    rc = orch_obj.orchestrate()
    if rc != 0:
        log.error(f"Orchestration finished with errors: rc={rc}")
    return rc


if __name__ == "__main__":
    rc = cfg.run_and_capture(main, cfg.LOG_FILES_NUM)
    sys.exit(rc)