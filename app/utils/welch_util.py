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
    Wrapper simple para adquirir muestras con pyhackrf2 y calcular PSD con WelchEstimator.

    Nuevo kwargs:
      - with_shift: bool (por defecto True). Si False, get_psd() devolverá solo Pxx.
    """
    def __init__(self, start_freq_hz: int, end_freq_hz: int, 
                 sample_rate_hz: int, resolution_hz: int, 
                 scale: str = 'dBm', verbose: bool = False, 
                 log=logging.getLogger(__name__),
                 **kwargs):
        
        self.freq = int(((end_freq_hz - start_freq_hz)/2) + start_freq_hz) #centro de freq
        self.sample_rate_hz = sample_rate_hz
        self.resolution_hz = resolution_hz
        self.scale = scale
        self.hack = None
        self.verbose = verbose
        self._log = log
        self.iq = None

        # Control para generar o no el vector de frecuencias en la salida
        self.with_shift = kwargs.get("with_shift", True)

    def init_hack(self):
        try:
            hack = HackRF()
        except Exception as e:
            self._log.error(f"Error al abrir HackRF: {e}")
            hack = None

        return hack
    
    def acquire_hackrf(self)-> int:
        hack = self.init_hack()
        if hack is None:
            return 1
        
        hack.center_freq = self.freq
        hack.sample_rate = self.sample_rate_hz
        hack.amplifier_on = False
        hack.lna_gain = 0
        hack.vga_gain = 0
        
        # read_samples puede ser bloqueante y devolver un array complejo
        self.iq = hack.read_samples(hack.sample_rate)
        if self.verbose:
            self._log.info(f"Acquired {len(self.iq)} samples")

        return 0
    
    def get_psd(self):
        """
        Adquiere muestras y calcula PSD. Retorno condicionado por self.with_shift:
          - with_shift == True  -> (frecuencias, Pxx)
          - with_shift == False -> Pxx
        En caso de fallo de adquisición:
          - with_shift == True  -> (None, None)
          - with_shift == False -> None
        """
        est = WelchEstimator(self.freq, self.sample_rate_hz, 
                               self.resolution_hz, r_ant=50.0,
                               with_shift=self.with_shift)
        
        err = self.acquire_hackrf()
        if err != 0 or self.iq is None:
            if self.with_shift:
                return None, None
            else:
                return None
        
        result = est.execute_welch(self.iq, self.scale)

        # result puede ser (f, Pxx) o Pxx según with_shift
        if self.with_shift:
            f, Pxx_scaled = result
            return f, Pxx_scaled
        else:
            Pxx_scaled = result
            return Pxx_scaled
