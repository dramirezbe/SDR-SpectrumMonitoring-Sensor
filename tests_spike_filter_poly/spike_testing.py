import cfg
log = cfg.set_logger()
from utils import ZmqPub, ZmqSub
import asyncio
import numpy as np
import matplotlib.pyplot as plt

# Topics
topic_data = "data"
topic_sub = "acquire"

async def run_rbw_sweep():
    log.info("Starting RBW Sweep & DC Spike Analyzer...")
    
    # 
    # Visualizing the DC spike relative to the noise floor is crucial for this logic.

    # 1. Setup ZMQ
    pub = ZmqPub(addr=cfg.IPC_CMD_ADDR)
    sub = ZmqSub(addr=cfg.IPC_DATA_ADDR, topic=topic_data)

    # 2. Setup Sweep Parameters
    # We sweep from 300 Hz to 1 MHz
    rbw_values = np.geomspace(300, 1000000, 100)
    
    # Storage for results
    results_rbw = []
    results_dc_width_hz = []
    results_occupancy_pct = []
    
    # 3. Setup Live Plotting (Snapshots)
    plt.ion() # Interactive mode on
    fig_snap, ax_snap = plt.subplots(figsize=(10, 5))
    ax_snap.set_facecolor('black')
    fig_snap.canvas.manager.set_window_title("Live Snapshot: Algorithm View")

    # Base Configuration
    base_config = {
        "rf_mode": "realtime",
        "center_freq_hz": 3000000000,
        "span": 20000000, # 20 MHz
        "window": "hamming",
        "overlap": 0.5,
        "sample_rate_hz": 20000000,
        "lna_gain": 0,
        "vga_gain": 0,
        "antenna_amp": False,
        "antenna_port": 1,
        "scale": "dBm",
        "ppm_error": 0
    }

    # Wait for ZMQ warmup
    await asyncio.sleep(0.5)

    try:
        for i, current_rbw in enumerate(rbw_values):
            # --- A. Update Config ---
            current_rbw = int(current_rbw)
            base_config["rbw_hz"] = current_rbw
            
            log.info(f"[{i+1}/{len(rbw_values)}] Sending Config -> RBW: {current_rbw} Hz")
            pub.public_client(topic_sub, base_config)
            
            # Pause for settlement
            await asyncio.sleep(0.2) 

            # --- B. Acquire Data ---
            try:
                raw_data = await asyncio.wait_for(sub.wait_msg(), timeout=5)
            except asyncio.TimeoutError:
                log.warning(f"Timeout waiting for data at RBW {current_rbw}")
                continue

            raw_pxx = np.array(raw_data.get("Pxx", []))
            start_freq = float(raw_data.get("start_freq_hz", 0))
            end_freq = float(raw_data.get("end_freq_hz", 0))

            # --- C. Slicing Logic ---
            desired_span = int(base_config.get("span"))
            
            if len(raw_pxx) == 0: continue

            current_bw = end_freq - start_freq
            final_pxx = raw_pxx
            
            # Slicing logic to zoom into desired span
            if current_bw > 0 and desired_span < current_bw:
                ratio = desired_span / current_bw
                bins_to_keep = int(len(raw_pxx) * ratio)
                if bins_to_keep > len(raw_pxx): bins_to_keep = len(raw_pxx)
                if bins_to_keep < 1: bins_to_keep = 1

                start_idx = int((len(raw_pxx) - bins_to_keep) // 2)
                end_idx = start_idx + bins_to_keep
                final_pxx = raw_pxx[start_idx : end_idx]

            # --- D. DC Spike Analysis ---
            if len(final_pxx) > 0:
                # 1. Calc Threshold
                noise_median = np.median(final_pxx)
                threshold = noise_median + 0.5

                # 2. Find Peak
                peak_idx = np.argmax(final_pxx)
                
                # 3. Walk Outwards
                left_idx = peak_idx
                while left_idx > 0 and final_pxx[left_idx] > threshold:
                    left_idx -= 1
                
                right_idx = peak_idx
                while right_idx < len(final_pxx) - 1 and final_pxx[right_idx] > threshold:
                    right_idx += 1
                
                # 4. Calc Width
                freq_res = desired_span / len(final_pxx) 
                width_bins = right_idx - left_idx
                if width_bins < 1: width_bins = 1
                
                dc_width_hz = width_bins * freq_res
                occupancy_pct = (dc_width_hz / desired_span) * 100

                # Store Results
                results_rbw.append(current_rbw)
                results_dc_width_hz.append(dc_width_hz)
                results_occupancy_pct.append(occupancy_pct)

                # --- E. LIVE SNAPSHOT PLOTTING (Every 5th iteration) ---
                if i % 5 == 0 or i == len(rbw_values) - 1:
                    ax_snap.clear()
                    
                    # X-Axis in MHz
                    freqs = np.linspace(start_freq, end_freq, len(raw_pxx))
                    # Slice freqs to match final_pxx
                    # Note: Approximation for visualization
                    viz_freqs = np.linspace(-desired_span/2, desired_span/2, len(final_pxx)) / 1e6
                    
                    # 1. Plot Signal
                    ax_snap.plot(viz_freqs, final_pxx, color='cyan', linewidth=0.8, label='Pxx')
                    
                    # 2. Plot Threshold
                    ax_snap.axhline(threshold, color='red', linestyle='--', alpha=0.7, label='Threshold (+1dB)')
                    
                    # 3. Highlight Detected Width
                    # Convert indices to x-axis values
                    x_left = viz_freqs[left_idx]
                    x_right = viz_freqs[right_idx]
                    
                    ax_snap.axvspan(x_left, x_right, color='lime', alpha=0.3, label='Detected Spike')
                    
                    ax_snap.set_title(f"RBW: {current_rbw} Hz | Detected Width: {dc_width_hz/1000:.2f} kHz")
                    ax_snap.set_ylabel("Power (dBm)")
                    ax_snap.set_xlabel("Frequency Offset (MHz)")
                    ax_snap.legend(loc='upper right')
                    ax_snap.grid(True, alpha=0.3)
                    
                    plt.pause(0.5) # Allow render update
                    
    except KeyboardInterrupt:
        log.info("Sweep interrupted.")
    except Exception as e:
        log.error(f"Error: {e}")
        import traceback
        traceback.print_exc()

    # Close snapshot window
    plt.close(fig_snap)

    # 
    # The choice of window (Hamming vs Rectangular) affects the width of this spike significantly.

    # --- F. Final Analysis Plots ---
    log.info("Generating Final Reports...")
    
    if not results_rbw:
        return

    plt.ioff() # Turn off interactive mode for final plot
    plt.figure(figsize=(12, 10))
    plt.style.use('dark_background')

    # Plot 1: Log-Log Width vs RBW
    plt.subplot(2, 1, 1)
    plt.loglog(results_rbw, results_dc_width_hz, 'o-', color='cyan', linewidth=2)
    plt.grid(True, which="both", linestyle='--', alpha=0.4)
    plt.title("Impact of RBW on DC Spike Width")
    plt.ylabel("Measured Spike Width (Hz)")
    plt.xlabel("RBW (Hz)")

    # Plot 2: Occupancy %
    plt.subplot(2, 1, 2)
    plt.semilogx(results_rbw, results_occupancy_pct, 'x-', color='magenta', linewidth=2)
    plt.grid(True, which="both", linestyle='--', alpha=0.4)
    plt.title("Spectrum Occupancy of DC Spike")
    plt.ylabel("Occupancy (%)")
    plt.xlabel("RBW (Hz)")

    plt.tight_layout()
    plt.show()

if __name__ == "__main__":
    asyncio.run(run_rbw_sweep())