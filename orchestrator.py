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

# --- (fetch_realtime_config remains unchanged) ---
def fetch_realtime_config(client):
    """
    Fetches job configuration from /realtime and validates it using ServerJobConfig.
    """
    delta_t_ms = 0 
    try:
        start_delta_t = time.perf_counter()
        rc_returned, resp = client.get(cfg.REALTIME_URL)
        end_delta_t = time.perf_counter()
        delta_t_ms = int((end_delta_t - start_delta_t) * 1000)
        
        json_payload = {}
        if resp is None or resp.status_code != 200:
            return {}, resp, delta_t_ms 
        
        try:
            json_payload = resp.json()
        except Exception:
            return {}, resp, delta_t_ms 
        
        if not json_payload:
            return {}, resp, delta_t_ms 

        # --- VALIDATION STEP ---
        try:
            config_obj = ServerRealtimeConfig(
                rf_mode="realtime",
                center_freq_hz=int(json_payload.get("center_freq_hz")), 
                sample_rate_hz=int(json_payload.get("sample_rate_hz")),
                rbw_hz=int(json_payload.get("rbw_hz")),
                window=json_payload.get("window"),
                scale=json_payload.get("scale"),
                overlap=float(json_payload.get("overlap")),
                lna_gain=16,
                vga_gain=30,
                antenna_amp=bool(json_payload.get("antenna_amp")),
                antenna_port=1, 
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

# --- HELPER: CALIBRATION SEQUENCE ---
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

# --- 1. REALTIME TRAP (Updated with async with) ---
async def run_realtime_logic(client, store) -> int:
    log.info("[REALTIME] Entering Loop...")
    if not GlobalSys.is_idle(): return 1
    
    if store.consult_persistent("campaign_runner_running"):
        return 1
    
    # 1. PROBE: Fetch config BEFORE changing state or opening sockets
    # This prevents the "Flapping" (Idle -> Realtime -> Error -> Idle) loop.
    next_config, _, delta_t_ms = fetch_realtime_config(client)

    if not next_config:
        # No valid work to do. Remain IDLE. Return 0 to indicate clean pass.
        return 0

    # 2. Commit to Realtime Mode
    GlobalSys.set(SysState.REALTIME)
    store.add_to_persistent("delta_t_ms", delta_t_ms)

    # 3. Instantiate Controller
    controller = ZmqPairController(addr=cfg.IPC_ADDR, is_server=True, verbose=False)

    try:
        async with controller as zmq_ctrl:
            log.info(f"[REALTIME] Valid config ({next_config.get('center_freq_hz')}Hz). Entering Stream.")
            
            while True:
                # A. Send the config we have (either from the probe or the previous loop end)
                await zmq_ctrl.send_command(next_config)

                try:
                    # B. Wait for Data
                    raw_payload = await asyncio.wait_for(zmq_ctrl.wait_for_data(), timeout=10)
                    data_dict = format_data_for_upload(raw_payload)
                    
                    # C. Upload
                    rc, resp = client.post_json("/data", data_dict)
                    if rc != 0:
                        log.error(f"[REALTIME] Upload failed: {resp}")

                except asyncio.TimeoutError:
                    log.warning("[REALTIME] TIMEOUT from C-Engine.")

                # D. Fetch Config for the NEXT iteration
                # We do this at the end to decide if we stay in the loop
                next_config, _, delta_t_ms = fetch_realtime_config(client)
                store.add_to_persistent("delta_t_ms", delta_t_ms)

                if not next_config:
                    log.warning("[REALTIME] Stop Command or Invalid Config. Exiting Loop.")
                    break 

                await asyncio.sleep(0.1) 

    except Exception as e:
        log.error(f"[REALTIME] Critical Error: {e}")
    
    finally:
        log.info("[REALTIME] Exited loop. State -> IDLE")
        GlobalSys.set(SysState.IDLE)
    
    return 0

# --- 2. CAMPAIGN TRAP (Unchanged) ---
async def run_campaigns_logic(client, store, scheduler) -> int:
    log.info("[CAMPAIGN] Entering Loop...")
    if not GlobalSys.is_idle():
        return 1

    try:
        rc, resp = client.get(cfg.CAMPAIGN_URL)
        if rc != 0: return 1
        
        camps_arr = resp.json().get("campaigns", [])
        #verbosity here debugging
        log.info(f"camps_arr: {camps_arr}")
        if not camps_arr: return 1

        is_active = scheduler.sync_jobs(camps_arr, cfg.get_time_ms(), store)
        
        if is_active:
            await _perform_calibration_sequence(store)
            GlobalSys.set(SysState.CAMPAIGN)
            
            while True:
                log.info("[CAMPAIGN] System Frozen in Campaign Mode...")
                await asyncio.sleep(cfg.INTERVAL_REQUEST_CAMPAIGNS_S)
                
                rc, resp = client.get(cfg.CAMPAIGN_URL)
                if rc != 0: break 
                
                camps_arr = resp.json().get("campaigns", [])
                still_active = scheduler.sync_jobs(camps_arr, cfg.get_time_ms(), store)
                
                if not still_active:
                    log.info("[CAMPAIGN] Window closed. Exiting Freeze.")
                    break
        else:
            pass

    except Exception as e:
        log.error(f"[CAMPAIGN] Error: {e}")

    finally:
        GlobalSys.set(SysState.IDLE)

    return 0

# --- 3. MAIN LOOP (Updated) ---
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
    
    log.info("System Initialized. Entering Main Loop.")

    while True:
        if GlobalSys.is_idle() and tim_check_realtime.time_elapsed():
            # Updated signature (no zmq_ctrl arg)
            await run_realtime_logic(client, store)
            tim_check_realtime.init_count(cfg.INTERVAL_REQUEST_REALTIME_S)

        if GlobalSys.is_idle() and tim_check_campaign.time_elapsed():
            await run_campaigns_logic(client, store, scheduler)
            tim_check_campaign.init_count(cfg.INTERVAL_REQUEST_CAMPAIGNS_S)

        await asyncio.sleep(0.1)
        
    return 0

if __name__ == "__main__":
    rc = cfg.run_and_capture(main)
    sys.exit(rc)