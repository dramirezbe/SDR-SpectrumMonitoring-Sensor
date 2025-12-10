import numpy as np
from scipy import signal
import time
from pyhackrf2 import HackRF
import matplotlib.pyplot as plt

# Optional GPU support
try:
    import cupy as cp
    GPU_AVAILABLE = True
except ImportError:
    GPU_AVAILABLE = False
    print("CuPy not available - using CPU only")

# ==========================================
#        POLYPHASE FILTER BANK CORE
# ==========================================

class PolyphaseFilterBank:
    """
    Standard PFB implementation.
    """
    def __init__(self, num_channels=256, overlap_factor=4, window='kaiser', use_gpu=False):
        self.M = num_channels
        self.L = overlap_factor
        self.filter_length = self.M * self.L
        self.use_gpu = use_gpu and GPU_AVAILABLE
        
        # Design prototype filter
        prototype = self._design_prototype(window)
        
        # Create polyphase decomposition
        self.polyphase_filters = self._create_polyphase_matrix(prototype)
        
        if self.use_gpu:
            self.polyphase_filters = cp.asarray(self.polyphase_filters, dtype=cp.complex64)
            self.state = cp.zeros((self.M, self.L - 1), dtype=cp.complex64)
        else:
            self.state = np.zeros((self.M, self.L - 1), dtype=np.complex64)

    def _design_prototype(self, window):
        cutoff = 1.0 / (2 * self.M)
        if window == 'kaiser':
            beta = 6.0
            h = signal.firwin(self.filter_length, cutoff, window=('kaiser', beta), scale=True)
        h *= self.M
        return h

    def _create_polyphase_matrix(self, prototype):
        num_taps_per_phase = len(prototype) // self.M
        matrix = prototype.reshape(num_taps_per_phase, self.M).T
        return np.flip(matrix, axis=0)

    def process_block(self, input_block):
        if self.use_gpu:
            return self._process_block_gpu(input_block)
        else:
            return self._process_block_cpu(input_block)

    def _process_block_cpu(self, input_block):
        # Stack state + new input column
        filter_input = np.column_stack([
            self.state, 
            np.asarray(input_block, dtype=np.complex64).reshape(-1, 1)
        ])
        
        # Shift state
        self.state = filter_input[:, 1:]
        
        # Polyphase convolution
        filtered = np.sum(self.polyphase_filters * filter_input, axis=1)
        
        # FFT and Shift
        channels = np.fft.fft(filtered)
        channels = np.fft.fftshift(channels)
        return channels

    def _process_block_gpu(self, input_block):
        input_gpu = cp.asarray(input_block, dtype=cp.complex64)
        filter_input = cp.column_stack([
            self.state, 
            input_gpu.reshape(-1, 1)
        ])
        self.state = filter_input[:, 1:]
        filtered = cp.sum(self.polyphase_filters * filter_input, axis=1)
        channels = cp.fft.fft(filtered)
        channels = cp.fft.fftshift(channels)
        return cp.asnumpy(channels)

# ==========================================
#           ACQUISITION & LOGIC
# ==========================================

def to_int_dict(d: dict) -> dict:
    return {k: int(v) for k, v in d.items()}

def apply_params_hackrf(h: HackRF, params: dict):
    h.sample_rate = params['sample_rate']
    h.center_freq = params['center_freq']
    h.lna_gain = params['lna_gain']
    h.vga_gain = params['vga_gain']

def acquire_one_shot(duration_sec=0.5):
    """
    Configures HackRF, grabs 'duration_sec' of samples, and closes device.
    """
    raw_params = {
        'sample_rate': 20e6,
        'center_freq': 98e6,
        'lna_gain': 16,
        'vga_gain': 20
    }
    params_h = to_int_dict(raw_params)
    
    # Open Device
    h = HackRF()
    apply_params_hackrf(h, params_h)
    
    # Calculate number of samples needed
    num_samples = int(params_h['sample_rate'] * duration_sec)
    
    print(f"Acquiring {duration_sec}s of data ({num_samples} samples)...")
    
    # Read samples (this blocks until done)
    raw_samples = h.read_samples(num_samples)
    
    # Convert to Complex IQ
    raw_data = np.array(raw_samples, dtype=np.float32)
    iq_data = raw_data[0::2] + 1j * raw_data[1::2]
    
    return iq_data, params_h

def compute_pfb_spectrum(iq_data, num_channels=256):
    """
    Runs the full IQ dataset through the PFB and averages the power.
    """
    pfb = PolyphaseFilterBank(num_channels=num_channels, overlap_factor=4, use_gpu=GPU_AVAILABLE)
    
    num_samples = len(iq_data)
    num_blocks = num_samples // num_channels
    
    print(f"Processing {num_blocks} blocks through Polyphase Filter Bank...")
    
    # Accumulator for Power Spectral Density
    power_accumulator = np.zeros(num_channels)
    
    # Loop through data in chunks of 'num_channels'
    # We must loop sequentially to maintain PFB filter state
    for i in range(num_blocks):
        block = iq_data[i * num_channels : (i + 1) * num_channels]
        
        # Get channelized output (complex)
        channel_out = pfb.process_block(block)
        
        # Accumulate Power (Magnitude Squared)
        power_accumulator += np.abs(channel_out)**2
    
    # Average over time
    avg_power = power_accumulator / num_blocks
    
    # Convert to dB
    # Adding small epsilon to avoid log(0)
    psd_db = 10 * np.log10(avg_power + 1e-12)
    
    return psd_db

def main():
    # 1. Acquire Data (One Shot)
    # 0.5 seconds at 20 MS/s = 10 million samples
    iq_data, params = acquire_one_shot(duration_sec=0.5)
    
    # 2. Process Data
    NUM_CHANNELS = 256
    psd_db = compute_pfb_spectrum(iq_data, num_channels=NUM_CHANNELS)
    
    # 3. Prepare Frequency Axis
    # PFB with fftshift centers DC at index M/2
    freqs = np.linspace(
        params['center_freq'] - params['sample_rate']/2,
        params['center_freq'] + params['sample_rate']/2,
        NUM_CHANNELS
    )
    freqs_mhz = freqs / 1e6
    
    # 4. Plot
    print("Plotting results...")
    plt.figure(figsize=(10, 6))
    
    # Plot 'step' style to represent discrete channel bins
    plt.step(freqs_mhz, psd_db, where='mid', color='#2ecc71', linewidth=1.5)
    #plt.fill_between(freqs_mhz, psd_db, np.min(psd_db), step='mid', color='#2ecc71', alpha=0.3)
    
    plt.title(f"PFB Spectrum Snapshot\nCenter: {params['center_freq']/1e6} MHz | Channels: {NUM_CHANNELS}")
    plt.xlabel("Frequency [MHz]")
    plt.ylabel("Power Spectral Density [dB]")
    plt.grid(True, which='both', linestyle='--', alpha=0.5)
    
    # Add marker for Center Frequency
    plt.axvline(params['center_freq']/1e6, color='red', linestyle='--', alpha=0.5, label='Center Freq')
    plt.legend()
    
    plt.tight_layout()
    plt.show()

if __name__ == "__main__":
    main()