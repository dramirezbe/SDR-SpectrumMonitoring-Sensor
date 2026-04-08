from utils.libs_DSP import SignalProcessingUtils
import numpy as np

class DCSpikeDetectionUtils:
    """
    Utilidades para detección de región de DC spike a partir
    del análisis simétrico de pendientes y curvaturas.
    """

    # ============================================================
    # 1) VALIDACIÓN Y CONFIGURACIÓN INICIAL
    # ============================================================
    @staticmethod
    def _prepare_input(power_dbm, analysis_fraction, min_half_width):
        """
        Convierte la entrada a ndarray y calcula parámetros básicos
        del rango de análisis.
        """
        x = np.asarray(power_dbm, dtype=float)
        N = len(x)
        center_idx = N // 2

        analysis_half_width = int(np.ceil((analysis_fraction * N) / 2.0))
        analysis_half_width = max(analysis_half_width, min_half_width + 5)
        analysis_half_width = min(
            analysis_half_width,
            center_idx - 3,
            N - center_idx - 4
        )

        return x, N, center_idx, analysis_half_width

    # ============================================================
    # 2) CONSTRUCCIÓN DE PERFILES
    # ============================================================
    @staticmethod
    def _build_symmetric_profiles(x, center_idx, analysis_half_width, smooth_window):
        """
        Suaviza la señal y construye perfiles izquierdo y derecho
        orientados desde el centro hacia afuera.
        """
        x_smooth = SignalProcessingUtils.moving_average_edge(x, smooth_window)

        left_profile = x_smooth[center_idx - analysis_half_width:center_idx + 1][::-1]
        right_profile = x_smooth[center_idx:center_idx + analysis_half_width + 1]

        return x_smooth, left_profile, right_profile

    # ============================================================
    # 3) CÁLCULO DE SLOPES Y CURVATURAS
    # ============================================================
    @staticmethod
    def _compute_profile_metrics(left_profile, right_profile, slope_smooth_window):
        """
        Calcula pendientes y curvaturas discretas suavizadas
        para ambos perfiles.
        """
        slope_left = SignalProcessingUtils.first_difference(left_profile)
        slope_right = SignalProcessingUtils.first_difference(right_profile)

        slope_left_s = SignalProcessingUtils.moving_average_edge(
            slope_left, slope_smooth_window
        )
        slope_right_s = SignalProcessingUtils.moving_average_edge(
            slope_right, slope_smooth_window
        )

        curv_left = SignalProcessingUtils.second_difference(left_profile)
        curv_right = SignalProcessingUtils.second_difference(right_profile)

        curv_left_s = SignalProcessingUtils.moving_average_edge(
            curv_left, slope_smooth_window
        )
        curv_right_s = SignalProcessingUtils.moving_average_edge(
            curv_right, slope_smooth_window
        )

        return {
            "slope_left_s": slope_left_s,
            "slope_right_s": slope_right_s,
            "curv_left_s": curv_left_s,
            "curv_right_s": curv_right_s,
        }

    # ============================================================
    # 4) UMBRALES ROBUSTOS
    # ============================================================
    @staticmethod
    def _estimate_thresholds(
        slope_left_s,
        slope_right_s,
        curv_left_s,
        curv_right_s,
        slope_zero_factor,
        slope_asym_abs_factor,
        curvature_factor
    ):
        """
        Estima escalas robustas y umbrales a partir de la región externa
        del rango analizado.
        """
        outer_start_slope = max(0, int(0.65 * len(slope_left_s)))
        outer_slopes = np.concatenate([
            slope_left_s[outer_start_slope:],
            slope_right_s[outer_start_slope:]
        ])

        outer_start_curv = max(0, int(0.65 * len(curv_left_s)))
        outer_curv = np.concatenate([
            curv_left_s[outer_start_curv:],
            curv_right_s[outer_start_curv:]
        ])

        slope_scale = SignalProcessingUtils.safe_robust_scale(outer_slopes, floor=1e-4)
        curv_scale = SignalProcessingUtils.safe_robust_scale(outer_curv, floor=1e-4)

        thresholds = {
            "slope_scale": float(slope_scale),
            "curv_scale": float(curv_scale),
            "slope_zero_thresh": float(slope_zero_factor * slope_scale),
            "slope_asym_abs_thresh": float(slope_asym_abs_factor * slope_scale),
            "curvature_thresh": float(curvature_factor * curv_scale),
        }

        return thresholds

    # ============================================================
    # 5) EVALUACIÓN LOCAL EN CADA k
    # ============================================================
    @staticmethod
    def _evaluate_k(
        k,
        slope_left_s,
        slope_right_s,
        curv_left_s,
        curv_right_s,
        slope_zero_thresh,
        slope_asym_abs_thresh,
        slope_asym_rel,
        curvature_thresh
    ):
        """
        Evalúa las métricas y reglas locales en una posición k.
        """
        sL = slope_left_s[k]
        sR = slope_right_s[k]

        near_zero_L = abs(sL) <= slope_zero_thresh
        near_zero_R = abs(sR) <= slope_zero_thresh

        dc_like_L = sL < -slope_zero_thresh
        dc_like_R = sR < -slope_zero_thresh

        mean_mag = 0.5 * (abs(sL) + abs(sR)) + 1e-12
        slope_diff = abs(sL - sR)

        slope_asym = slope_diff > max(slope_asym_abs_thresh, slope_asym_rel * mean_mag)

        cL = abs(curv_left_s[k - 1]) if (k - 1) < len(curv_left_s) else 0.0
        cR = abs(curv_right_s[k - 1]) if (k - 1) < len(curv_right_s) else 0.0
        abrupt_change = (cL > curvature_thresh) or (cR > curvature_thresh)

        one_side_breaks = (dc_like_L and not dc_like_R) or (dc_like_R and not dc_like_L)

        return {
            "slope_left": float(sL),
            "slope_right": float(sR),
            "near_zero_L": bool(near_zero_L),
            "near_zero_R": bool(near_zero_R),
            "dc_like_L": bool(dc_like_L),
            "dc_like_R": bool(dc_like_R),
            "slope_diff": float(slope_diff),
            "slope_asym": bool(slope_asym),
            "curv_left": float(cL),
            "curv_right": float(cR),
            "abrupt_change": bool(abrupt_change),
            "one_side_breaks": bool(one_side_breaks),
        }

    # ============================================================
    # 6) DETECCIÓN PRINCIPAL
    # ============================================================
    @staticmethod
    def detect_dc_spike_region_by_symmetric_slope(
        power_dbm,
        analysis_fraction=0.05,
        smooth_window=9,
        slope_smooth_window=7,
        min_half_width=2,
        consecutive_confirm=4,
        slope_zero_factor=2.0,
        slope_asym_rel=0.85,
        slope_asym_abs_factor=2.5,
        curvature_factor=3.0,
        debug=False
    ):
        """
        Detecta el semi-ancho del DC spike analizando desde el centro hacia afuera.

        Idea:
        - Se suaviza la PSD.
        - Se construyen perfiles izquierda y derecha ordenados centro -> afuera.
        - Se analizan pendientes simétricas.
        - El DC se considera vigente mientras ambos lados mantengan una
          pendiente descendente coherente.
        - El límite del DC se detecta cuando ocurre de forma sostenida:
            1) pendiente ~ 0 (piso de ruido),
            2) cambio abrupto de pendiente,
            3) pérdida de simetría,
            4) un lado deja de parecer DC y el otro no.

        Retorna
        -------
        detected_half_width : int
            Semi-ancho detectado.
        debug_info : dict
            Diccionario con información detallada del proceso.
        """
        x, N, center_idx, analysis_half_width = DCSpikeDetectionUtils._prepare_input(
            power_dbm=power_dbm,
            analysis_fraction=analysis_fraction,
            min_half_width=min_half_width
        )

        if analysis_half_width <= min_half_width + 1:
            return min_half_width, {
                "reason": "No hay suficientes bins para análisis alrededor del centro.",
                "analysis_half_width": int(analysis_half_width),
                "center_idx": int(center_idx),
                "termination_mode": "insufficient_support"
            }

        _, left_profile, right_profile = DCSpikeDetectionUtils._build_symmetric_profiles(
            x=x,
            center_idx=center_idx,
            analysis_half_width=analysis_half_width,
            smooth_window=smooth_window
        )

        metrics = DCSpikeDetectionUtils._compute_profile_metrics(
            left_profile=left_profile,
            right_profile=right_profile,
            slope_smooth_window=slope_smooth_window
        )

        thresholds = DCSpikeDetectionUtils._estimate_thresholds(
            slope_left_s=metrics["slope_left_s"],
            slope_right_s=metrics["slope_right_s"],
            curv_left_s=metrics["curv_left_s"],
            curv_right_s=metrics["curv_right_s"],
            slope_zero_factor=slope_zero_factor,
            slope_asym_abs_factor=slope_asym_abs_factor,
            curvature_factor=curvature_factor
        )

        detected_half_width = int(analysis_half_width)
        stop_reason = "No se detectó claramente el final del DC en el rango analizado."
        termination_mode = "max_range_reached"

        zero_count = 0
        asym_count = 0
        abrupt_count = 0

        eval_len = min(
            len(metrics["slope_left_s"]),
            len(metrics["slope_right_s"]),
            len(metrics["curv_left_s"]) + 1,
            len(metrics["curv_right_s"]) + 1
        )

        per_k = []

        for k in range(min_half_width, eval_len):
            local = DCSpikeDetectionUtils._evaluate_k(
                k=k,
                slope_left_s=metrics["slope_left_s"],
                slope_right_s=metrics["slope_right_s"],
                curv_left_s=metrics["curv_left_s"],
                curv_right_s=metrics["curv_right_s"],
                slope_zero_thresh=thresholds["slope_zero_thresh"],
                slope_asym_abs_thresh=thresholds["slope_asym_abs_thresh"],
                slope_asym_rel=slope_asym_rel,
                curvature_thresh=thresholds["curvature_thresh"]
            )

            if local["near_zero_L"] and local["near_zero_R"]:
                zero_count += 1
            else:
                zero_count = 0

            if local["slope_asym"] or local["one_side_breaks"]:
                asym_count += 1
            else:
                asym_count = 0

            if local["abrupt_change"] and (
                local["slope_asym"]
                or local["near_zero_L"]
                or local["near_zero_R"]
                or local["one_side_breaks"]
            ):
                abrupt_count += 1
            else:
                abrupt_count = 0

            per_k.append({
                "k": int(k),
                "slope_left": local["slope_left"],
                "slope_right": local["slope_right"],
                "near_zero_L": local["near_zero_L"],
                "near_zero_R": local["near_zero_R"],
                "dc_like_L": local["dc_like_L"],
                "dc_like_R": local["dc_like_R"],
                "slope_diff": local["slope_diff"],
                "slope_asym": local["slope_asym"],
                "curv_left": local["curv_left"],
                "curv_right": local["curv_right"],
                "abrupt_change": local["abrupt_change"],
                "zero_count": int(zero_count),
                "asym_count": int(asym_count),
                "abrupt_count": int(abrupt_count),
            })

            if zero_count >= consecutive_confirm:
                detected_half_width = k - consecutive_confirm + 1
                stop_reason = (
                    "La pendiente llegó de forma sostenida cerca de cero en ambos lados. "
                    "Se interpreta como llegada al piso de ruido/base del DC."
                )
                termination_mode = "noise_floor"
                break

            if asym_count >= consecutive_confirm:
                detected_half_width = k - consecutive_confirm + 1
                stop_reason = (
                    "Se perdió de forma sostenida la coherencia simétrica de pendientes "
                    "entre izquierda y derecha. Se interpreta como fin de la falda DC "
                    "o superposición con otra estructura espectral."
                )
                termination_mode = "emission_or_asymmetry"
                break

            if abrupt_count >= consecutive_confirm:
                detected_half_width = k - consecutive_confirm + 1
                stop_reason = (
                    "Se detectó un cambio abrupto sostenido de pendiente/curvatura "
                    "en la base del perfil. Se interpreta como final del DC spike."
                )
                termination_mode = "abrupt_transition"
                break

        detected_half_width = max(int(detected_half_width), int(min_half_width))

        debug_info = {
            "reason": stop_reason,
            "center_idx": int(center_idx),
            "analysis_half_width": int(analysis_half_width),
            "detected_half_width": int(detected_half_width),
            "slope_zero_thresh": thresholds["slope_zero_thresh"],
            "slope_asym_abs_thresh": thresholds["slope_asym_abs_thresh"],
            "curvature_thresh": thresholds["curvature_thresh"],
            "slope_scale": thresholds["slope_scale"],
            "curv_scale": thresholds["curv_scale"],
            "left_profile": left_profile,
            "right_profile": right_profile,
            "slope_left_s": metrics["slope_left_s"],
            "slope_right_s": metrics["slope_right_s"],
            "curv_left_s": metrics["curv_left_s"],
            "curv_right_s": metrics["curv_right_s"],
            "per_k": per_k,
            "termination_mode": termination_mode
        }

        if debug:
            print("\n[DEBUG DETECCIÓN DC]")
            print("  Centro:", center_idx)
            print("  Rango analizado por lado:", analysis_half_width)
            print("  Semi-ancho detectado:", detected_half_width)
            print("  Umbral pendiente ~ 0:", thresholds["slope_zero_thresh"])
            print("  Umbral asimetría abs:", thresholds["slope_asym_abs_thresh"])
            print("  Umbral curvatura:", thresholds["curvature_thresh"])
            print("  Modo de terminación:", termination_mode)
            print("  Motivo:", stop_reason)

        return detected_half_width, debug_info
