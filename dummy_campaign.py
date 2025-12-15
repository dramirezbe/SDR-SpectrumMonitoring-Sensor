import cfg
log = cfg.set_logger()
from utils import RequestClient, ZmqPairController, ShmStore
from functions import format_data_for_upload

import asyncio
from dataclasses import asdict
from enum import Enum, auto
import time
import datetime

class SysState(Enum):
    IDLE = auto()
    CAMPAIGN = auto()
    REALTIME = auto()
    CALIBRATING = auto()

def fetch_realtime_config(client):
    """
    Fetches job configuration from /realtime and validates it using ServerJobConfig.
    """
    delta_t_ms = 0 

    try:
        rc_returned, resp = client.get(cfg.CAMPAIGN_URL)
        
        json_payload = {}
        
        if resp is None or resp.status_code != 200:
            return {}, resp, delta_t_ms, None  
        
        try:
            json_payload = resp.json()
            json_payload = json_payload.get("campaigns")[-1]
            log.info(f"json_payload: {json_payload}")
        except Exception:
            return {}, resp, delta_t_ms, None 
        
        if not json_payload:
            return {}, resp, delta_t_ms, None  

        # --- VALIDATION STEP ---
        try:
            # Instantiate Dataclass
            # Instantiate Dataclass
            camp_id = int(json_payload.get("campaign_id"))
            log.info(f"CAMP_ID: {camp_id}")
            
            # FIX: Do not cast this dict to int()
            timeframe = json_payload.get("timeframe") 
            
            # Now access the keys inside the dict
            local_dt_start = datetime.datetime.fromtimestamp(int(timeframe.get("start")) / 1000)
            local_dt_end = datetime.datetime.fromtimestamp(int(timeframe.get("end")) / 1000)
            
            human_time_start = local_dt_start.strftime('%Y-%m-%d %H:%M:%S')
            human_time_end = local_dt_end.strftime('%Y-%m-%d %H:%M:%S')

            log.info(f"TIME START: {human_time_start}")
            log.info(f"TIME END: {human_time_end}")

            config_obj = {
                "rf_mode": "campaign",
                "center_freq_hz": int(json_payload.get("center_freq_hz")),
                "span": int(json_payload.get("span")),
                "sample_rate_hz": int(json_payload.get("sample_rate_hz")),
                "rbw_hz": int(json_payload.get("rbw_hz")),
                "overlap": float(json_payload.get("overlap")),
                "window": json_payload.get("window"),
                "scale": json_payload.get("scale"),
                "lna_gain": int(json_payload.get("lna_gain")),
                "vga_gain": int(json_payload.get("vga_gain")),
                "antenna_amp": bool(json_payload.get("antenna_amp")),
                "antenna_port": int(json_payload.get("antenna_port")),
                "ppm_error": 0,
            }
            log.info(f"config_obj: {config_obj}")

            return config_obj, resp, delta_t_ms, camp_id

        except (ValueError, TypeError) as val_err:
            log.error(f"VALIDATION ERROR: {val_err}")
            return {}, resp, delta_t_ms, None  

    except Exception as e:
        log.error(f"Error fetching config: {e}")
        return {}, None, 0, None 



# --- 3. Main Server Loop ---
async def run_server():
    log.info("Starting ZmqPairController server loop...")
    
    zmq_ctrl = ZmqPairController(addr=cfg.IPC_ADDR, is_server=True, verbose=False)
    await asyncio.sleep(0.5)
    
    client = RequestClient(cfg.API_URL, mac_wifi=cfg.get_mac(), timeout=(5, 15), verbose=True, logger=log)
    
    # "Never Die" Loop
    while True:
        try:
            log.info("Fetching job configuration...")
            c_config, _, _, camp_id = fetch_realtime_config(client)

            # --- CHECK: If validation failed or HTTP failed ---
            if not c_config:
                log.warning("Bad Config or Connection Error. Sleeping 5s before retrying...")
                await asyncio.sleep(5)
                continue # Jumps back to 'while True'

            # --- LOGGING PARAMS ---
            log.info("----SERVER PARAMS-----")
            for key, val in c_config.items():
                log.info(f"{key:<18}: {val}")
            
            # 1. Send Command
            time.sleep(60) #1 min blocked
            await zmq_ctrl.send_command(c_config)
            
            log.info("Waiting for PSD data from C engine...")

            try:
                # 2. Wait for Data
                raw_payload = await asyncio.wait_for(zmq_ctrl.wait_for_data(), timeout=10)
                
                # 3. Format
                data_dict = format_data_for_upload(raw_payload)
                data_dict['campaign_id'] = camp_id
                
                
                # --- LOGGING DATA ---
                log.info("----DATATOSEND--------")
                final_pxx = data_dict.get('Pxx', [])
                pxx_preview = final_pxx[:5] if isinstance(final_pxx, list) else []
                log.info(f"Pxx (First 5)     : {pxx_preview}")
                log.info("----------------------")

                # 4. Upload
                client.post_json("/data", data_dict)

            except asyncio.TimeoutError:
                log.warning("TIMEOUT: No data from C-Engine. Retrying...")
                continue 
            
        except Exception as e:
            # BROAD SAFETY NET: Catches anything else to prevent script death
            log.error(f"CRITICAL LOOP ERROR: {e}")
            log.info("Sleeping 5s to recover...")
            await asyncio.sleep(5)

if __name__ == "__main__":
    try:
        asyncio.run(run_server())
    except KeyboardInterrupt:
        pass