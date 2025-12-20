#!/usr/bin/env python3
# orchestrator.py

import cfg
log = cfg.set_logger()
from utils import RequestClient, ZmqPairController, ServerRealtimeConfig, ShmStore, ElapsedTimer
from functions import format_data_for_upload, CronSchedulerCampaign, GlobalSys, SysState

import sys
import asyncio
from dataclasses import asdict
import time

# --- CONFIG FETCHING ---
def fetch_realtime_config(client):
    """Fetches job configuration and validates it."""
    delta_t_ms = 0 
    try:
        start_delta_t = time.perf_counter()
        _, resp = client.get(cfg.REALTIME_URL)
        end_delta_t = time.perf_counter()
        delta_t_ms = int((end_delta_t - start_delta_t) * 1000)
        
        if resp is None or resp.status_code != 200:
            return {}, resp, delta_t_ms 
        
        try:
            json_payload = resp.json()
        except Exception:
            return {}, resp, delta_t_ms 
        
        if not json_payload:
            return {}, resp, delta_t_ms

        log.info(f"json_payload: {json_payload}") 

        try:
            config_obj = ServerRealtimeConfig(
                rf_mode="realtime",
                center_freq_hz=int(json_payload.get("center_freq_hz")), 
                sample_rate_hz=int(json_payload.get("sample_rate_hz")),
                rbw_hz=int(json_payload.get("rbw_hz")),
                window=json_payload.get("window"),
                scale=json_payload.get("scale"),
                overlap=float(json_payload.get("overlap")),
                lna_gain=int(json_payload.get("lna_gain")),
                vga_gain=int(json_payload.get("vga_gain")),
                antenna_amp=bool(json_payload.get("antenna_amp")),
                antenna_port=int(json_payload.get("antenna_port")), 
                span=int(json_payload.get("span")),
                ppm_error=0
            )
            return asdict(config_obj), resp, delta_t_ms

        except (ValueError, TypeError) as val_err:
            log.error(f"SKIPPING REALTIME: {val_err}")
            return {}, resp, delta_t_ms 

    except Exception as e:
        log.error(f"Error fetching config: {e}")
        return {}, None, 0

# --- HELPER: CALIBRATION ---
async def _perform_calibration_sequence(store):
    log.info("--------------------------------")
    log.info("🛠️ STARTING PRE-CAMPAIGN CALIBRATION")
    GlobalSys.set(SysState.KALIBRATING)
    try:
        await asyncio.sleep(5) 
        log.info("✅ CALIBRATION COMPLETE")
    except Exception as e:
        log.error(f"❌ Error during calibration: {e}")
    log.info("--------------------------------")

# --- 1. REALTIME LOGIC (STICKY MODE WITH ROTATION) ---
async def run_realtime_logic(client, store) -> int:
    log.info("[REALTIME] Entering Sticky Mode...")
    if not GlobalSys.is_idle(): return 1
    
    # 1. Initial Probe
    next_config, _, delta_t_ms = fetch_realtime_config(client)
    if not next_config:
        return 0

    # 2. Lock State and Initialize Rotation Timer
    GlobalSys.set(SysState.REALTIME)
    store.add_to_persistent("delta_t_ms", delta_t_ms)
    
    # Force break after 5 minutes (300s) to allow Campaign checks
    timer_force_rotation = ElapsedTimer()
    timer_force_rotation.init_count(300) 

    controller = ZmqPairController(addr=cfg.IPC_ADDR, is_server=True, verbose=False)

    try:
        async with controller as zmq_ctrl:
            log.info("[REALTIME] Connection established. Max session duration: 5m.")
            
            while True:
                # A. Check for 5-minute rotation
                if timer_force_rotation.time_elapsed():
                    log.info("[REALTIME] Periodic rotation triggered. Re-evaluating system state.")
                    break

                # B. Data Acquisition Cycle
                await zmq_ctrl.send_command(next_config)
                try:
                    raw_payload = await asyncio.wait_for(zmq_ctrl.wait_for_data(), timeout=10)
                    data_dict = format_data_for_upload(raw_payload)
                    
                    # Upload (Non-fatal if network blips during post)
                    rc, resp = client.post_json("/data", data_dict)
                    if rc != 0:
                        log.warning(f"[REALTIME] Upload failed (RC {rc}).")
                except asyncio.TimeoutError:
                    log.warning("[REALTIME] C-Engine Timeout.")

                # C. Config Heartbeat (1-strike fail logic)
                new_conf, _, dt = fetch_realtime_config(client)
                if not new_conf:
                    log.error("[REALTIME] Config fetch failed or Stop command received. Breaking immediately.")
                    break 
                
                # Update config for next acquisition loop
                next_config = new_conf
                store.add_to_persistent("delta_t_ms", dt)

                await asyncio.sleep(0.1)

    except Exception as e:
        log.error(f"[REALTIME] Critical loop error: {e}")
    finally:
        log.info("[REALTIME] Reverting to IDLE.")
        GlobalSys.set(SysState.IDLE)
    
    return 0

# --- 2. CAMPAIGN LOGIC ---
async def run_campaigns_logic(client, store, scheduler) -> int:
    log.info("[CAMPAIGN] Checking for scheduled campaigns...")
    if not GlobalSys.is_idle(): return 1

    try:
        rc, resp = client.get(cfg.CAMPAIGN_URL)
        if rc != 0: return 1
        
        camps_arr = resp.json().get("campaigns", [])
        if not camps_arr: return 1

        is_active = scheduler.sync_jobs(camps_arr, cfg.get_time_ms(), store)
        
        if is_active:
            await _perform_calibration_sequence(store)
            GlobalSys.set(SysState.CAMPAIGN)
            
            while True:
                await asyncio.sleep(cfg.INTERVAL_REQUEST_CAMPAIGNS_S)
                rc, resp = client.get(cfg.CAMPAIGN_URL)
                if rc != 0: break 
                
                camps_arr = resp.json().get("campaigns", [])
                if not scheduler.sync_jobs(camps_arr, cfg.get_time_ms(), store):
                    log.info("[CAMPAIGN] Window closed. Exiting campaign mode.")
                    break
    except Exception as e:
        log.error(f"[CAMPAIGN] Error: {e}")
    finally:
        GlobalSys.set(SysState.IDLE)
    return 0

# --- 3. MAIN LOOP ---
async def main() -> int:
    time.sleep(5) 
    store = ShmStore()
    client = RequestClient(cfg.API_URL, mac_wifi=cfg.get_mac(), timeout=(5, 15), verbose=True, logger=log)
    scheduler = CronSchedulerCampaign(
        poll_interval_s=cfg.INTERVAL_REQUEST_CAMPAIGNS_S, 
        python_env=cfg.PYTHON_ENV_STR,
        cmd=str((cfg.PROJECT_ROOT / "campaign_runner.py").absolute()), 
        logger=log
    )
    
    tim_check_realtime = ElapsedTimer()
    tim_check_campaign = ElapsedTimer()
    
    tim_check_realtime.init_count(cfg.INTERVAL_REQUEST_REALTIME_S)
    tim_check_campaign.init_count(cfg.INTERVAL_REQUEST_CAMPAIGNS_S)
    
    log.info("Orchestrator online. Monitoring tasks...")

    while True:
        # Check Realtime
        if GlobalSys.is_idle() and tim_check_realtime.time_elapsed():
            await run_realtime_logic(client, store)
            tim_check_realtime.init_count(cfg.INTERVAL_REQUEST_REALTIME_S)

        # Check Campaigns
        if GlobalSys.is_idle() and tim_check_campaign.time_elapsed():
            await run_campaigns_logic(client, store, scheduler)
            tim_check_campaign.init_count(cfg.INTERVAL_REQUEST_CAMPAIGNS_S)

        await asyncio.sleep(0.1)
    return 0

if __name__ == "__main__":
    rc = cfg.run_and_capture(main)
    sys.exit(rc)