
import numpy as np
import pandas as pd

class SignalProcessingUtils:
    """
    Utilidades de procesamiento de señales para análisis de PSD
    y reconstrucción de regiones espectrales.
    """

    # ============================================================
    # 1) GENERACIÓN DE RUIDO
    # ============================================================
    @staticmethod
    def generate_reconstruction_noise(n_samples, noise_std_db=None, rng=None):
        """
        Genera ruido aditivo para la reconstrucción en unidades de dB/dBm.
        """
        if noise_std_db is None:
            noise_std_db = 0.0

        noise_std_db = float(noise_std_db)

        if noise_std_db <= 0.0 or n_samples <= 0:
            return np.zeros(int(n_samples), dtype=float)

        if rng is None:
            rng = np.random.default_rng()

        return rng.normal(loc=0.0, scale=noise_std_db, size=int(n_samples))


    # ============================================================
    # 2) SUAVIZADO
    # ============================================================
    @staticmethod
    def moving_average_edge(x, window):
        """
        Media móvil con padding en bordes para mantener mismo tamaño.
        """
        x = np.asarray(x, dtype=float)

        if window < 3 or len(x) < 3:
            return x.copy()

        if window % 2 == 0:
            window += 1

        window = min(window, len(x) if len(x) % 2 == 1 else len(x) - 1)
        if window < 3:
            return x.copy()

        pad = window // 2
        xpad = np.pad(x, pad_width=pad, mode="edge")

        kernel = np.ones(window, dtype=float) / window
        y = np.convolve(xpad, kernel, mode="same")

        return y[pad:-pad]


    # ============================================================
    # 3) ESTIMADORES ROBUSTOS
    # ============================================================
    @staticmethod
    def robust_mad(x):
        """
        Estimador robusto de dispersión basado en MAD.
        """
        x = np.asarray(x, dtype=float)

        if len(x) == 0:
            return 0.0

        med = np.median(x)
        mad = np.median(np.abs(x - med))

        return 1.4826 * mad


    @staticmethod
    def safe_robust_scale(x, floor=1e-6):
        """
        Escala robusta con piso mínimo.
        """
        s = SignalProcessingUtils.robust_mad(x)
        return float(max(s, floor))


    # ============================================================
    # 4) OPERADORES DIFERENCIALES
    # ============================================================
    @staticmethod
    def first_difference(x):
        """
        Primera diferencia discreta.
        """
        x = np.asarray(x, dtype=float)

        if len(x) < 2:
            return np.array([], dtype=float)

        return np.diff(x)


    @staticmethod
    def second_difference(x):
        """
        Segunda diferencia discreta.
        """
        x = np.asarray(x, dtype=float)

        if len(x) < 3:
            return np.array([], dtype=float)

        return x[2:] - 2.0 * x[1:-1] + x[:-2]
    


class WindowReconstructionUtils:
    """
    Utilidades para reconstrucción de ventanas removidas
    dentro de una PSD o señal 1D.
    """

    # ============================================================
    # 1) RECONSTRUCCIÓN POLINÓMICA LOCAL
    # ============================================================
    @staticmethod
    def fit_local_polynomial_reconstruction(
        x,
        i0,
        i1,
        support_bins=10,
        poly_degree=2,
        noise_std_db=None,
        rng=None
    ):
        """
        Reconstruye la ventana [i0, i1] usando un ajuste polinómico
        sobre bins laterales de soporte y añade ruido opcional.

        Parámetros
        ----------
        x : array-like
            Señal original.
        i0 : int
            Índice inicial de la ventana a reconstruir.
        i1 : int
            Índice final de la ventana a reconstruir.
        support_bins : int, optional
            Número de bins de soporte a cada lado.
        poly_degree : int, optional
            Grado del polinomio de ajuste.
        noise_std_db : float or None, optional
            Desviación estándar del ruido aditivo.
        rng : np.random.Generator or None, optional
            Generador aleatorio.

        Retorna
        -------
        y : np.ndarray
            Señal reconstruida completa.
        support_idx : np.ndarray
            Índices usados como soporte para el ajuste.
        reconstructed_noisy : np.ndarray
            Segmento reconstruido con ruido.
        """
        x = np.asarray(x, dtype=float)
        N = len(x)
        y = x.copy()

        left_start = max(0, i0 - support_bins)
        left_end = i0 - 1

        right_start = i1 + 1
        right_end = min(N - 1, i1 + support_bins)

        left_idx = (
            np.arange(left_start, left_end + 1)
            if left_end >= left_start
            else np.array([], dtype=int)
        )
        right_idx = (
            np.arange(right_start, right_end + 1)
            if right_end >= right_start
            else np.array([], dtype=int)
        )

        support_idx = np.concatenate([left_idx, right_idx])

        if len(support_idx) < 2:
            return y, support_idx, y[i0:i1 + 1].copy()

        k = support_idx.astype(float)
        v = x[support_idx]

        deg = min(poly_degree, len(k) - 1)
        deg = max(deg, 1)

        coeffs = np.polyfit(k, v, deg=deg)
        poly = np.poly1d(coeffs)

        repair_idx = np.arange(i0, i1 + 1, dtype=float)
        reconstructed = poly(repair_idx)

        noise = SignalProcessingUtils.generate_reconstruction_noise(
            n_samples=len(reconstructed),
            noise_std_db=noise_std_db,
            rng=rng
        )

        reconstructed_noisy = reconstructed + noise
        y[i0:i1 + 1] = reconstructed_noisy

        return y, support_idx, reconstructed_noisy

    # ============================================================
    # 2) RECONSTRUCCIÓN LINEAL
    # ============================================================
    @staticmethod
    def fit_linear_reconstruction(
        x,
        i0,
        i1,
        noise_std_db=None,
        rng=None
    ):
        """
        Reconstruye linealmente la ventana [i0, i1] usando
        los puntos inmediatamente exteriores y añade ruido opcional.

        Parámetros
        ----------
        x : array-like
            Señal original.
        i0 : int
            Índice inicial de la ventana a reconstruir.
        i1 : int
            Índice final de la ventana a reconstruir.
        noise_std_db : float or None, optional
            Desviación estándar del ruido aditivo.
        rng : np.random.Generator or None, optional
            Generador aleatorio.

        Retorna
        -------
        y : np.ndarray
            Señal reconstruida completa.
        support_idx : np.ndarray
            Índices usados como soporte.
        reconstructed_noisy : np.ndarray
            Segmento reconstruido con ruido.
        """
        x = np.asarray(x, dtype=float)
        N = len(x)
        y = x.copy()

        left_idx = i0 - 1
        right_idx = i1 + 1

        if left_idx < 0 or right_idx >= N:
            return y, np.array([], dtype=int), y[i0:i1 + 1].copy()

        x0, y0 = float(left_idx), float(x[left_idx])
        x1, y1 = float(right_idx), float(x[right_idx])

        repair_idx = np.arange(i0, i1 + 1, dtype=float)

        if x1 == x0:
            reconstructed = np.full(len(repair_idx), y0, dtype=float)
        else:
            reconstructed = y0 + (y1 - y0) * (repair_idx - x0) / (x1 - x0)

        noise = SignalProcessingUtils.generate_reconstruction_noise(
            n_samples=len(reconstructed),
            noise_std_db=noise_std_db,
            rng=rng
        )

        reconstructed_noisy = reconstructed + noise
        y[i0:i1 + 1] = reconstructed_noisy

        support_idx = np.array([left_idx, right_idx], dtype=int)

        return y, support_idx, reconstructed_noisy
    

