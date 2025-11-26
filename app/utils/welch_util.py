#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""!
@file welch_util.py
@brief Utility functions and classes for Welch's method.
"""
import numpy as np
from scipy.signal import welch
from typing import Tuple, Optional, Union
from pyhackrf2 import HackRF
import logging

class WelchEstimator:
    """
    Calcula la PSD de una señal IQ dada usando el método de Welch,
    aplicando la escala y el centrado de frecuencia correctos.

    La clase se configura una vez con los parámetros de la señal (fs, freq)
    y los deseados para Welch (rbw, window, overlap). Luego, el método
    execute_welch() se puede llamar repetidamente con diferentes arrays de datos.

    Nuevo argumento (kwargs):
      - with_shift: bool (por defecto True). Si False, NO se genera ni devuelve
                    el vector de frecuencias; solo se devuelve la PSD escalada.
    """
    def __init__(self, freq: int, fs: int, desired_rbw: int, **kwargs):
        self.freq = freq  # Frecuencia central en Hz
        self.fs = fs      # Tasa de muestreo en Hz
        self.desired_rbw = desired_rbw  # Resolution Bandwidth deseada en Hz
        self.window = kwargs.get("window", "hamming")
        self.overlap = kwargs.get("overlap", 0.5) # Proporción de solapamiento

        # Controla si se debe generar/shiftear el eje de frecuencias
        self.with_shift = kwargs.get("with_shift", True)
        
        # Comprueba si se proporcionó una impedancia
        r_ant_val = kwargs.get("r_ant")
        if r_ant_val is not None:
            self.r_ant = r_ant_val
            self.impedance = True
        else:
            self.r_ant = 50.0  # Un valor por defecto, solo se usará si se pide dBm
            self.impedance = False

        # Calcula el nperseg ideal basado en el RBW deseado
        self.desired_nperseg = self._calculate_desired_nperseg()
        
    def _next_power_of_2(self, x: int) -> int:
        """Calcula la siguiente potencia de 2 que es >= x."""
        if x <= 0:
            return 1
        
        return 1 << (x - 1).bit_length()

    def _calculate_desired_nperseg(self) -> int:
        """Calcula el nperseg (potencia de 2) necesario para
        garantizar un RBW <= desired_rbw."""
        # RBW_approx = fs / N
        # N_ideal = fs / desired_rbw
        N_float = self.fs / self.desired_rbw
        
        # Redondea hacia arriba a la sig. potencia de 2
        # para garantizar que el RBW real sea <= al deseado.
        N_pow2 = self._next_power_of_2(int(np.ceil(N_float))) 
        return N_pow2

    def _welch_psd(self, iq_data: np.ndarray, nperseg: int, noverlap: int) -> np.ndarray:
        """Ejecuta welch y aplica fftshift y corrección de impedancia."""
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
        """Aplica la escala de unidades deseada (dBm, dBFS, etc.) a la PSD."""
        scale = scale.lower()
        
        if scale == "dbfs":
            # Copia para no modificar los datos IQ originales
            iq_data_copy = iq_data.copy()
            
            # Normaliza por la magnitud máxima del vector complejo (Full Scale)
            max_mag = np.max(np.abs(iq_data_copy))
            if max_mag > 0:
                temp_buffer = iq_data_copy / max_mag
            else:
                temp_buffer = iq_data_copy # Señal es cero

            # Recalcula Pxx con la señal normalizada (temporal)
            _, Pxx = welch(temp_buffer, 
                           fs=self.fs, 
                           nperseg=nperseg, 
                           noverlap=noverlap, 
                           window=self.window, 
                           )
            Pxx = np.fft.fftshift(Pxx)
            
            P_FS = 1.0 # Potencia Full Scale es 1.0 (para una señal de amplitud 1.0)
            return 10 * np.log10(Pxx / P_FS + 1e-20)

        # Para otras escalas, usa los datos IQ tal cual
        Pxx = self._welch_psd(iq_data, nperseg, noverlap)

        match scale:
            case "dbm":
                # Si se pide dBm pero no se dio r_ant, asumimos 50 Ohm
                Pxx_W = Pxx / self.r_ant if self.impedance else Pxx / 50.0
                return 10 * np.log10(Pxx_W * 1000 + 1e-20)
            case "db":
                return 10 * np.log10(Pxx + 1e-20) 
            case "v2/hz":
                # V^2/Hz (PSD de voltaje). Pxx es Vrms^2/Hz.
                # Si Pxx es W/Hz (impedance=True), P = Vrms^2 / R -> Vrms^2 = P * R
                if self.impedance:
                    return Pxx * self.r_ant
                else:
                    return Pxx # Asume que Pxx ya está en Vrms^2/Hz
            case _:
                raise ValueError("Escala no válida. Use 'V2/Hz', 'dB', 'dBm' o 'dBFS'.")
    
    def execute_welch(self, iq_data: np.ndarray, scale: str = 'dBm'):
        """
        Calcula la PSD para un array de datos IQ dado.

        Retorno:
            - Si self.with_shift == True: (frecuencias, Pxx_scaled)
            - Si self.with_shift == False: Pxx_scaled
        """
        n_samples = len(iq_data)
        nperseg = self.desired_nperseg
        
        # --- Validación Crítica ---
        # Comprueba si tenemos suficientes muestras para el RBW deseado
        if n_samples < nperseg:
            nperseg = n_samples 

        # Calcula el solapamiento real en muestras
        noverlap = int(nperseg * self.overlap)
        
        # Genera el eje de frecuencias SOLO si requested
        f = None
        if self.with_shift:
            N_psd = nperseg 
            f_base = np.fft.fftfreq(N_psd, d=1/self.fs)
            f_shifted = np.fft.fftshift(f_base)
            f = f_shifted + self.freq # Centra en la frecuencia de la portadora
        
        # Calcula la PSD y aplica la escala
        Pxx_scaled = self._scale_signal(iq_data, nperseg, noverlap, scale)
        
        if self.with_shift:
            return f, Pxx_scaled
        else:
            return Pxx_scaled
    

class CampaignHackRF:
    """
    Simple wrapper to acquire samples with pyhackrf2 and calculate PSD.
    """
    def __init__(self, start_freq_hz: int, end_freq_hz: int, 
                 sample_rate_hz: int, resolution_hz: int, 
                 scale: str = 'dBm', verbose: bool = False, remove_dc_spike: bool = True, 
                 log=logging.getLogger(__name__),
                 **kwargs):
        
        self.freq = int(((end_freq_hz - start_freq_hz)/2) + start_freq_hz)
        self.sample_rate_hz = sample_rate_hz
        self.resolution_hz = resolution_hz
        self.scale = scale
        self.verbose = verbose
        self._log = log
        self.iq = None
        self.remove_dc_spike = remove_dc_spike

        # Standard params
        self.window = kwargs.get("window", "hamming")
        self.overlap = kwargs.get("overlap", 0.5)
        self.lna_gain = kwargs.get("lna_gain", 0)
        self.vga_gain = kwargs.get("vga_gain", 0)
        self.antenna_amp = kwargs.get("antenna_amp", False)
        self.r_ant = kwargs.get("r_ant", 50.0)

    def init_hack(self) -> Optional[HackRF]:
        try:
            return HackRF()
        except Exception as e:
            self._log.error(f"Error opening HackRF: {e}")
            return None
    
    def acquire_hackrf(self) -> int:
        hack = self.init_hack()
        if hack is None: return 1
        
        hack.center_freq = self.freq
        hack.sample_rate = self.sample_rate_hz
        hack.amplifier_on = self.antenna_amp
        hack.lna_gain = self.lna_gain
        hack.vga_gain = self.vga_gain
        
        try:
            # Drop first samples to let LO settle
            hack.read_samples(4096)
            self.iq = hack.read_samples(hack.sample_rate)
            if self.verbose:
                self._log.info(f"Acquired {len(self.iq)} samples at {self.freq}Hz")
        except Exception as e:
            self._log.error(f"Error reading samples: {e}")
            return 1
        finally:
            hack.close()

        return 0
    
    def get_psd(self) -> Tuple[Optional[np.ndarray], Optional[np.ndarray]]:
        """
        Acquires samples and calculates PSD. 
        If remove_dc_spike is True, performs a secondary acquisition to stitch the center.
        """
        # 1. Main Acquisition (Center Freq)
        est_main = WelchEstimator(self.freq, self.sample_rate_hz, self.resolution_hz, 
                                  r_ant=self.r_ant, window=self.window, overlap=self.overlap)
        
        if self.acquire_hackrf() != 0 or self.iq is None:
            return None, None
            
        f_main, pxx_main = est_main.execute_welch(self.iq, self.scale)
        
        # If no spike removal needed, return immediately
        if not self.remove_dc_spike:
            return f_main, pxx_main

        # 2. Secondary Acquisition (Offset by fs/4) to get clean center
        original_freq = self.freq
        offset = self.sample_rate_hz / 4
        
        # Move LO
        self.freq = int(original_freq + offset)
        
        est_offset = WelchEstimator(self.freq, self.sample_rate_hz, self.resolution_hz,
                                    r_ant=self.r_ant, window=self.window, overlap=self.overlap)

        if self.acquire_hackrf() != 0 or self.iq is None:
            self.freq = original_freq # Restore freq on fail
            return None, None
            
        f_offset, pxx_offset = est_offset.execute_welch(self.iq, self.scale)
        self.freq = original_freq # Restore freq

        # 3. Stitching
        BW_SPIKE = 300_000 # 300 kHz notch width
        
        # Identify "Bad" indices in main capture (around original center)
        bad_mask = (f_main >= (original_freq - BW_SPIKE/2)) & \
                   (f_main <= (original_freq + BW_SPIKE/2))
        
        # Interpolate clean values from offset capture into the bad region
        # np.interp(x_target, x_source, y_source)
        pxx_main[bad_mask] = np.interp(f_main[bad_mask], f_offset, pxx_offset)
        
        return f_main, pxx_main