#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""!
@file welch_util.py
@brief Utility functions and classes for Welch's method.
"""
import numpy as np
from scipy.signal import welch
from typing import Optional
from pyhackrf2 import HackRF
import logging

class WelchEstimator:
    """
    Calculates the PSD of a given IQ signal using Welch's method,
    applying the correct frequency scaling and centering.

    The class is configured once with signal parameters (fs, freq)
    and Welch parameters (rbw, window, overlap). Then, the 
    execute_welch() method can be called repeatedly with different data arrays.

    Arguments (kwargs):
      - with_shift: bool (default True). If False, the frequency vector is 
                    NOT generated or returned; only the scaled PSD is returned.
      - window: str (default 'hamming'). Window function to use.
      - overlap: float (default 0.5). Overlap ratio.
      - r_ant: float. Antenna impedance (default 50.0).
    """
    def __init__(self, freq: int, fs: int, desired_rbw: int, **kwargs):
        self.freq = freq  # Center frequency in Hz
        self.fs = fs      # Sample rate in Hz
        self.desired_rbw = desired_rbw  # Desired Resolution Bandwidth in Hz
        self.window = kwargs.get("window", "hamming")
        self.overlap = kwargs.get("overlap", 0.5) # Overlap ratio

        # Controls whether to generate/shift the frequency axis
        self.with_shift = kwargs.get("with_shift", True)
        
        # Check if impedance was provided
        r_ant_val = kwargs.get("r_ant")
        if r_ant_val is not None:
            self.r_ant = r_ant_val
            self.impedance = True
        else:
            self.r_ant = 50.0  # Default value, only used if dBm is requested
            self.impedance = False

        # Calculate ideal nperseg based on desired RBW
        self.desired_nperseg = self._calculate_desired_nperseg()
        
    def _next_power_of_2(self, x: int) -> int:
        """Calculates the next power of 2 greater than or equal to x."""
        if x <= 0:
            return 1
        
        return 1 << (x - 1).bit_length()

    def _calculate_desired_nperseg(self) -> int:
        """
        Calculates the nperseg (power of 2) necessary to 
        guarantee an RBW <= desired_rbw.
        """
        # RBW_approx = fs / N
        # N_ideal = fs / desired_rbw
        N_float = self.fs / self.desired_rbw
        
        # Round up to the next power of 2 
        # to ensure actual RBW is <= desired.
        N_pow2 = self._next_power_of_2(int(np.ceil(N_float))) 
        return N_pow2

    def _welch_psd(self, iq_data: np.ndarray, nperseg: int, noverlap: int) -> np.ndarray:
        """Executes welch and applies fftshift and impedance correction."""
        _, Pxx = welch(iq_data, 
                       fs=self.fs, 
                       nperseg=nperseg, 
                       noverlap=noverlap,
                       window=self.window,
                       )

        Pxx = np.fft.fftshift(Pxx)
        if self.impedance:
            Pxx = Pxx / self.r_ant
        return Pxx
        
    def _scale_signal(self, iq_data: np.ndarray, nperseg: int, noverlap: int, scale: str) -> np.ndarray:
        """Applies the desired unit scaling (dBm, dBFS, etc.) to the PSD."""
        scale = scale.lower()
        
        if scale == "dbfs":
            # Copy to avoid modifying original IQ data
            iq_data_copy = iq_data.copy()
            
            # Normalize by maximum magnitude of complex vector (Full Scale)
            max_mag = np.max(np.abs(iq_data_copy))
            if max_mag > 0:
                temp_buffer = iq_data_copy / max_mag
            else:
                temp_buffer = iq_data_copy # Signal is zero

            # Recalculate Pxx with normalized signal (temporary)
            _, Pxx = welch(temp_buffer, 
                           fs=self.fs, 
                           nperseg=nperseg, 
                           noverlap=noverlap, 
                           window=self.window, 
                           )
            Pxx = np.fft.fftshift(Pxx)
            
            P_FS = 1.0 # Full Scale power is 1.0 (for a signal of amplitude 1.0)
            return 10 * np.log10(Pxx / P_FS + 1e-20)

        # For other scales, use IQ data as is
        Pxx = self._welch_psd(iq_data, nperseg, noverlap)

        match scale:
            case "dbm":
                # If dBm requested but no r_ant given, assume 50 Ohm
                Pxx_W = Pxx / self.r_ant if self.impedance else Pxx / 50.0
                return 10 * np.log10(Pxx_W * 1000 + 1e-20)
            case "db":
                return 10 * np.log10(Pxx + 1e-20) 
            case "v2/hz":
                # V^2/Hz (Voltage PSD). Pxx is Vrms^2/Hz.
                # If Pxx is W/Hz (impedance=True), P = Vrms^2 / R -> Vrms^2 = P * R
                if self.impedance:
                    return Pxx * self.r_ant
                else:
                    return Pxx # Assumes Pxx is already Vrms^2/Hz
            case _:
                raise ValueError("Invalid scale. Use 'V2/Hz', 'dB', 'dBm' or 'dBFS'.")
    
    def execute_welch(self, iq_data: np.ndarray, scale: str = 'dBm'):
        """
        Calculates the PSD for a given IQ data array.

        Returns:
            - If self.with_shift == True: (frequencies, Pxx_scaled)
            - If self.with_shift == False: Pxx_scaled
        """
        n_samples = len(iq_data)
        nperseg = self.desired_nperseg
        
        # --- Critical Validation ---
        # Check if we have enough samples for the desired RBW
        if n_samples < nperseg:
            nperseg = n_samples 

        # Calculate actual overlap in samples
        noverlap = int(nperseg * self.overlap)
        
        # Generate frequency axis ONLY if requested
        f = None
        if self.with_shift:
            N_psd = nperseg 
            f_base = np.fft.fftfreq(N_psd, d=1/self.fs)
            f_shifted = np.fft.fftshift(f_base)
            f = f_shifted + self.freq # Center on carrier frequency
        
        # Calculate PSD and apply scale
        Pxx_scaled = self._scale_signal(iq_data, nperseg, noverlap, scale)
        
        if self.with_shift:
            return f, Pxx_scaled
        else:
            return Pxx_scaled
    

class CampaignHackRF:
    """
    Simple wrapper to acquire samples with pyhackrf2 and calculate PSD with WelchEstimator.
    """
    def __init__(self, start_freq_hz: int, end_freq_hz: int, 
                 sample_rate_hz: int, resolution_hz: int, 
                 scale: str = 'dBm', verbose: bool = False, 
                 log=logging.getLogger(__name__),
                 **kwargs):
        
        self.freq = int(((end_freq_hz - start_freq_hz)/2) + start_freq_hz) # Center freq
        self.sample_rate_hz = sample_rate_hz
        self.resolution_hz = resolution_hz
        self.scale = scale
        self.hack = None
        self.verbose = verbose
        self._log = log
        self.iq = None

        # Control to generate or not the frequency vector in the output
        self.with_shift = kwargs.get("with_shift", True)

        # --- New Parameters Handling ---
        # If keys are not present in kwargs, default values are used.
        self.window = kwargs.get("window", "hamming")
        self.overlap = kwargs.get("overlap", 0.5)
        self.lna_gain = kwargs.get("lna_gain", 0)         # Default 0 if not specified
        self.vga_gain = kwargs.get("vga_gain", 0)         # Default 0 if not specified
        self.antenna_amp = kwargs.get("antenna_amp", False) # Default False

    def init_hack(self) -> Optional[HackRF]:
        try:
            hack = HackRF()
        except Exception as e:
            self._log.error(f"Error opening HackRF: {e}")
            hack = None

        return hack
    
    def acquire_hackrf(self) -> int:
        hack = self.init_hack()
        if hack is None:
            return 1
        
        hack.center_freq = self.freq
        hack.sample_rate = self.sample_rate_hz
        
        # Apply user-defined or default gains/amp settings
        hack.amplifier_on = self.antenna_amp
        hack.lna_gain = self.lna_gain
        hack.vga_gain = self.vga_gain
        
        # read_samples can be blocking and return a complex array
        try:
            self.iq = hack.read_samples(hack.sample_rate)
            if self.verbose:
                self._log.info(f"Acquired {len(self.iq)} samples")
        except Exception as e:
            self._log.error(f"Error reading samples: {e}")
            return 1
        finally:
            # Good practice to close/release device if init created a new instance per call
            # Assuming hack instance lifecycle is managed here
            hack.close()

        return 0
    
    def get_psd(self):
        """
        Acquires samples and calculates PSD. Return conditioned by self.with_shift:
          - with_shift == True  -> (frequencies, Pxx)
          - with_shift == False -> Pxx
        In case of acquisition failure:
          - with_shift == True  -> (None, None)
          - with_shift == False -> None
        """
        # Pass the specific window and overlap settings to the estimator
        est = WelchEstimator(
            freq=self.freq, 
            fs=self.sample_rate_hz, 
            desired_rbw=self.resolution_hz, 
            r_ant=50.0,
            with_shift=self.with_shift,
            window=self.window,
            overlap=self.overlap
        )
        
        err = self.acquire_hackrf()
        if err != 0 or self.iq is None:
            if self.with_shift:
                return None, None
            else:
                return None
        
        result = est.execute_welch(self.iq, self.scale)

        # result can be (f, Pxx) or Pxx depending on with_shift
        if self.with_shift:
            f, Pxx_scaled = result
            return f, Pxx_scaled
        else:
            Pxx_scaled = result
            return Pxx_scaled