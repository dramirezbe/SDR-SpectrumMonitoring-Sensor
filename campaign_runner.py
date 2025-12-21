#!/usr/bin/env python3
import cfg
import sys
import json
import asyncio
import time
from pathlib import Path
from utils import atomic_write_bytes, RequestClient, StatusDevice, ShmStore, ZmqPairController
from functions import format_data_for_upload, AcquireCampaign

log = cfg.set_logger()

class CampaignRunner:
    def __init__(self):
        self.status_obj = StatusDevice(logs_dir=cfg.LOGS_DIR, logger=log)
        self.cli = RequestClient(cfg.API_URL, mac_wifi=cfg.get_mac(), timeout=(5, 15), verbose=cfg.VERBOSE, logger=log)
        self.store = ShmStore()
        self.campaign_id = self.store.consult_persistent("campaign_id")

    def _get_rf_params(self) -> dict:
        keys = ["center_freq_hz", "span", "sample_rate_hz", "rbw_hz", "overlap", 
                "window", "scale", "lna_gain", "vga_gain", "antenna_amp", 
                "antenna_port", "ppm_error", "filter"]
        try:
            return {k: self.store.consult_persistent(k) for k in keys}
        except Exception as e:
            log.error(f"Error reading rf params: {e}")
            return {}

    def _get_disk_usage(self) -> float:
        use = float(self.status_obj.get_disk().get("disk_mb", 0))
        total = float(self.status_obj.get_total_disk().get("disk_mb", 1))
        return use / total

    def _cleanup_disk(self, target_dir: Path, to_delete: int = 10):
        try:
            files = sorted([p for p in target_dir.iterdir() if p.suffix == ".json"], 
                           key=lambda p: int(p.stem) if p.stem.isdigit() else 0)
            for f in files[:to_delete]:
                f.unlink()
        except Exception as e:
            log.error(f"Disk cleanup failed: {e}")

    def _save_data(self, data: dict, target_dir: Path) -> bool:
        try:
            timestamp = cfg.get_time_ms()
            json_bytes = json.dumps(data, separators=(",", ":")).encode("utf-8")
            target_path = target_dir / f"{timestamp}.json"
            atomic_write_bytes(target_path, json_bytes)
            return True
        except Exception as e:
            log.error(f"Save failed: {e}")
            return False

    async def acquire_payload(self, rf_cfg: dict):
        """Handles the ZMQ interaction and hardware acquisition."""
        self.store.add_to_persistent("campaign_runner_running", True)
        try:
            async with ZmqPairController(addr=cfg.IPC_ADDR, is_server=True) as zmq_ctrl:
                await asyncio.sleep(0.5)
                acquirer = AcquireCampaign(zmq_ctrl, log)
                log.info(f"Starting Campaign Acquisition ID: {self.campaign_id}")
                return await acquirer.get_corrected_data(rf_cfg)
        except OSError as e:
            if "Address already in use" in str(e):
                log.warning("⚠️ ZMQ Socket busy. Skipping.")
            return None
        finally:
            self.store.add_to_persistent("campaign_runner_running", False)

    async def run(self) -> int:
        # 1. Setup
        rf_cfg = self._get_rf_params()
        if not rf_cfg:
            return 1

        # 2. Acquisition
        raw_payload = await self.acquire_payload(rf_cfg)
        if not raw_payload:
            return 1

        data_dict = format_data_for_upload(raw_payload)
        data_dict["campaign_id"] = self.campaign_id or 0

        # 3. Upload
        start_t = time.perf_counter()
        rc, _ = self.cli.post_json(cfg.DATA_URL, data_dict)
        delta_t_ms = int((time.perf_counter() - start_t) * 1000)

        # 4. Post-Process (Queue or Save)
        if rc != 0:
            if len(list(cfg.QUEUE_DIR.iterdir())) < 50:
                self._save_data(data_dict, cfg.QUEUE_DIR)
            return 1

        self.store.add_to_persistent("delta_t_ms", delta_t_ms)
        
        # Disk Management
        if self._get_disk_usage() > 0.8:
            self._cleanup_disk(cfg.HISTORIC_DIR)
        
        if self._get_disk_usage() < 0.9:
            self._save_data(data_dict, cfg.HISTORIC_DIR)

        return 0

if __name__ == "__main__":
    runner = CampaignRunner()
    rc = cfg.run_and_capture(runner.run)
    sys.exit(rc)