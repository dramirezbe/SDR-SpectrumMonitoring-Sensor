import cfg
log = cfg.set_logger()
from utils import RequestClient, ZmqPairController, ServerRealtimeConfig, ShmStore
from functions import format_data_for_upload

import asyncio
from dataclasses import asdict
from enum import Enum, auto
import time

def fetch_realtime_config(client):
    """
    Fetches job configuration from /realtime and validates it using ServerJobConfig.
    """
    delta_t_ms = 0 

    try:
        start_delta_t = time.perf_counter()
        rc_returned, resp = client.get("/realtime")
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
                lna_gain=0,
                vga_gain=int(json_payload.get("vga_gain")),
                antenna_amp=True,
                antenna_port=1, #===========Changed from int(json_payload.get("antenna_port"))=========================
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



# --- 3. Main Server Loop ---
async def run_server():
    log.info("Starting ZmqPairController server loop...")
    store = ShmStore()
    
    zmq_ctrl = ZmqPairController(addr=cfg.IPC_ADDR, is_server=True, verbose=False)
    await asyncio.sleep(0.5)
    
    client = RequestClient(cfg.API_URL, mac_wifi=cfg.get_mac(), timeout=(5, 15), verbose=True, logger=log)
    
    # "Never Die" Loop
    while True:
        try:
            log.info("Fetching job configuration...")
            c_config, _, delta_t_ms = fetch_realtime_config(client)
            store.add_to_persistent("delta_t_ms", delta_t_ms)

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
            await zmq_ctrl.send_command(c_config)
            
            log.info("Waiting for PSD data from C engine...")

            try:
                # 2. Wait for Data
                raw_payload = await asyncio.wait_for(zmq_ctrl.wait_for_data(), timeout=10)
                
                # 3. Format
                data_dict = format_data_for_upload(raw_payload)
                
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