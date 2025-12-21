#!/usr/bin/env python3
# orchestrator.py

import cfg
log = cfg.set_logger()
from utils import (
    RequestClient, ZmqPairController, ServerRealtimeConfig, 
    FilterConfig, DemodulationConfig, ShmStore, ElapsedTimer
)
from functions import (
    format_data_for_upload, CronSchedulerCampaign, GlobalSys, 
    SysState, SimpleDCSpikeCleaner, AcquireRealtime
)

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

        #Debugging
        log.info(f"json_payload: {json_payload}")

        try:
            filter_data = json_payload.get("filter")
            demodulation_data = json_payload.get("demodulation")
            demodulation_obj = None
            filter_obj = None

            if demodulation_data:
                demodulation_obj = DemodulationConfig(
                    type=demodulation_data.get("type"),
                    bw_hz=int(demodulation_data.get("bw_hz"))
                )
            
            if filter_data:
                filter_obj = FilterConfig(
                    type=filter_data.get("type"),
                    bw_hz=int(filter_data.get("bw_hz")),
                    order=int(filter_data.get("order"))
                )

            config_obj = ServerRealtimeConfig(
                method_psd="pfb",
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
                ppm_error=0,
                demodulation=demodulation_obj,
                filter=filter_obj
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

# --- 1. REALTIME LOGIC (WITH OFFSET & CROP) ---
async def run_realtime_logic(client, store) -> int:
    log.info("[REALTIME] Entering Sticky Mode (Offset & Crop enabled)...")
    if not GlobalSys.is_idle(): return 1
    
    # 1. Initial Probe
    next_config, _, delta_t_ms = fetch_realtime_config(client)
    if not next_config:
        return 0
    
    # 2. Lock State
    GlobalSys.set(SysState.REALTIME)
    store.add_to_persistent("delta_t_ms", delta_t_ms)
    
    timer_force_rotation = ElapsedTimer()
    timer_force_rotation.init_count(300) 

    controller = ZmqPairController(addr=cfg.IPC_ADDR, is_server=True, verbose=False)
    cleaner = SimpleDCSpikeCleaner(search_frac=0.05, width_frac=0.005, neighbor_bins=2)

    try:
        async with controller as zmq_ctrl:
            # Initialize the specialized acquirer
            acquirer = AcquireRealtime(
                controller=zmq_ctrl, 
                cleaner=cleaner,
                hardware_max_bw=20_000_000, 
                user_safe_bw=18_000_000
            )

            log.info("[REALTIME] Connection established. Processing stream...")
            
            while True:
                if timer_force_rotation.time_elapsed():
                    log.info("[REALTIME] Periodic rotation triggered.")
                    break

                #StateMachine Realtime
                is_demod = bool(next_config.get("demodulation"))

                if is_demod:
                    dsp_payload = await acquirer.acquire_raw(next_config)
                else:
                    dsp_payload = await acquirer.acquire_with_offset(next_config)
                
                if dsp_payload:
                    final_payload = format_data_for_upload(dsp_payload)

                    rc, _ = client.post_json(cfg.DATA_URL, final_payload)
                    if rc != 0:
                        log.warning(f"[REALTIME] Upload failed (RC {rc}).")
                else:
                    log.warning("[REALTIME] Acquisition timeout or DSP error.")

                # --- STEP D: Heartbeat / Config Update ---
                new_conf, _, dt = fetch_realtime_config(client)
                if not new_conf:
                    log.error("[REALTIME] Stop command received. Breaking.")
                    break 
                
                next_config = new_conf
                store.add_to_persistent("delta_t_ms", dt)

                await asyncio.sleep(0.05)

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
    time.sleep(1) 
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
        if GlobalSys.is_idle() and tim_check_realtime.time_elapsed():
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