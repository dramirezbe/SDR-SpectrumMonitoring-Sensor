from pyhackrf2 import HackRF
from scipy.signal import welch
import matplotlib.pyplot as plt
import numpy as np

def remove_spike(Pxx):
    # Assuming Pxx_den is your power spectral density array
    num_pxx = len(Pxx)
    index_dc = num_pxx // 2

    # Define the width of the spike to remove (requested +- 5 points)
    spike_width = 5
    # Define the width of the window used to estimate the background noise
    noise_window = 20 

    # 1. Define the range we want to fix (the notch)
    notch_start = index_dc - spike_width
    notch_end   = index_dc + spike_width + 1  # +1 because Python ranges are exclusive at the end

    # 2. Extract surrounding data to estimate the actual noise floor
    # We take data from the left and right of the spike, avoiding the spike itself
    left_sample  = Pxx[index_dc - noise_window : notch_start]
    right_sample = Pxx[notch_end : index_dc + noise_window]

    # 3. Calculate a representative background value (Median is robust against other outliers)
    background_level = np.median(np.concatenate((left_sample, right_sample)))

    # 4. Apply the notch
    # Replace the DC spike region with the estimated background level
    Pxx[notch_start : notch_end] = background_level

    return Pxx
    

def apply_welch(iq_data, params_h):

    f, Pxx_den = welch(
        iq_data, 
        fs=params_h['sample_rate'], 
        nperseg=4096, 
        return_onesided=False, 
        scaling='spectrum'
    )
    
    # Shift zero-frequency to center
    f = np.fft.fftshift(f)
    Pxx_den = np.fft.fftshift(Pxx_den)

    Pxx_den = remove_spike(Pxx_den)        

    # --- STEP 3: Convert to dBm/Hz (50 Ohm) ---
    # Formula: 10*log10(V^2/Hz) + 13.01
    psd_dbm_hz = 10 * np.log10(Pxx_den + 1e-15) + 13.01

    # --- STEP 4: Plot ---
    # Convert frequency axis to MHz and shift to center freq
    freq_mhz = (f + params_h['center_freq']) / 1e6

    return freq_mhz, psd_dbm_hz


def acquire_hackrf():
    raw_params = {
        'sample_rate': 6e6,
        'center_freq': 105.7e6,
        'lna_gain': 0,
        'vga_gain': 0
    }

    params_h = to_int_dict(raw_params)
    h = HackRF()
    apply_params_hackrf(h, params_h)
    
    # Read samples
    # Assuming sample_rate is sufficient buffer size for a quick snapshot
    # This returns scaled floats now
    samples = h.read_samples(params_h['sample_rate']) 
    
    # --- STEP 1: Handle Data Format ---
    # Since data is already float, we just ensure it is a numpy array
    raw_data = np.array(samples, dtype=np.float32)
    
    # Check if data is Interleaved (I, Q, I, Q) or already Complex
    # Most SDR drivers return interleaved floats. We combine them here.
    if np.iscomplexobj(raw_data):
        iq_data = raw_data
    else:
        # Interleaved: Evens are I (Real), Odds are Q (Imag)
        # No division by 128.0 needed as user confirmed data is scaled
        iq_data = raw_data[0::2] + 1j * raw_data[1::2]
    
    print(f"Number of IQ pairs: {len(iq_data)}")
    return iq_data, params_h


def to_int_dict(d: dict) -> dict:
    return {k: int(v) for k, v in d.items()}

def apply_params_hackrf(h: HackRF, params: dict):
    h.sample_rate = params['sample_rate']
    h.center_freq = params['center_freq']
    h.lna_gain = params['lna_gain']
    h.vga_gain = params['vga_gain']

def main():
    iq_data, params_h = acquire_hackrf()
    f, Pxx = apply_welch(iq_data, params_h)
    
    plt.figure(figsize=(10, 6))
    plt.plot(f, Pxx)
    plt.title(f"Power Spectral Density (Center: {params_h['center_freq']/1e6} MHz)")
    plt.xlabel('Frequency [MHz]')
    plt.ylabel('PSD [dBm/Hz] @ 50$\Omega$')
    plt.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.show()

if __name__ == "__main__":
    main()