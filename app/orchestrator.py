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
from utils import run_and_capture, RequestClient, CronHandler

log = cfg.get_logger()
RUNNER_PATH = (cfg.PROJECT_ROOT / "build" / "acquire_runner").resolve()


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
    verbose: bool = cfg.VERBOSE
    _log: Any = log
    jobs_ep: str = cfg.JOBS_URL
    last_state_get: str = ""
    payload: Optional[Dict[str, Any]] = None

    def fetch_jobs(self):
        """Fetches jobs from the API endpoint and returns orchestrator state."""
        rc, resp = self.client.get(self.jobs_ep)
        if rc != 0 or resp is None:
            self._log.error(f"Failed to fetch jobs: rc={rc}")
            return rc, None

        try:
            self.payload = resp.json()
            resp_str = str(self.payload)
            if self.verbose:
                self._log.info("Received jobs: %s", self.payload)
        except Exception:
            self._log.exception("Failed to parse JSON response")
            return 2, None

        if self.payload is None:
            return 2, None

        campaigns = self.payload.get("campaigns", [])
        real_time = self.payload.get("real_time", None)

        if self.last_state_get == resp_str:
            return 0, OrchestratorState.ORCH_IDLE

        if real_time:
            self.last_state_get = resp_str
            if isinstance(real_time, dict):
                real_time.pop("demodulation", None)
            return 0, OrchestratorState.ORCH_REALTIME
        elif len(campaigns) > 0:
            self.last_state_get = resp_str
            return 0, OrchestratorState.ORCH_CAMPAIGN_SYNC
        else:
            self.last_state_get = resp_str
            return 0, OrchestratorState.ORCH_IDLE

    def orchestrate(self):
        """Given an entry state, orchestrates the jobs."""
        rc, state = self.fetch_jobs()
        if rc != 0:
            self._log.error(f"Failed to fetch jobs: rc={rc}")
            return rc

        match state:
            case OrchestratorState.ORCH_IDLE:
                if self.verbose:
                    self._log.info("No jobs to sync")
                return 0
            case OrchestratorState.ORCH_REALTIME:
                return self.run_realtime()
            case OrchestratorState.ORCH_CAMPAIGN_SYNC:
                return self.validate_campaigns()
        return 0

    def run_realtime(self) -> int:
        """Executes acquire_runner in a loop, checking the API for a stop signal."""
        while True:
            rc, state = self.fetch_jobs()
            if rc != 0:
                return rc
            if state != OrchestratorState.ORCH_REALTIME:
                self._log.info("Real-time mode ended by API.")
                return 0

            if self.payload is None or self.payload.get("real_time") is None:
                return 1

            resp = self.payload["real_time"]
            start_freq_hz = resp.get("start_freq_hz", 98000000)
            end_freq_hz = resp.get("end_freq_hz", 108000000)
            resolution_hz = resp.get("resolution_hz", 4096)
            antenna_port = resp.get("antenna_port", 0)

            cmd = [str(RUNNER_PATH), str(start_freq_hz), str(end_freq_hz), str(resolution_hz), str(antenna_port)]

            try:
                subprocess.run(
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=True,
                )
            except subprocess.CalledProcessError as e:
                self._log.error(f"Failed to run acquire_runner with error: {e}")
                return 1

    def validate_campaigns(self) -> int:
        """Validates the campaigns in the payload, and then sync them with cron."""
        if self.payload is None:
            self._log.error("No payload found, error in orchestrate logic")
            return 1
        campaigns = self.payload.get("campaigns", [])
        if not campaigns:
            self._log.error("No campaigns found, error in orchestrate logic")
            return 1

        for camp in campaigns:
            try:
                campaign = Campaign.from_dict(camp)
            except ValueError as e:
                self._log.error(str(e))
                return 1

            if self.campaign_sync(campaign) != 0:
                return 1
        return 0

    def campaign_sync(self, campaign: Campaign) -> int:
        """Synchronizes a campaign with the crontab using the CronHandler module."""
        
        # Instantiate the new handler, passing in the required dependencies.
        cron = CronHandler(
            logger=self._log,
            verbose=self.verbose,
            get_time_ms=cfg.get_time_ms
        )
        
        if cron.cron is None:
            return 1

        id = str(campaign.campaign_id)
        cmd = f"{RUNNER_PATH} {campaign.start_freq_hz} {campaign.end_freq_hz} {campaign.resolution_hz} {campaign.antenna_port}"
        minutes = int(campaign.acquisition_period_s / 60)

        is_active_now = cron.is_in_activate_time(start=campaign.start, end=campaign.end)
        is_terminal_status = campaign.status in ["canceled", "finished", "error"]

        # 1. ALWAYS erase any job with this ID to ensure a clean slate.
        if cron.erase(comment=id) != 0:
            return 1

        # 2. DECIDE if a new job should be added.
        if not is_terminal_status and is_active_now:
            if self.verbose:
                self._log.info(f"[CRON] Syncing active camp_id {id}")
            if cron.add(command=cmd, comment=id, minutes=minutes) != 0:
                return 1
        else:
            if self.verbose:
                self._log.info(f"[CRON] Erased or ignored camp_id {id} (Terminal: {is_terminal_status}, Active: {is_active_now})")

        # 3. SAVE any changes made to the crontab.
        return cron.save()


def main():
    client = RequestClient(
        base_url=cfg.API_URL,
        timeout=(5, 15),
        verbose=cfg.VERBOSE,
        logger=log,
    )
    orch_obj = JobsOrchestrator(client=client, verbose=cfg.VERBOSE, _log=log, jobs_ep=cfg.JOBS_URL)

    rc = orch_obj.orchestrate()
    if rc != 0:
        log.error(f"Failed to orchestrate: rc={rc}")
    return rc


if __name__ == "__main__":
    rc = run_and_capture(main, log, cfg.LOGS_DIR / "orchestrator", cfg.get_time_ms(), cfg.LOG_FILES_NUM)
    sys.exit(rc)