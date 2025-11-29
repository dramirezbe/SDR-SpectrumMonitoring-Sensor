from utils import CampaignHackRF
import cfg
log = cfg.set_logger()

import sys
import socketio
import time
import numpy as np

class PersistentSensor:
    def __init__(self, server_url):
        self.server_url = server_url
        self.sio = None # Will be created fresh every cycle

    def setup_client(self):
        """Creates a fresh SocketIO instance and binds events."""
        self.sio = socketio.Client(reconnection=False, request_timeout=10)

        # --- EVENT HANDLERS (Defined inside to access self.sio) ---
        
        @self.sio.event
        def connect():
            log.info(">>> CONNECTED to Master Server")

        @self.sio.event
        def disconnect():
            log.info("xxx DISCONNECTED from Master Server")

        @self.sio.event
        def server_ack(data):
            log.info(f"[ACK] {data}. Waiting for next job...")

        @self.sio.on('configure_sensor')
        def on_configure_sensor(config):
            self.handle_job(config)

    def handle_job(self, config):
        """The logic to run the HackRF and send data."""
        start_freq = config['start_freq_hz']
        end_freq = config['end_freq_hz']
        # Extract other configs
        resolution = config.get('rbw_hz', 10000)
        sample_rate = config.get('sample_rate_hz', 20000000)
        scale = config.get('scale', 'dBm')

        log.info(f"JOB: {start_freq/1e6} MHz -> {end_freq/1e6} MHz")

        # 1. Acquire
        hack = CampaignHackRF(
            start_freq_hz=start_freq, 
            end_freq_hz=end_freq, 
            sample_rate_hz=sample_rate, 
            resolution_hz=resolution, 
            window=config.get('window', 'hamming'), 
            overlap=config.get('overlap', 0.5), 
            lna_gain=config.get('lna_gain', 0), 
            vga_gain=config.get('vga_gain', 0), 
            antenna_amp=config.get('antenna_amp', False),
            r_ant=50.0, 
            verbose=True, 
            scale=scale
        )

        _, pxx = hack.get_psd()    
        
        if pxx is None:
            log.error("Acquisition failed.")
            return

        # 2. Check Connection before sending
        if not self.sio.connected:
            log.warning("Lost connection during job. Discarding result.")
            return

        # 3. Send
        payload = {
            "timestamp": time.time(),
            "start_freq_hz": start_freq,
            "end_freq_hz": end_freq,
            "Pxx": pxx.tolist() 
        }
        
        try:
            log.info(">>> Sending Results...")
            self.sio.emit('sensor_reading', payload)
        except Exception as e:
            log.error(f"Emit failed: {e}")

    def run_forever(self):
        log.info("--- Starting Immortal Client ---")
        
        while True:
            try:
                # 1. Create a FRESH instance (Prevents Segfaults)
                self.setup_client()
                
                # 2. Try to connect
                log.info(f"Connecting to {self.server_url}...")
                self.sio.connect(self.server_url)
                
                # 3. Block here while connected
                while self.sio.connected:
                    time.sleep(1)
                
                # If we get here, we disconnected naturally
                log.info("Connection lost. Restarting cycle...")

            except socketio.exceptions.ConnectionError:
                log.info("Server not found. Retrying in 5s...")
            except KeyboardInterrupt:
                log.info("Stopping...")
                if self.sio and self.sio.connected:
                    self.sio.disconnect()
                break
            except Exception as e:
                log.error(f"Unexpected error: {e}")
            
            # 4. Clean up before retry
            if self.sio:
                try:
                    self.sio.disconnect()
                except:
                    pass
                self.sio = None # Destroy object
            
            time.sleep(5) # Wait before rebuilding

if __name__ == "__main__":
    # Point this to your server IP
    client = PersistentSensor('http://localhost:5000')
    
    # Run and capture logs using your utility
    rc = cfg.run_and_capture(client.run_forever, cfg.LOG_FILES_NUM)
    sys.exit(rc)