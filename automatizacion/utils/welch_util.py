#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""!
@file welch_util.py
@brief Utility functions and classes for Welch's method.
"""
import numpy as np
from scipy.signal import welch
from typing import Optional, Tuple, Dict, Any, Union
try:
    from pyhackrf2 import HackRF
except Exception as _hackrf_import_error:  # pragma: no cover - import guard for environments sin HackRF
    HackRF = None
    _HACKRF_IMPORT_ERROR = _hackrf_import_error
import logging
import time

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
    
    def execute_welch(
        self,
        iq_data: np.ndarray,
        scale: str = 'dBm',
        return_meta: bool = False
    ) -> Union[
        Tuple[np.ndarray, np.ndarray],
        np.ndarray,
        Tuple[Tuple[np.ndarray, np.ndarray], Dict[str, Any]],
        Tuple[np.ndarray, Dict[str, Any]]
    ]:
        """
        Calculates the PSD for a given IQ data array.

        Args:
            iq_data: complex array with IQ samples.
            scale: output units ("dBm", "dB", "dBFS", "V2/Hz").
            return_meta: if True, returns a metadata dict with timing and Welch parameters.

        Returns:
            - If self.with_shift == True: (frequencies, Pxx_scaled)
            - If self.with_shift == False: Pxx_scaled
            - If return_meta == True: the above plus a metadata dict
        """
        t_start = time.perf_counter()
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
        elapsed_ms = (time.perf_counter() - t_start) * 1000

        meta = {
            "n_samples": n_samples,
            "nperseg": nperseg,
            "noverlap": noverlap,
            "rbw_hz_approx": self.fs / nperseg if nperseg > 0 else None,
            "elapsed_ms": elapsed_ms,
            "scale": scale.lower(),
            "with_shift": self.with_shift,
        }
        
        if self.with_shift:
            result = (f, Pxx_scaled)
        else:
            result = Pxx_scaled

        if return_meta:
            return result, meta
        return result
    
class CampaignHackRF:
    """
    Adquiere muestras reales con pyhackrf2 y calcula la PSD con WelchEstimator.
    Pensado para integrarse con client.configure_sensor() y devolver siempre (f, Pxx).
    """

    def __init__(
        self,
        start_freq_hz: int,
        end_freq_hz: int,
        sample_rate_hz: int,
        resolution_hz: int,
        scale: str = "dBm",
        verbose: bool = False,
        log: logging.Logger = logging.getLogger(__name__),
        **kwargs: Any,
    ) -> None:
        self.freq = int(((end_freq_hz - start_freq_hz) / 2) + start_freq_hz)
        self.sample_rate_hz = int(sample_rate_hz) if sample_rate_hz else 20_000_000
        self.resolution_hz = int(resolution_hz) if resolution_hz else 10_000
        self.scale = scale or "dBm"
        self.verbose = verbose
        self._log = log

        # Instancia HackRF y buffer de IQ
        self.hack: Optional[HackRF] = None
        self.iq: Optional[np.ndarray] = None

        # Controla si se generan frecuencias en la salida (client asume True)
        self.with_shift: bool = kwargs.get("with_shift", True)

        # Parametros de Welch
        self.window: str = kwargs.get("window", "hamming")
        self.overlap: float = kwargs.get("overlap", 0.5)
        self.r_ant: float = kwargs.get("r_ant", 50.0)

        # Parametros RF
        self.lna_gain: int = kwargs.get("lna_gain", 0)
        self.vga_gain: int = kwargs.get("vga_gain", 0)
        self.antenna_amp: bool = kwargs.get("antenna_amp", True)
        self.bias_tee: bool = kwargs.get("bias_tee", False)

        # Control de captura
        self.num_samples: Optional[int] = kwargs.get("num_samples")
        self.capture_seconds: Optional[float] = kwargs.get("capture_seconds")

    def _ensure_hackrf(self) -> Optional[HackRF]:
        if HackRF is None:
            self._log.error(f"pyhackrf2 no disponible: {_HACKRF_IMPORT_ERROR}")
            return None

        if self.hack is not None:
            return self.hack

        try:
            self.hack = HackRF()
            if self.verbose:
                self._log.info("HackRF inicializada")
        except Exception as exc:
            self._log.error(f"Error abriendo HackRF: {exc}")
            self.hack = None
        return self.hack

    def _configure_device(self, hack: HackRF) -> None:
        # Configuracion basica antes de leer
        hack.center_freq = self.freq
        hack.sample_rate = self.sample_rate_hz
        hack.amplifier_on = self.antenna_amp
        hack.lna_gain = self.lna_gain
        hack.vga_gain = self.vga_gain
        hack.bias_tee_on = self.bias_tee

    def _samples_to_read(self, est: WelchEstimator) -> int:
        # Si el usuario especifico la cantidad, respetarla
        if self.num_samples:
            return int(self.num_samples)

        # Permitir captura por segundos (util para promediar mas tiempo)
        if self.capture_seconds:
            return int(self.sample_rate_hz * self.capture_seconds)

        # Por defecto, usar al menos 1s o 2x nperseg calculado
        return max(self.sample_rate_hz, est.desired_nperseg * 2)

    def acquire_hackrf(self, est: WelchEstimator) -> int:
        hack = self._ensure_hackrf()
        if hack is None:
            return 1

        try:
            self._configure_device(hack)
            n_samples = self._samples_to_read(est)
            self.iq = hack.read_samples(n_samples)
            if self.verbose:
                self._log.info(f"Capturadas {len(self.iq)} muestras a {self.sample_rate_hz} sps")
        except Exception as exc:
            self._log.error(f"Error leyendo muestras HackRF: {exc}")
            self.iq = None
            return 1

        return 0

    def close(self) -> None:
        if self.hack is not None:
            try:
                self.hack.close()
            except Exception as exc:
                self._log.warning(f"No se pudo cerrar HackRF limpiamente: {exc}")
            self.hack = None

    def get_psd(
        self, return_meta: bool = False
    ) -> Union[Tuple[np.ndarray, np.ndarray], Tuple[Tuple[np.ndarray, np.ndarray], Dict[str, Any]]]:
        """
        Captura IQ con la HackRF y calcula la PSD.
        Siempre devuelve (f, Pxx) o ((f, Pxx), meta) si return_meta=True.
        """
        est = WelchEstimator(
            freq=self.freq,
            fs=self.sample_rate_hz,
            desired_rbw=self.resolution_hz,
            r_ant=self.r_ant,
            with_shift=self.with_shift,
            window=self.window,
            overlap=self.overlap,
        )

        err = self.acquire_hackrf(est)
        if err != 0 or self.iq is None:
            if self.with_shift:
                return (None, None) if not return_meta else ((None, None), {})
            return None if not return_meta else (None, {})

        result = est.execute_welch(self.iq, self.scale, return_meta=return_meta)

        if return_meta:
            (f, pxx), meta = result
            return (f, pxx), meta

        # Con with_shift=True, execute_welch devuelve (f, pxx)
        f, pxx = result
        return f, pxx
