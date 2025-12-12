import json
import matplotlib.pyplot as plt
import numpy as np
import os
import glob
from collections import defaultdict

# --- Configuration ---
INPUT_DIR = "json_spikes"
OUTPUT_DIR = "plots"
SPAN_HZ = 20_000_000  # 20 MHz (Must match the 'span' in your acquisition script)

def load_data(directory):
    """Loads all JSON files and groups them by RBW."""
    json_files = glob.glob(os.path.join(directory, "*.json"))
    
    if not json_files:
        print(f"No JSON files found in {directory}")
        return {}

    grouped_data = defaultdict(list)

    for filepath in json_files:
        try:
            with open(filepath, 'r') as f:
                data = json.load(f)
                rbw = data.get("rbw_hz", "unknown")
                grouped_data[rbw].append(data)
        except Exception as e:
            print(f"Error reading {filepath}: {e}")

    # Sort the list for each RBW by acquisition index so the legend is in order
    for rbw in grouped_data:
        grouped_data[rbw].sort(key=lambda x: x['acquisition_index'])

    return grouped_data

def generate_plots(grouped_data):
    """Generates and saves one plot per RBW setting."""
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    for rbw, acquisitions in grouped_data.items():
        plt.figure(figsize=(12, 7))
        
        # Get pxx_length from the first acquisition in the group
        # (Assuming all acquisitions for the same RBW have the same length)
        first_acq = acquisitions[0]
        pxx_len = first_acq.get("pxx_length", len(first_acq['pxx_data']))
        
        print(f"Processing RBW: {rbw} Hz ({len(acquisitions)} files, Length: {pxx_len})...")

        for acq in acquisitions:
            # 1. Extract Data
            pxx = np.array(acq['pxx_data'])
            center_freq = acq['center_freq_hz']
            idx = acq['acquisition_index']
            
            # 2. Reconstruct Frequency Axis
            # We assume the data spans [Center - Span/2] to [Center + Span/2]
            n_points = len(pxx)
            start_freq = center_freq - (SPAN_HZ / 2)
            stop_freq = center_freq + (SPAN_HZ / 2)
            
            # Create frequency array in MHz for readability
            freq_axis_mhz = np.linspace(start_freq, stop_freq, n_points) / 1e6

            # 3. Plot
            plt.plot(freq_axis_mhz, pxx, label=f"Acq {idx}", alpha=0.8, linewidth=1)

        # 4. Formatting
        # Updated Title to include pxx_length
        plt.title(f"Spectrum Acquisition (RBW: {rbw} Hz, pxx_length: {pxx_len})", fontsize=14)
        plt.xlabel("Frequency (MHz)", fontsize=12)
        plt.ylabel("Power (dBm)", fontsize=12)
        plt.grid(True, which='both', linestyle='--', linewidth=0.5, alpha=0.7)
        plt.legend(loc='upper right')
        
        # 5. Save
        filename = f"plot_rbw_{rbw}.png"
        save_path = os.path.join(OUTPUT_DIR, filename)
        plt.savefig(save_path, dpi=150)
        plt.close()
        
        print(f"Saved: {save_path}")

if __name__ == "__main__":
    if not os.path.exists(INPUT_DIR):
        print(f"Directory '{INPUT_DIR}' not found. Run the acquisition script first.")
    else:
        data = load_data(INPUT_DIR)
        generate_plots(data)
        print("All plots generated.")