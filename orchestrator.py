#!/usr/bin/env python3
#orchestrator.py

import cfg
log = cfg.set_logger()
from utils import RequestClient, ZmqPairController, ServerRealtimeConfig, ShmStore,ElapsedTimer
from functions import format_data_for_upload, CronSchedulerCampaign, GlobalSys, SysState

import asyncio
from dataclasses import asdict
import time

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
            log.info(f"json_payload: {json_payload}")
        except Exception:
            return {}, resp, delta_t_ms 
        
        if not json_payload:
            return {}, resp, delta_t_ms 

        # --- VALIDATION STEP ---
        try:
            # Instantiate Dataclass
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
                antenna_port=1, #===========Change it from int(json_payload.get("antenna_port"))=========================
                span=int(json_payload.get("span")),
                ppm_error=0
            )
            log.info(f"config_obj: {config_obj}")

            return asdict(config_obj), resp, delta_t_ms

        except (ValueError, TypeError) as val_err:
            log.error(f"VALIDATION ERROR: {val_err}")
            return {}, resp, delta_t_ms 

    except Exception as e:
        log.error(f"Error fetching config: {e}")
        return {}, None, 0
    
# --- 1. REALTIME TRAP ---
async def run_realtime_logic(client, store, zmq_ctrl) -> int:
    """
    Enters REALTIME state and FREEZES here until an invalid command occurs.
    """
    if not GlobalSys.is_idle():
        return 1
    
    GlobalSys.set(SysState.REALTIME)
    
    try:
        # TRAP LOOP: We stay here forever until 'break'
        while True:
            log.info("[REALTIME] Fetching configuration...")
            c_config, _, delta_t_ms = fetch_realtime_config(client)
            store.add_to_persistent("delta_t_ms", delta_t_ms)

            # EXIT CONDITION: Invalid Command / Empty Config
            if not c_config:
                log.warning("[REALTIME] ⛔ Invalid Config or Stop Command. Exiting Freeze.")
                break 

            # Process Valid Command
            log.info(f"[REALTIME] Processing valid config...")
            await zmq_ctrl.send_command(c_config)

            try:
                raw_payload = await asyncio.wait_for(zmq_ctrl.wait_for_data(), timeout=10)
                data_dict = format_data_for_upload(raw_payload)
                
                rc, resp = client.post_json("/data", data_dict)
                if rc != 0:
                    log.error(f"[REALTIME] Upload failed: {resp}")
                    # Decide: Do we exit on upload fail? Or retry? 
                    # Usually, upload fail doesn't mean invalid command, so we might continue.
                    # But if you want strict exit:
                    # break 

            except asyncio.TimeoutError:
                log.warning("[REALTIME] TIMEOUT from C-Engine.")
                # Timeout might be temporary, but if you want strict exit:
                # break

            # Sleep slightly to avoid hammering if fetch is instant
            await asyncio.sleep(0.1) 

    except Exception as e:
        log.error(f"[REALTIME] Critical Error: {e}")
    
    finally:
        # ALWAYS RELEASE LOCK
        GlobalSys.set(SysState.IDLE)
    
    return 0

# --- 2. CAMPAIGN TRAP ---
async def run_campaigns_logic(client, store, scheduler) -> int:
    """
    Enters CAMPAIGN state and FREEZES here until no campaigns are active.
    """
    if not GlobalSys.is_idle():
        return 1

    # We don't set state yet; we check if there is actual work first.
    
    try:
        # Check Initial Status
        rc, resp = client.get(cfg.CAMPAIGN_URL)
        if rc != 0: return 1
        
        camps_arr = resp.json().get("campaigns", [])
        if not camps_arr: return 1

        # Check if we are in a window
        is_active = scheduler.sync_jobs(camps_arr, cfg.get_time_ms(), store)
        
        if is_active:
            GlobalSys.set(SysState.CAMPAIGN)
            
            # TRAP LOOP: Stay here while active
            while True:
                log.info("[CAMPAIGN] System Frozen in Campaign Mode...")
                
                # Sleep for poll interval (e.g. 10s) before checking again
                await asyncio.sleep(cfg.INTERVAL_REQUEST_CAMPAIGNS_S)
                
                # Re-fetch to see if cancelled or finished
                rc, resp = client.get(cfg.CAMPAIGN_URL)
                if rc != 0: break # API Error -> Release lock
                
                camps_arr = resp.json().get("campaigns", [])
                
                # Update Scheduler & Check Window
                still_active = scheduler.sync_jobs(camps_arr, cfg.get_time_ms(), store)
                
                # EXIT CONDITION: No campaigns in window
                if not still_active:
                    log.info("[CAMPAIGN] Window closed. Exiting Freeze.")
                    break
        else:
            log.info("Campaigns exist but currently outside window.")

    except Exception as e:
        log.error(f"[CAMPAIGN] Error: {e}")

    finally:
        if GlobalSys.current == SysState.CAMPAIGN:
            GlobalSys.set(SysState.IDLE)

    return 0

# --- 3. CALIBRATION ---
async def run_kalibrate_logic(store)->int:
    # Only runs if explicitly called by Main Loop idle check
    if not GlobalSys.is_idle(): return 1
    
    GlobalSys.set(SysState.KALIBRATING)
    try:
        log.info("[CALIB] Starting Calibration (System Frozen)...")
        # Simulate blocking work or async work
        await asyncio.sleep(5) 
        log.info("[CALIB] Calibration Done.")
    finally:
        GlobalSys.set(SysState.IDLE)
    return 0

# --- 4. MAIN LOOP ---
async def run_server():
    time.sleep(5) 
    store = ShmStore()
    client = RequestClient(cfg.API_URL, mac_wifi=cfg.get_mac(), timeout=(5, 15), verbose=True, logger=log)
    scheduler = CronSchedulerCampaign(
        poll_interval_s=cfg.INTERVAL_REQUEST_CAMPAIGNS_S, 
        python_env=cfg.PYTHON_ENV_STR,
        cmd=str((cfg.PROJECT_ROOT / "campaign_runner.py").absolute()), 
        logger=log
    )
    zmq_ctrl = ZmqPairController(addr=cfg.IPC_ADDR, is_server=True, verbose=False)
    
    # Timers for checking availability
    # Note: We don't rely on timers for 'duration' of states anymore, 
    # only for how often we check "Are there orders?"
    tim_check_realtime = ElapsedTimer()
    tim_check_campaign = ElapsedTimer()
    
    tim_check_realtime.init_count(cfg.INTERVAL_REQUEST_REALTIME_S)
    tim_check_campaign.init_count(cfg.INTERVAL_REQUEST_CAMPAIGNS_S)
    
    # IDLE TIMER
    last_busy_time = time.time()
    
    log.info("System Initialized. Entering Main Loop.")

    while True:
        # 1. Attempt Realtime (High Priority)
        # If this enters, it TRAPS until invalid command.
        if GlobalSys.is_idle() and tim_check_realtime.time_elapsed():
            await run_realtime_logic(client, store, zmq_ctrl)
            tim_check_realtime.init_count(cfg.INTERVAL_REQUEST_REALTIME_S)
            
            # If we just came back from Realtime, update busy time
            if not GlobalSys.is_idle(): 
                 # This check handles the moment logic returns but state isn't swapped yet (unlikely due to finally)
                 # Actually, simpler: we update last_busy_time at the end of loop if IDLE.
                 pass

        # 2. Attempt Campaign
        # If this enters, it TRAPS until window closes.
        if GlobalSys.is_idle() and tim_check_campaign.time_elapsed():
            await run_campaigns_logic(client, store, scheduler)
            tim_check_campaign.init_count(cfg.INTERVAL_REQUEST_CAMPAIGNS_S)

        # 3. Handle Idle / Calibration
        if not GlobalSys.is_idle():
            # If we are NOT idle (which shouldn't happen here because functions trap, 
            # but good for safety), reset timer.
            last_busy_time = time.time()
        else:
            # We are IDLE. How long?
            idle_duration = time.time() - last_busy_time
            if idle_duration > 60:
                log.info(f"System IDLE for {idle_duration:.1f}s. Triggering Calibration.")
                await run_kalibrate_logic(store)
                last_busy_time = time.time() # Reset after calib

        await asyncio.sleep(0.1)

if __name__ == "__main__":
    try:
        asyncio.run(run_server())
    except KeyboardInterrupt:
        pass