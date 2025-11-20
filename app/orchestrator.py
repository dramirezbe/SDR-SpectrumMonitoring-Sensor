#!/usr/bin/env python3
"""
@file orchestrator.py
@brief It schedules with cron tool, different acquire_runner repeatedly
"""
from __future__ import annotations
import sys
import subprocess
from dataclasses import dataclass
from typing import Any, Dict, Optional

import cfg
from cfg import OrchestratorState
from utils import RequestClient, CronHandler

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

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Campaign":
        cid = d.get("campaign_id")
        if cid is None:
            raise ValueError("Campaign ID is required")

        status = d.get("status")
        if status is None:
            raise ValueError("Status is required")

        def req(key: str):
            v = d.get(key)
            if v is None:
                raise ValueError(f"{key} is required")
            return v

        start_freq_hz = req("start_freq_hz")
        end_freq_hz = req("end_freq_hz")
        resolution_hz = req("resolution_hz")
        antenna_port = req("antenna_port")

        val = d.get("acquisition_period_s")
        if val is None:
            raise ValueError("Acquisition period is required")
        try:
            acquisition_period_s = int(val)
        except (TypeError, ValueError):
            raise ValueError(f"Invalid acquisition_period_s: {val!r}")

        timeframe = d.get("timeframe")
        if timeframe is None:
            raise ValueError("Timeframe is required")
        if not isinstance(timeframe, dict):
            raise ValueError("Timeframe must be an object with 'start' and 'end' keys")

        s_val = timeframe.get("start")
        if s_val is None:
            raise ValueError("Start time is required")
        try:
            start = int(s_val)
        except (TypeError, ValueError):
            raise ValueError(f"Invalid timeframe start: {s_val!r}")

        e_val = timeframe.get("end")
        if e_val is None:
            raise ValueError("End time is required")
        try:
            end = int(e_val)
        except (TypeError, ValueError):
            raise ValueError(f"Invalid timeframe end: {e_val!r}")

        return cls(
            campaign_id=int(cid),
            status=str(status),
            start_freq_hz=int(start_freq_hz),
            end_freq_hz=int(end_freq_hz),
            resolution_hz=int(resolution_hz),
            antenna_port=int(antenna_port),
            acquisition_period_s=acquisition_period_s,
            timeframe=timeframe,
            start=start,
            end=end,
        )


@dataclass
class JobsOrchestrator:
    client: RequestClient
    _log: Any = log
    jobs_ep: str = cfg.JOBS_URL
    payload: Optional[Dict[str, Any]] = None
    # Removed last_state_get as requested (stateless execution)

    def fetch_jobs(self):
        """Fetches jobs from the API endpoint and returns orchestrator state."""
        rc, resp = self.client.get(self.jobs_ep)
        if rc != 0 or resp is None:
            self._log.error(f"Failed to fetch jobs: rc={rc}")
            return rc, None

        try:
            self.payload = resp.json()
            self._log.info("Received jobs: %s", self.payload)
        except Exception:
            self._log.exception("Failed to parse JSON response")
            return 2, None

        if self.payload is None:
            return 2, None

        campaigns = self.payload.get("campaigns", [])
        real_time = self.payload.get("real_time", None)

        # Priority logic: Realtime > Campaigns > Idle
        if real_time:
            if isinstance(real_time, dict):
                real_time.pop("demodulation", None)
            return 0, OrchestratorState.ORCH_REALTIME
        elif len(campaigns) > 0:
            return 0, OrchestratorState.ORCH_CAMPAIGN_SYNC
        else:
            return 0, OrchestratorState.ORCH_IDLE

    def orchestrate(self):
        """Given an entry state, orchestrates the jobs."""
        rc, state = self.fetch_jobs()
        if rc != 0:
            self._log.error(f"Failed to fetch jobs: rc={rc}")
            return rc

        match state:
            case OrchestratorState.ORCH_IDLE:
                self._log.info("No jobs to sync")
                # Optional: clear cron here if IDLE implies 'stop everything'
                return 0
            case OrchestratorState.ORCH_REALTIME:
                return self.run_realtime()
            case OrchestratorState.ORCH_CAMPAIGN_SYNC:
                return self.validate_campaigns()
        return 0

    def run_realtime(self) -> int:
        """Executes acquire_runner in a loop, checking the API for a stop signal."""
        self._log.info("Starting Real-Time Loop...")
        while True:
            # 1. Execute one run
            if self.payload and self.payload.get("real_time"):
                resp = self.payload["real_time"]
                start_freq_hz = resp.get("start_freq_hz", 98000000)
                end_freq_hz = resp.get("end_freq_hz", 108000000)
                resolution_hz = resp.get("resolution_hz", 4096)
                antenna_port = resp.get("antenna_port", 0)

                cmd = [
                    str(RUNNER_PATH),
                    str(start_freq_hz),
                    str(end_freq_hz),
                    str(resolution_hz),
                    str(antenna_port)
                ]

                try:
                    # check=True raises exception if runner returns non-zero
                    subprocess.run(
                        cmd,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        check=True,
                    )
                except subprocess.CalledProcessError as e:
                    self._log.error(f"Failed to run acquire_runner: {e}")
                    # We don't return 1 here to keep the loop alive, 
                    # but you might want a retry delay.
            
            # 2. Refresh State (Check if we should stop)
            rc, state = self.fetch_jobs()
            if rc != 0:
                # If API fails, we log and retry the loop (or you could exit)
                self._log.warning("API check failed, retrying loop...")
                continue

            if state != OrchestratorState.ORCH_REALTIME:
                self._log.info("Real-time mode ended by API.")
                return 0

    def validate_campaigns(self) -> int:
        """Validates the campaigns and syncs them with cron efficiently."""
        if self.payload is None:
            self._log.error("No payload found")
            return 1
            
        campaigns_data = self.payload.get("campaigns", [])
        if not campaigns_data:
            self._log.info("No campaigns in list to sync.")
            return 0

        # 1. Instantiate CronHandler ONCE outside the loop
        cron = CronHandler(
            logger=self._log,
            verbose=cfg.VERBOSE,
            get_time_ms=cfg.get_time_ms
        )
        
        if cron.cron is None:
            self._log.error("Failed to load Crontab")
            return 1

        error_count = 0

        # 2. Process all campaigns in memory
        for camp_dict in campaigns_data:
            try:
                campaign = Campaign.from_dict(camp_dict)
                # Pass the cron instance, do NOT save inside campaign_sync
                self.campaign_sync(campaign, cron)
            except ValueError as e:
                self._log.error(f"Skipping invalid campaign: {e}")
                error_count += 1
                continue  # Continue to the next campaign even if this one fails

        # 3. Save changes to disk ONCE at the end
        if error_count < len(campaigns_data):
            return cron.save()
        
        return 1 if error_count == len(campaigns_data) else 0

    def campaign_sync(self, campaign: Campaign, cron: CronHandler) -> None:
        """
        Modifies the cron object in memory. 
        Does NOT call cron.save().
        """
        id_str = str(campaign.campaign_id)
        cmd = f"{RUNNER_PATH} {campaign.start_freq_hz} {campaign.end_freq_hz} {campaign.resolution_hz} {campaign.antenna_port}"
        minutes = int(campaign.acquisition_period_s / 60)

        is_active_now = cron.is_in_activate_time(start=campaign.start, end=campaign.end)
        is_terminal_status = campaign.status in ["canceled", "finished", "error"]

        # 1. Erase existing job for this ID (clean slate)
        cron.erase(comment=id_str)

        # 2. Add new job if conditions met
        if not is_terminal_status and is_active_now:
            self._log.info(f"[CRON] Queueing active camp_id {id_str}")
            cron.add(command=cmd, comment=id_str, minutes=minutes)
        else:
            self._log.info(f"[CRON] Skipping camp_id {id_str} (Terminal: {is_terminal_status}, Active: {is_active_now})")


def main():
    client = RequestClient(cfg.API_URL, timeout=(5, 15), verbose=cfg.VERBOSE, logger=log, api_key=cfg.API_KEY)
    orch_obj = JobsOrchestrator(client=client, _log=log, jobs_ep=cfg.JOBS_URL)

    rc = orch_obj.orchestrate()
    if rc != 0:
        log.error(f"Failed to orchestrate: rc={rc}")
    return rc


if __name__ == "__main__":
    rc = cfg.run_and_capture(main, cfg.LOG_FILES_NUM)
    sys.exit(rc)