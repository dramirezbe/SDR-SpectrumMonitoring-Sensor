/**
 * @file iq_iir_filter.c
 * @brief Implementación matemática de filtros IIR Butterworth y Biquads.
 *
 * Contiene los algoritmos de diseño de filtros Robert Bristow-Johnson (RBJ) 
 * y la implementación de la Forma Directa II Transpuesta (DF2T).
 */
#include "iq_iir_filter.h"

/**
 * @addtogroup iq_iir_filter_module
 * @{
 */

/**
 * @brief Restringe un valor entero dentro de un rango determinado.
 * @param[in] v  Valor a evaluar.
 * @param[in] lo Límite inferior.
 * @param[in] hi Límite superior.
 * @return int Valor truncado al rango [lo, hi].
 */
static int clamp_int(int v, int lo, int hi) {
    if (v < lo) return lo;
    if (v > hi) return hi;
    return v;
}

/**
 * @brief Restringe un valor de punto flotante doble dentro de un rango determinado.
 * @param[in] v  Valor a evaluar.
 * @param[in] lo Límite inferior.
 * @param[in] hi Límite superior.
 * @return double Valor truncado al rango [lo, hi].
 */
static double clamp_double(double v, double lo, double hi) {
    if (v < lo) return lo;
    if (v > hi) return hi;
    return v;
}

/**
 * @brief Filtro DC Blocker de un solo polo.
 *
 * La transferencia en el dominio Z es:
 * \f[ H(z) = \frac{1 - z^{-1}}{1 - r \cdot z^{-1}} \f]
 *
 * Donde \f$ r \f$ suele ser \f$ \approx 0.995 \f$. Esto crea un cero en DC y un polo 
 * muy cercano que cancela el efecto en el resto de la banda.
 * @param[in]     x   Muestra de entrada actual.
 * @param[in,out] x1  Puntero al estado de la muestra de entrada anterior.
 * @param[in,out] y1  Puntero al estado de la muestra de salida anterior.
 * @param[in]     r   Factor de radio de polo (determina el ancho de la muesca).
 * @return float Muestra filtrada.
 */
static inline float dc_block_1p(float x, float *x1, float *y1, float r) {
    // y[n] = x[n] - x[n-1] + r*y[n-1]
    float y = x - (*x1) + r * (*y1);
    *x1 = x;
    *y1 = y;
    return y;
}

/**
 * @brief Diseño de filtros Robert Bristow-Johnson (RBJ).
 * Convierte los parámetros de frecuencia y Q en coeficientes de transferencia:
 * \f[ H(z) = \frac{b_0 + b_1 z^{-1} + b_2 z^{-2}}{1 + a_1 z^{-1} + a_2 z^{-2}} \f]
 * * @param[in]  fs Frecuencia de muestreo.
 * @param[in]  fc Frecuencia de corte.
 * @param[in]  Q  Factor de calidad (determina la respuesta en la esquina).
 * @param[out] b0 Numerador 0.
 * @param[out] b1 Numerador 1.
 * @param[out] b2 Numerador 2.
 * @param[out] a1 Denominador 1 (normalizado).
 * @param[out] a2 Denominador 2 (normalizado).
 */
static void rbj_lowpass(float fs, float fc, float Q,
                        float *b0, float *b1, float *b2,
                        float *a1, float *a2)
{
    // clamp
    if (fc < 1.0f) fc = 1.0f;
    if (fc > 0.49f * fs) fc = 0.49f * fs;
    if (Q < 0.05f) Q = 0.05f;

    const float w0 = 2.0f * (float)M_PI * (fc / fs);
    const float c  = cosf(w0);
    const float s  = sinf(w0);
    const float alpha = s / (2.0f * Q);

    float bb0 = (1.0f - c) * 0.5f;
    float bb1 = (1.0f - c);
    float bb2 = (1.0f - c) * 0.5f;
    float aa0 = (1.0f + alpha);
    float aa1 = (-2.0f * c);
    float aa2 = (1.0f - alpha);

    // normalize
    *b0 = bb0 / aa0;
    *b1 = bb1 / aa0;
    *b2 = bb2 / aa0;
    *a1 = aa1 / aa0;
    *a2 = aa2 / aa0;
}

/**
 * @brief Cálculo de factor de calidad para Butterworth.
 * * Para un orden \f$ N \f$, los polos se distribuyen uniformemente en el semiplano 
 * izquierdo del plano S. El valor de \f$ Q \f$ para la sección \f$ k \f$ es:
 * \f[ Q_k = \frac{1}{-2 \cos(\frac{(2k + N + 1)\pi}{2N})} \f]
 * @param[in] N Orden total del filtro.
 * @param[in] k Índice de la sección (0 a N/2 - 1).
 * @return float Valor de Q correspondiente.
 */
static float butterworth_Q(int N, int k) {
    double phi = M_PI * (2.0 * (double)k + 1.0) / (2.0 * (double)N);
    double s   = sin(phi);
    if (s < 1e-9) s = 1e-9;
    double Q = 1.0 / (2.0 * s);
    return (float)Q;
}

/**
 * @brief Gestiona la asignación y liberación de memoria para las secciones del filtro.
 * * @param[in,out] st       Puntero al estado del filtro.
 * @param[in]     sections Cantidad de secciones biquad a alojar.
 * @return int 0 en éxito, -1 si falló el sistema de memoria.
 */
static int alloc_sections(iq_iir_filter_t *st, int sections) {
    // free old if any
    free(st->b0); free(st->b1); free(st->b2); free(st->a1); free(st->a2);
    free(st->z1_i); free(st->z2_i); free(st->z1_q); free(st->z2_q);

    st->b0 = st->b1 = st->b2 = st->a1 = st->a2 = NULL;
    st->z1_i = st->z2_i = st->z1_q = st->z2_q = NULL;

    st->b0   = (float*)calloc((size_t)sections, sizeof(float));
    st->b1   = (float*)calloc((size_t)sections, sizeof(float));
    st->b2   = (float*)calloc((size_t)sections, sizeof(float));
    st->a1   = (float*)calloc((size_t)sections, sizeof(float));
    st->a2   = (float*)calloc((size_t)sections, sizeof(float));

    st->z1_i = (float*)calloc((size_t)sections, sizeof(float));
    st->z2_i = (float*)calloc((size_t)sections, sizeof(float));
    st->z1_q = (float*)calloc((size_t)sections, sizeof(float));
    st->z2_q = (float*)calloc((size_t)sections, sizeof(float));

    if (!st->b0 || !st->b1 || !st->b2 || !st->a1 || !st->a2 ||
        !st->z1_i || !st->z2_i || !st->z1_q || !st->z2_q) {
        return -1;
    }

    return 0;
}

int iq_iir_filter_init(iq_iir_filter_t *st, double fs_hz, const filter_audio_t *cfg, int enable_dc_block) {
    if (!st || !cfg) return -1;
    memset(st, 0, sizeof(*st));
    st->initialized = 1;

    st->enable_dc = enable_dc_block ? 1 : 0;
    st->dc_r = 0.995f; // suave, típico

    return iq_iir_filter_config(st, fs_hz, cfg);
}

int iq_iir_filter_config(iq_iir_filter_t *st, double fs_hz, const filter_audio_t *cfg) {
    if (!st || !st->initialized || !cfg) return -1;

    if (fs_hz <= 0.0) fs_hz = 1.0;
    st->fs_hz = fs_hz;

    // En tu filter_t: bw_filter_hz = BW "dos lados"
    float bw = cfg->bw_filter_hz;
    if (!(bw > 0.0f)) bw = 1.0f;

    // Si te pasan BW demasiado pequeño para FM, eso mata audio
    st->bw_hz = bw;

    // Orden: usarlo como Butterworth par
    int N = cfg->order_fliter;
    N = clamp_int(N, 2, 12);
    if (N % 2) N += 1; // forzar par
    st->order = N;

    int sections = N / 2;
    if (sections != st->sections || st->b0 == NULL) {
        st->sections = sections;
        if (alloc_sections(st, sections) != 0) return -1;
        iq_iir_filter_reset(st);
    }

    // Diseñar secciones Butterworth
    // cutoff fc = bw/2
    float fc = 0.5f * st->bw_hz;
    fc = (float)clamp_double(fc, 1.0, 0.49 * st->fs_hz);

    for (int k = 0; k < sections; ++k) {
        float Q = butterworth_Q(N, k);
        rbj_lowpass((float)st->fs_hz, fc, Q,
                    &st->b0[k], &st->b1[k], &st->b2[k],
                    &st->a1[k], &st->a2[k]);
    }

    return 0;
}

void iq_iir_filter_reset(iq_iir_filter_t *st) {
    if (!st) return;

    if (st->sections > 0) {
        memset(st->z1_i, 0, (size_t)st->sections * sizeof(float));
        memset(st->z2_i, 0, (size_t)st->sections * sizeof(float));
        memset(st->z1_q, 0, (size_t)st->sections * sizeof(float));
        memset(st->z2_q, 0, (size_t)st->sections * sizeof(float));
    }

    st->dc_x1_i = st->dc_y1_i = 0.0f;
    st->dc_x1_q = st->dc_y1_q = 0.0f;
}

void iq_iir_filter_free(iq_iir_filter_t *st) {
    if (!st) return;

    free(st->b0); free(st->b1); free(st->b2); free(st->a1); free(st->a2);
    free(st->z1_i); free(st->z2_i); free(st->z1_q); free(st->z2_q);

    memset(st, 0, sizeof(*st));
}

/**
 * @brief Núcleo de la Forma Directa II Transpuesta (DF2T).
 *
 * A diferencia de la Forma Directa I, la DF2T minimiza los requerimientos de 
 * almacenamiento y es numéricamente superior para implementaciones en punto flotante.
 *
 * Las ecuaciones de estado que gobiernan cada sección son:
 * \f[
 * \begin{aligned}
 * y[n]   &= b_0 x[n] + z_1[n-1] \\
 * z_1[n] &= b_1 x[n] - a_1 y[n] + z_2[n-1] \\
 * z_2[n] &= b_2 x[n] - a_2 y[n]
 * \end{aligned}
 * \f]
 * @param[in]     x  Muestra de entrada.
 * @param[in]     b0 Coeficiente numerador.
 * @param[in]     b1 Coeficiente numerador.
 * @param[in]     b2 Coeficiente numerador.
 * @param[in]     a1 Coeficiente denominador.
 * @param[in]     a2 Coeficiente denominador.
 * @param[in,out] z1 Registro de estado 1.
 * @param[in,out] z2 Registro de estado 2.
 * @return float Muestra filtrada resultante.
 */
static inline float biquad_df2t(float x, float b0, float b1, float b2, float a1, float a2, float *z1, float *z2) {
    float y = b0 * x + *z1;
    *z1 = b1 * x - a1 * y + *z2;
    *z2 = b2 * x - a2 * y;
    return y;
}

void iq_iir_filter_apply_inplace(iq_iir_filter_t *st, signal_iq_t *sig) {
    if (!st || !sig || !sig->signal_iq) return;

    // Solo hacemos LP para baseband (independiente del enum), porque es lo que necesitas para canal
    // Si quieres apagarlo, lo controlas en rf_audio.c (no acá).
    for (size_t n = 0; n < sig->n_signal; ++n) {
        float xi = (float)creal(sig->signal_iq[n]);
        float xq = (float)cimag(sig->signal_iq[n]);

        // DC blocker
        if (st->enable_dc) {
            xi = dc_block_1p(xi, &st->dc_x1_i, &st->dc_y1_i, st->dc_r);
            xq = dc_block_1p(xq, &st->dc_x1_q, &st->dc_y1_q, st->dc_r);
        }

        // Cascada biquads
        for (int s = 0; s < st->sections; ++s) {
            xi = biquad_df2t(xi, st->b0[s], st->b1[s], st->b2[s], st->a1[s], st->a2[s], &st->z1_i[s], &st->z2_i[s]);
            xq = biquad_df2t(xq, st->b0[s], st->b1[s], st->b2[s], st->a1[s], st->a2[s], &st->z1_q[s], &st->z2_q[s]);
        }

        sig->signal_iq[n] = (double)xi + (double)xq * I;
    }
}

/** @} */