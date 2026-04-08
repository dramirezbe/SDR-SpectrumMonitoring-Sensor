import numpy as np
from utils.libs_DSP import WindowReconstructionUtils
from utils.dc_spike_detection import DCSpikeDetectionUtils
from utils.spectral_content_analysis import SpectralContentAnalysisUtils


class DCSpikeRemovalPipeline:
    """
    Pipeline principal de remoción adaptativa de DC spike.

    Flujo:
    1) Detecta el semi-ancho del DC spike por coherencia simétrica de pendientes.
    2) Evalúa baja ocupación espectral local en la región centrada,
       excluyendo una ventana alrededor del DC.
    3) Si hay baja ocupación espectral, amplía la ventana detectada.
    4) Reconstruye la región removida.
    5) Selecciona reconstrucción lineal o polinómica según termination_mode.
    """

    # ============================================================
    # 1) DETECCIÓN INICIAL
    # ============================================================
    @staticmethod
    def _detect_initial_dc_region(
        x,
        analysis_fraction,
        smooth_window,
        slope_smooth_window,
        min_half_width,
        debug
    ):
        """
        Ejecuta la detección inicial de la región DC.
        """
        detected_half_width, detect_info = (
            DCSpikeDetectionUtils.detect_dc_spike_region_by_symmetric_slope(
                power_dbm=x,
                analysis_fraction=analysis_fraction,
                smooth_window=smooth_window,
                slope_smooth_window=slope_smooth_window,
                min_half_width=min_half_width,
                debug=debug
            )
        )

        return int(detected_half_width), detect_info

    # ============================================================
    # 2) TEST DE BAJA OCUPACIÓN + EXPANSIÓN
    # ============================================================
    @staticmethod
    def _evaluate_low_content_and_expand(
        x,
        detected_half_width,
        min_half_width,
        center_idx,
        enable_low_content_expansion,
        low_content_center_fraction,
        low_content_exclusion_multiplier,
        low_content_expand_factor,
        low_content_histogram_bins,
        low_content_histogram_min_bins,
        low_content_histogram_max_bins,
        low_content_mean_median_max_diff_db,
        low_content_high_tail_sigma_factor,
        low_content_max_high_tail_fraction,
        debug
    ):
        """
        Evalúa si hay baja ocupación espectral local y, si aplica,
        expande el semi-ancho detectado.
        """
        N = len(x)

        low_content_flag = False
        low_content_info = {
            "low_content": False,
            "reason": "Test no ejecutado."
        }

        expanded_half_width = int(detected_half_width)

        if enable_low_content_expansion:
            low_content_flag, low_content_info = (
                SpectralContentAnalysisUtils.detect_low_spectral_content_by_histogram_mean_median(
                    power_dbm=x,
                    detected_half_width=detected_half_width,
                    center_fraction=low_content_center_fraction,
                    central_exclusion_multiplier=low_content_exclusion_multiplier,
                    histogram_bins=low_content_histogram_bins,
                    histogram_min_bins=low_content_histogram_min_bins,
                    histogram_max_bins=low_content_histogram_max_bins,
                    mean_median_max_diff_db=low_content_mean_median_max_diff_db,
                    high_tail_sigma_factor=low_content_high_tail_sigma_factor,
                    max_high_tail_fraction=low_content_max_high_tail_fraction,
                    debug=debug
                )
            )

            if low_content_flag:
                expanded_half_width = int(
                    np.ceil(low_content_expand_factor * detected_half_width)
                )

        max_valid_half_width = min(center_idx, N - center_idx - 1)
        expanded_half_width = int(
            np.clip(expanded_half_width, min_half_width, max_valid_half_width)
        )

        return expanded_half_width, low_content_flag, low_content_info

    # ============================================================
    # 3) CONSTRUIR VENTANA FINAL DE REPARACIÓN
    # ============================================================
    @staticmethod
    def _build_repair_window(N, center_idx, expanded_half_width):
        """
        Construye la ventana final [i0, i1] a reparar.
        """
        i0 = center_idx - expanded_half_width
        i1 = center_idx + expanded_half_width

        i0 = max(0, i0)
        i1 = min(N - 1, i1)

        return int(i0), int(i1)

    # ============================================================
    # 4) RECONSTRUCCIÓN FINAL
    # ============================================================
    @staticmethod
    def _reconstruct_region(
        x,
        i0,
        i1,
        termination_mode,
        support_bins,
        poly_degree,
        noise_std_db
    ):
        """
        Reconstruye la región removida usando el método adecuado
        según el modo de terminación de la detección.
        """
        if termination_mode == "noise_floor":
            x_filtered, support_idx, reconstructed = (
                WindowReconstructionUtils.fit_linear_reconstruction(
                    x=x,
                    i0=i0,
                    i1=i1,
                    noise_std_db=noise_std_db
                )
            )
            reconstruction_mode = "linear"
        else:
            x_filtered, support_idx, reconstructed = (
                WindowReconstructionUtils.fit_local_polynomial_reconstruction(
                    x=x,
                    i0=i0,
                    i1=i1,
                    support_bins=support_bins,
                    poly_degree=poly_degree,
                    noise_std_db=noise_std_db
                )
            )
            reconstruction_mode = "polynomial"

        return x_filtered, support_idx, reconstructed, reconstruction_mode

    # ============================================================
    # 5) PIPELINE PRINCIPAL
    # ============================================================
    @staticmethod
    def remove_dc_spike_adaptive_symmetric(
        power_dbm,
        analysis_fraction=0.05,
        smooth_window=9,
        slope_smooth_window=7,
        support_bins=14,
        poly_degree=2,
        min_half_width=2,
        debug=False,
        noise_std_db=None,
        enable_low_content_expansion=True,
        low_content_center_fraction=0.10,
        low_content_exclusion_multiplier=2.5,
        low_content_expand_factor=3.0,
        low_content_histogram_bins="fd",
        low_content_histogram_min_bins=24,
        low_content_histogram_max_bins=96,
        low_content_mean_median_max_diff_db=0.11,
        low_content_high_tail_sigma_factor=2.5,
        low_content_max_high_tail_fraction=0.025
    ):
        """
        Pipeline principal de remoción adaptativa de DC spike.

        Parámetros
        ----------
        power_dbm : array-like
            PSD de entrada.
        analysis_fraction : float
            Fracción centrada usada para detección del DC spike.
        smooth_window : int
            Ventana de suavizado para la PSD.
        slope_smooth_window : int
            Ventana de suavizado para pendientes/curvaturas.
        support_bins : int
            Número de bins laterales usados por la reconstrucción polinómica.
        poly_degree : int
            Grado del polinomio de reconstrucción.
        min_half_width : int
            Semi-ancho mínimo permitido.
        debug : bool
            Si True, imprime trazas de depuración.
        noise_std_db : float or None
            Desviación estándar del ruido añadido a la reconstrucción.
        enable_low_content_expansion : bool
            Si True, habilita expansión por baja ocupación espectral.

        Retorna
        -------
        x_filtered : np.ndarray
            PSD filtrada/reconstruida.
        center_idx : int
            Índice central del espectro.
        repair_slice : tuple
            Ventana reparada (i0, i1).
        debug_info : dict
            Información detallada del proceso.
        """
        x = np.asarray(power_dbm, dtype=float).copy()
        N = len(x)
        center_idx = N // 2

        detected_half_width, detect_info = (
            DCSpikeRemovalPipeline._detect_initial_dc_region(
                x=x,
                analysis_fraction=analysis_fraction,
                smooth_window=smooth_window,
                slope_smooth_window=slope_smooth_window,
                min_half_width=min_half_width,
                debug=debug
            )
        )

        original_detected_half_width = int(detected_half_width)

        expanded_half_width, low_content_flag, low_content_info = (
            DCSpikeRemovalPipeline._evaluate_low_content_and_expand(
                x=x,
                detected_half_width=detected_half_width,
                min_half_width=min_half_width,
                center_idx=center_idx,
                enable_low_content_expansion=enable_low_content_expansion,
                low_content_center_fraction=low_content_center_fraction,
                low_content_exclusion_multiplier=low_content_exclusion_multiplier,
                low_content_expand_factor=low_content_expand_factor,
                low_content_histogram_bins=low_content_histogram_bins,
                low_content_histogram_min_bins=low_content_histogram_min_bins,
                low_content_histogram_max_bins=low_content_histogram_max_bins,
                low_content_mean_median_max_diff_db=low_content_mean_median_max_diff_db,
                low_content_high_tail_sigma_factor=low_content_high_tail_sigma_factor,
                low_content_max_high_tail_fraction=low_content_max_high_tail_fraction,
                debug=debug
            )
        )

        i0, i1 = DCSpikeRemovalPipeline._build_repair_window(
            N=N,
            center_idx=center_idx,
            expanded_half_width=expanded_half_width
        )

        termination_mode = detect_info.get("termination_mode", "emission_or_asymmetry")

        x_filtered, support_idx, reconstructed, reconstruction_mode = (
            DCSpikeRemovalPipeline._reconstruct_region(
                x=x,
                i0=i0,
                i1=i1,
                termination_mode=termination_mode,
                support_bins=support_bins,
                poly_degree=poly_degree,
                noise_std_db=noise_std_db
            )
        )

        debug_info = {
            "center_idx": int(center_idx),
            "original_detected_half_width": int(original_detected_half_width),
            "final_half_width": int(expanded_half_width),
            "low_content_expansion_applied": bool(low_content_flag),
            "low_content_expand_factor": float(low_content_expand_factor),
            "repair_slice": (int(i0), int(i1)),
            "support_idx": support_idx,
            "reconstructed": reconstructed,
            "detect_info": detect_info,
            "low_content_info": low_content_info,
            "termination_mode": termination_mode,
            "reconstruction_mode": reconstruction_mode
        }

        if debug:
            print("\n[DEBUG REMOCIÓN DC]")
            print("  Centro:", center_idx)
            print("  Semi-ancho detectado original:", original_detected_half_width)
            print("  ¿Low spectral content?:", low_content_flag)
            print("  Semi-ancho final:", expanded_half_width)
            print("  Ventana reparada:", (i0, i1))
            print("  termination_mode:", termination_mode)
            print("  reconstruction_mode:", reconstruction_mode)

        return x_filtered, center_idx, (i0, i1), debug_info
