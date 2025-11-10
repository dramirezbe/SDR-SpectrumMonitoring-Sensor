"""
@file utils/sdr_util.py
@brief Dummy utilities to simulate SDR operations (IQ and PSD acquisition).
"""

from __future__ import annotations

from pathlib import Path
from typing import Union

import numpy as np
import scipy.signal as sig

class AcquireFrame:
    """
    Dummy SDR frame acquisition class.
    Simulates IQ data capture and PSD computation for testing.
    """

    def __init__(
        self,
        start_freq_hz: int,
        end_freq_hz: int,
        resolution_hz: int
    ) -> None:
        self.start_freq_hz = start_freq_hz
        self.end_freq_hz = end_freq_hz
        self.resolution_hz = resolution_hz

    # --------------------------------------------------------------------------
    # 1. Genera archivo binario con IQ simulados (uint8 intercalados)
    # --------------------------------------------------------------------------
    def create_IQ(self, path: Path) -> Path:
        """
        Create a dummy IQ binary file (.cs8) with interleaved uint8 samples.
        Example: [I,Q,I,Q,...].

        Returns:
            Path to the created file.
        """

        path.mkdir(parents=True, exist_ok=True)
        file_path = path / "0.cs8"

        N = int(1e6)  # 1 millón de muestras (dummy)
        iq = np.random.randint(0, 256, size=(N, 2), dtype=np.uint8)
        iq.tofile(file_path)

        return file_path

    # --------------------------------------------------------------------------
    # 2. Lee archivo binario y devuelve IQ array
    # --------------------------------------------------------------------------
    def get_IQ(self, path:Path) -> np.ndarray:
        """
        Read an IQ file (.cs8) and return a numpy array of shape (N, 2).
        """
        p = path / "0.cs8"
        if not p.is_file():
            raise FileNotFoundError(f"IQ file not found at: {p}")

        data = np.fromfile(p, dtype=np.uint8)
        iq = data.reshape(-1, 2)
        return iq

    # --------------------------------------------------------------------------
    # 3. Calcula PSD, borra el archivo después
    # --------------------------------------------------------------------------
    def get_psd(self, path: Path) -> np.ndarray:
        """
        Compute Power Spectral Density (PSD) from stored IQ data file.
        Deletes the file after analysis.

        Returns:
            1-D numpy array with PSD values.
        """
        p = path / "0.cs8"
        if not p.is_file():
            raise FileNotFoundError(f"IQ file not found at: {p}")

        # leer y convertir a float32 para el cómputo
        iq = np.fromfile(p, dtype=np.uint8).reshape(-1, 2).astype(np.float32)
        signal = iq[:, 0] + 1j * iq[:, 1]

        # sig.welch espera fs (sample rate); aquí usamos resolution_hz tal como antes
        freqs, psd = sig.welch(signal, fs=self.resolution_hz, window="hann")

        # borrar archivo después del análisis
        p.unlink()

        return psd
