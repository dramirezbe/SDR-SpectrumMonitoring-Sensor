import numpy as np
from utils.libs_DSP import SignalProcessingUtils


class SpectralContentAnalysisUtils:
    """
    Utilidades para analizar contenido espectral mediante
    histogramas y selección de regiones centradas de la PSD.
    """

    @staticmethod
    def _fit_linear_slope(x_region):
        """
        Ajusta una recta y = a*n + b sobre una región 1D y retorna:
        - slope: pendiente a
        - intercept: término independiente
        - r2: coeficiente de determinación simple
        """
        x_region = np.asarray(x_region, dtype=float)
        x_region = x_region[np.isfinite(x_region)]

        if len(x_region) < 2:
            return 0.0, float(x_region[0]) if len(x_region) == 1 else 0.0, 0.0

        n = np.arange(len(x_region), dtype=float)

        slope, intercept = np.polyfit(n, x_region, 1)

        y_hat = slope * n + intercept
        ss_res = float(np.sum((x_region - y_hat) ** 2))
        ss_tot = float(np.sum((x_region - np.mean(x_region)) ** 2))

        if ss_tot <= 1e-12:
            r2 = 0.0
        else:
            r2 = 1.0 - ss_res / ss_tot

        return float(slope), float(intercept), float(r2)


    @staticmethod
    def _analyze_lateral_slopes_around_initial_window(
        power_dbm,
        detected_half_width,
        expansion_multiplier=2.5,
        min_side_bins=6,
        slope_near_zero_thresh=0.02
    ):
        """
        Analiza las regiones laterales entre la ventana inicial detectada
        y la ventana ampliada por expansion_multiplier.

        Si las pendientes laterales están cerca de cero, se interpreta
        como comportamiento compatible con ruido/fondo localmente plano.
        """
        x = np.asarray(power_dbm, dtype=float)
        N = len(x)
        center_idx = N // 2

        detected_half_width = int(max(1, detected_half_width))
        expanded_half_width = int(np.ceil(expansion_multiplier * detected_half_width))
        expanded_half_width = min(expanded_half_width, center_idx, N - center_idx - 1)

        # Región lateral izquierda: [center-expanded, center-detected-1]
        left_start = center_idx - expanded_half_width
        left_end = center_idx - detected_half_width - 1

        # Región lateral derecha: [center+detected+1, center+expanded]
        right_start = center_idx + detected_half_width + 1
        right_end = center_idx + expanded_half_width

        left_idx = (
            np.arange(left_start, left_end + 1, dtype=int)
            if left_end >= left_start else np.array([], dtype=int)
        )
        right_idx = (
            np.arange(right_start, right_end + 1, dtype=int)
            if right_end >= right_start else np.array([], dtype=int)
        )

        x_left = x[left_idx] if len(left_idx) > 0 else np.array([], dtype=float)
        x_right = x[right_idx] if len(right_idx) > 0 else np.array([], dtype=float)

        enough_left = len(x_left) >= min_side_bins
        enough_right = len(x_right) >= min_side_bins

        left_slope, left_intercept, left_r2 = (
            SpectralContentAnalysisUtils._fit_linear_slope(x_left)
            if enough_left else (0.0, 0.0, 0.0)
        )

        right_slope, right_intercept, right_r2 = (
            SpectralContentAnalysisUtils._fit_linear_slope(x_right)
            if enough_right else (0.0, 0.0, 0.0)
        )

        left_near_zero = (abs(left_slope) <= slope_near_zero_thresh) if enough_left else True
        right_near_zero = (abs(right_slope) <= slope_near_zero_thresh) if enough_right else True

        slopes_support_low_content = left_near_zero and right_near_zero

        info = {
            "center_idx": int(center_idx),
            "detected_half_width": int(detected_half_width),
            "expanded_half_width": int(expanded_half_width),
            "left_slice": (
                (int(left_idx[0]), int(left_idx[-1])) if len(left_idx) > 0 else None
            ),
            "right_slice": (
                (int(right_idx[0]), int(right_idx[-1])) if len(right_idx) > 0 else None
            ),
            "left_n": int(len(x_left)),
            "right_n": int(len(x_right)),
            "enough_left": bool(enough_left),
            "enough_right": bool(enough_right),
            "left_slope": float(left_slope),
            "right_slope": float(right_slope),
            "left_intercept": float(left_intercept),
            "right_intercept": float(right_intercept),
            "left_r2": float(left_r2),
            "right_r2": float(right_r2),
            "slope_near_zero_thresh": float(slope_near_zero_thresh),
            "left_near_zero": bool(left_near_zero),
            "right_near_zero": bool(right_near_zero),
            "slopes_support_low_content": bool(slopes_support_low_content),
        }

        return slopes_support_low_content, info
    

    # ============================================================
    # 1) MEDIA Y MEDIANA A PARTIR DEL HISTOGRAMA
    # ============================================================
    @staticmethod
    def histogram_mean_median(
        x,
        bins="fd",
        min_bins=24,
        max_bins=96
    ):
        """
        Estima media y mediana a partir del histograma de frecuencias.
        """
        x = np.asarray(x, dtype=float)
        x = x[np.isfinite(x)]

        if len(x) == 0:
            raise ValueError("No hay datos válidos para construir el histograma.")

        if np.allclose(np.max(x), np.min(x)):
            val = float(x[0])
            counts = np.array([len(x)], dtype=float)
            edges = np.array([val - 0.5, val + 0.5], dtype=float)
            centers = np.array([val], dtype=float)
            return val, val, counts, edges, centers

        if isinstance(bins, str):
            edges0 = np.histogram_bin_edges(x, bins=bins)
            n_bins = len(edges0) - 1
        else:
            n_bins = int(bins)

        n_bins = max(min_bins, n_bins)
        n_bins = min(max_bins, n_bins)

        counts, edges = np.histogram(x, bins=n_bins)
        counts = counts.astype(float)

        centers = 0.5 * (edges[:-1] + edges[1:])
        total = np.sum(counts)

        if total <= 0:
            raise ValueError("El histograma quedó vacío.")

        hist_mean = float(np.sum(centers * counts) / total)

        cdf = np.cumsum(counts)
        half = 0.5 * total
        idx = int(np.searchsorted(cdf, half, side="left"))
        idx = np.clip(idx, 0, len(counts) - 1)

        left_edge = edges[idx]
        right_edge = edges[idx + 1]
        bin_count = counts[idx]

        cdf_before = cdf[idx - 1] if idx > 0 else 0.0

        if bin_count <= 0:
            hist_median = float(0.5 * (left_edge + right_edge))
        else:
            frac = (half - cdf_before) / bin_count
            frac = np.clip(frac, 0.0, 1.0)
            hist_median = float(left_edge + frac * (right_edge - left_edge))

        return hist_mean, hist_median, counts, edges, centers

    # ============================================================
    # 2) EXTRAER REGIÓN CENTRADA EXCLUYENDO DC
    # ============================================================
    @staticmethod
    def extract_centered_analysis_without_dc(
        power_dbm,
        center_fraction=0.10,
        exclusion_half_width=0
    ):
        """
        Extrae solo una fracción centrada de la PSD, excluyendo
        una ventana central alrededor del DC.
        """
        x = np.asarray(power_dbm, dtype=float)
        N = len(x)
        center_idx = N // 2

        analysis_half_width = int(np.ceil((center_fraction * N) / 2.0))
        analysis_half_width = max(analysis_half_width, exclusion_half_width + 3)
        analysis_half_width = min(analysis_half_width, center_idx, N - center_idx - 1)

        if analysis_half_width < 3:
            return np.array([], dtype=float), {
                "center_idx": int(center_idx),
                "analysis_half_width": int(analysis_half_width),
                "analysis_slice": (int(center_idx), int(center_idx)),
                "excluded_slice": (int(center_idx), int(center_idx)),
                "selected_idx": np.array([], dtype=int)
            }

        a0 = center_idx - analysis_half_width
        a1 = center_idx + analysis_half_width

        e0 = max(a0, center_idx - exclusion_half_width)
        e1 = min(a1, center_idx + exclusion_half_width)

        left_idx = np.arange(a0, e0, dtype=int) if e0 > a0 else np.array([], dtype=int)
        right_idx = np.arange(e1 + 1, a1 + 1, dtype=int) if e1 < a1 else np.array([], dtype=int)

        selected_idx = np.concatenate([left_idx, right_idx])

        x_sel = x[selected_idx] if len(selected_idx) > 0 else np.array([], dtype=float)

        info = {
            "center_idx": int(center_idx),
            "analysis_half_width": int(analysis_half_width),
            "analysis_slice": (int(a0), int(a1)),
            "excluded_slice": (int(e0), int(e1)),
            "selected_idx": selected_idx
        }

        return x_sel, info

    # ============================================================
    # 3) MÉTRICA AUXILIAR DE COLA ALTA
    # ============================================================
    @staticmethod
    def _compute_high_tail_metrics(
        x_sel,
        hist_median,
        high_tail_sigma_factor
    ):
        """
        Calcula escala robusta y fracción de cola alta
        sobre la región seleccionada.
        """
        sigma_rob = float(
            SignalProcessingUtils.safe_robust_scale(x_sel, floor=1e-4)
        )

        high_tail_threshold = hist_median + high_tail_sigma_factor * sigma_rob
        high_tail_fraction = float(np.mean(x_sel > high_tail_threshold))

        return sigma_rob, high_tail_threshold, high_tail_fraction

    # ============================================================
    # 4) TEST DE BAJA OCUPACIÓN ESPECTRAL
    # ============================================================
    @staticmethod
    @staticmethod
    def detect_low_spectral_content_by_histogram_mean_median(
        power_dbm,
        detected_half_width,
        center_fraction=0.10,
        central_exclusion_multiplier=2.5,
        histogram_bins="fd",
        histogram_min_bins=24,
        histogram_max_bins=96,
        mean_median_max_diff_db=0.15,
        high_tail_sigma_factor=2.5,
        max_high_tail_fraction=0.025,
        enable_lateral_slope_stage=True,
        slope_expansion_multiplier=2.5,
        slope_min_side_bins=6,
        slope_near_zero_thresh=0.023,
        debug=False
    ):
        """
        Detecta si la PSD tiene contenido espectral despreciable
        usando:
        1) región centrada excluyendo DC
        2) cercanía entre media y mediana + fracción de cola alta
        3) opcionalmente, pendientes laterales alrededor de la ventana inicial
        """
        x = np.asarray(power_dbm, dtype=float)
        N = len(x)

        exclusion_half_width = int(
            np.ceil(central_exclusion_multiplier * detected_half_width)
        )
        exclusion_half_width = max(exclusion_half_width, detected_half_width)

        x_sel, region_info = SpectralContentAnalysisUtils.extract_centered_analysis_without_dc(
            power_dbm=x,
            center_fraction=center_fraction,
            exclusion_half_width=exclusion_half_width
        )

        if len(x_sel) < 10:
            debug_info = {
                "low_content": False,
                "reason": "Muy pocas muestras después de excluir la región DC.",
                "N_total": int(N),
                "N_selected": int(len(x_sel)),
                "detected_half_width": int(detected_half_width),
                "exclusion_half_width": int(exclusion_half_width),
                **region_info
            }
            return False, debug_info

        hist_mean, hist_median, counts, edges, centers = (
            SpectralContentAnalysisUtils.histogram_mean_median(
                x_sel,
                bins=histogram_bins,
                min_bins=histogram_min_bins,
                max_bins=histogram_max_bins
            )
        )

        mean_minus_median = float(np.abs(hist_mean - hist_median))

        sigma_rob, high_tail_threshold, high_tail_fraction = (
            SpectralContentAnalysisUtils._compute_high_tail_metrics(
                x_sel=x_sel,
                hist_median=hist_median,
                high_tail_sigma_factor=high_tail_sigma_factor
            )
        )

        # ========================================================
        # Etapa 2: criterio preliminar
        # ========================================================
        low_content_stage2 = (
            mean_minus_median <= mean_median_max_diff_db
            and high_tail_fraction <= max_high_tail_fraction
        )

        slope_stage_applied = False
        slopes_support_low_content = True
        slope_info = {
            "left_slope": None,
            "right_slope": None,
            "left_near_zero": None,
            "right_near_zero": None,
            "slopes_support_low_content": None
        }

        # ========================================================
        # Etapa 3: análisis condicional de pendientes laterales
        # ========================================================
        if low_content_stage2 and enable_lateral_slope_stage:
            slope_stage_applied = True

            slopes_support_low_content, slope_info = (
                SpectralContentAnalysisUtils._analyze_lateral_slopes_around_initial_window(
                    power_dbm=x,
                    detected_half_width=detected_half_width,
                    expansion_multiplier=slope_expansion_multiplier,
                    min_side_bins=slope_min_side_bins,
                    slope_near_zero_thresh=slope_near_zero_thresh
                )
            )

        low_content = bool(low_content_stage2 and slopes_support_low_content)

        if not low_content_stage2:
            reason = (
                "Se detecta ocupación espectral apreciable en la etapa histográfica: "
                "la media se separa de la mediana o la cola alta no es despreciable."
            )
        elif slope_stage_applied and not slopes_support_low_content:
            reason = (
                "La etapa histográfica sugiere bajo contenido, pero las pendientes "
                "laterales alrededor de la ventana inicial no son cercanas a cero; "
                "esto sugiere estructura espectral local y se rechaza low_content."
            )
        else:
            reason = (
                "Contenido espectral despreciable: la etapa histográfica es compatible "
                "con ruido/fondo y las pendientes laterales son cercanas a cero."
            )

        debug_info = {
            "low_content": bool(low_content),
            "low_content_stage2": bool(low_content_stage2),
            "slope_stage_applied": bool(slope_stage_applied),
            "reason": reason,
            "N_total": int(N),
            "N_selected": int(len(x_sel)),
            "detected_half_width": int(detected_half_width),
            "exclusion_half_width": int(exclusion_half_width),
            "hist_mean": float(hist_mean),
            "hist_median": float(hist_median),
            "mean_minus_median": float(mean_minus_median),
            "mean_median_max_diff_db": float(mean_median_max_diff_db),
            "sigma_rob": float(sigma_rob),
            "high_tail_threshold": float(high_tail_threshold),
            "high_tail_fraction": float(high_tail_fraction),
            "max_high_tail_fraction": float(max_high_tail_fraction),
            "hist_counts": counts,
            "hist_edges": edges,
            "hist_centers": centers,
            **region_info,
            **slope_info
        }

        if debug:
            print("\n[DEBUG LOW SPECTRAL CONTENT TEST]")
            print("  detected_half_width:", detected_half_width)
            print("  exclusion_half_width:", exclusion_half_width)
            print("  analysis_slice:", region_info["analysis_slice"])
            print("  excluded_slice:", region_info["excluded_slice"])
            print("  N_selected:", len(x_sel))
            print("  hist_mean:", hist_mean)
            print("  hist_median:", hist_median)
            print("  mean_minus_median:", mean_minus_median)
            print("  sigma_rob:", sigma_rob)
            print("  high_tail_threshold:", high_tail_threshold)
            print("  high_tail_fraction:", high_tail_fraction)
            print("  low_content_stage2:", low_content_stage2)
            print("  slope_stage_applied:", slope_stage_applied)

            if slope_stage_applied:
                print("  left_slope:", slope_info["left_slope"])
                print("  right_slope:", slope_info["right_slope"])
                print("  left_near_zero:", slope_info["left_near_zero"])
                print("  right_near_zero:", slope_info["right_near_zero"])
                print("  slopes_support_low_content:", slope_info["slopes_support_low_content"])

            print("  low_content_final:", low_content)
            print("  reason:", reason)

        return low_content, debug_info
