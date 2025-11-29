from utils import WelchEstimator, CampaignHackRF
import cfg
log = cfg.set_logger()

import sys
import socketio
import time
import numpy as np

# Enable built-in reconnection logic
sio = socketio.Client(reconnection=True, reconnection_delay=2)

@sio.event
def connect():
    log.info(">>> CONNECTED to Master Server")

@sio.event
def disconnect():
    log.info("xxx DISCONNECTED from Master Server")

@sio.event
def configure_sensor(config):
    start_freq = config['start_freq_hz']
    end_freq = config['end_freq_hz']
    resolution = config['rbw_hz']
    port = config['antenna_port'] 
    window = config['window']
    overlap = config['overlap']
    sample_rate = config['sample_rate_hz']
    lna_gain = config['lna_gain']
    vga_gain = config['vga_gain']
    antenna_amp = config['antenna_amp']
    span = config['span_hz']
    scale = config['scale']

    log.info("ACQUIRING WITH")
    log.info("-------------")
    log.info(f"Start Freq: {start_freq}")
    log.info(f"End Freq: {end_freq}")
    log.info(f"Resolution: {resolution}")
    log.info(f"Sample Rate: {sample_rate}")
    log.info(f"Window: {window}")
    log.info(f"Overlap: {overlap}")
    log.info(f"lna_gain: {lna_gain}")
    log.info(f"vga_gain: {vga_gain}")
    log.info(f"antenna_amp: {antenna_amp}")
    log.info("-------------")

    # Note: 'with_shift' is removed as the new class always returns (f, Pxx)
    hack = CampaignHackRF(start_freq_hz=start_freq, end_freq_hz=end_freq, 
                         sample_rate_hz=sample_rate, resolution_hz=resolution, 
                         window=window, overlap=overlap, lna_gain=lna_gain, 
                         vga_gain=vga_gain, antenna_amp=antenna_amp,
                         r_ant=50.0, verbose=True, scale=scale)

    # --- FIX: Unpack the tuple (freqs, pxx) ---
    _, pxx = hack.get_psd()    
    
    # Check if acquisition failed
    if pxx is None:
        log.error("Acquisition failed (hack.get_psd returned None)")
        return

    payload = {
        "timestamp": time.time(),
        "start_freq_hz": config['start_freq_hz'],
        "end_freq_hz": config['end_freq_hz'],
        "Pxx": pxx.tolist() # Now calling tolist() on the numpy array
    }
    
    log.info(">>> Sending Results to Server")
    sio.emit('sensor_reading', payload)

@sio.event
def server_ack(data):
    log.info(f"[ACK] {data}. Waiting for next job...")

def main() -> int:
    server_url = 'http://10.182.143.246:5000'
    
    # Infinite Connection Loop
    while True:
        try:
            if not sio.connected:
                log.info(f"Attempting to connect to {server_url}...")
                sio.connect(server_url)
                sio.wait() # Blocks here until disconnected
            else:
                time.sleep(1)
                
        except socketio.exceptions.ConnectionError:
            log.info("Server not found. Retrying in 2 seconds...")
            time.sleep(2)
        except KeyboardInterrupt:
            log.info("User stopped script.")
            break
        except Exception as e:
            log.info(f"Unexpected error: {e}. Retrying...")
            time.sleep(2)
    return 0

if __name__ == "__main__":
    rc = cfg.run_and_capture(main, cfg.LOG_FILES_NUM)
    sys.exit(rc)