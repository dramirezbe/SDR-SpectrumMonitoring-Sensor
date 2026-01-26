/**
 * @file am_radio.c
 * @brief Implementación de la demodulación AM y filtrado de audio.
 */

#include "am_radio.h"

/**
 * @addtogroup am_module
 * @{
 */

 /**
 * @brief Reinicia las métricas de envolvente para un nuevo cálculo de ventana.
 * @param[in,out] st Puntero al estado de métricas de profundidad AM.
 */
static inline void am_depth_reset(am_depth_state_t *st) {
    if (!st) return;
    st->env_min = 1e9f;
    st->env_max = 0.0f;
    st->counter = 0;
    // st->report_samples stays as configured
    // st->depth_ema stays (EMA memory)
}

/**
 * @brief Actualiza la métrica de profundidad AM utilizando la envolvente diezmada.
 * * La profundidad de modulación \f$ m \f$ se calcula como:
 * \f[ m = \frac{A_{max} - A_{min}}{A_{max} + A_{min}} \f]
 * El valor resultante se filtra mediante un promedio móvil exponencial (EMA).
 *
 * @param[in,out] st             Estado de métricas de profundidad AM.
 * @param[in]     env_decimated  Muestra de envolvente después del diezmado.
 * @return float                 Profundidad de modulación suavizada actual (0.0 a 1.0).
 */
static inline float update_am_depth_from_env_ctx(am_depth_state_t *st, float env_decimated) {
    if (!st) return 0.0f;
    if (!isfinite(env_decimated)) return st->depth_ema;

    if (env_decimated < st->env_min) st->env_min = env_decimated;
    if (env_decimated > st->env_max) st->env_max = env_decimated;

    st->counter++;
    if (st->report_samples > 0 && st->counter >= st->report_samples) {
        float denom = (st->env_max + st->env_min);
        float m = 0.0f;

        if (denom > 1e-9f) {
            m = (st->env_max - st->env_min) / denom;
            if (m < 0.0f) m = 0.0f;
            // Option A (your choice): clamp to 100% max
            if (m > 1.0f) m = 1.0f;
        }

        st->depth_ema = (1.0f - DEPTH_EMA_ALPHA) * st->depth_ema
                      + (DEPTH_EMA_ALPHA) * m;

        am_depth_reset(st);
    }

    return st->depth_ema; // m in [0..1]
}

void am_radio_init(am_radio_t *r, double fs, int audio_fs) {
    if (!r) return;
    memset(r, 0, sizeof(*r));

    r->audio_acc = 0.0;
    r->samples_in_acc = 0;

    r->decim_factor = (int)llround(fs / (double)audio_fs);
    if (r->decim_factor < 1) r->decim_factor = 1;

    r->gain = 22000.0f;

    r->enable_dc_block = 1;
    r->enable_lpf = 1;

    // DC blocker ~30 Hz @ 48 kHz
    r->dc_r  = 0.996f;
    r->dc_x1 = 0.0f;
    r->dc_y1 = 0.0f;

    // Conservative voice LPF for AM
    biquad_lowpass(r, (float)audio_fs, AM_AUDIO_LPF_HZ, AM_AUDIO_Q);
}

/**
 * @brief Calcula los coeficientes de un filtro biquad pasa bajos.
 * * Utiliza la arquitectura RBJ (Robert Bristow-Johnson) para un filtro Butterworth.
 *
 * @param[in,out] r   Puntero al estado del radio donde se guardarán los coeficientes.
 * @param[in]     fs  Frecuencia de muestreo (Hz).
 * @param[in]     fc  Frecuencia de corte (Hz).
 * @param[in]     Q   Factor de calidad.
 */
static void biquad_lowpass(am_radio_t *r, float fs, float fc, float Q) {
    if (fc <= 0.0f) fc = 1.0f;
    if (fc > 0.49f * fs) fc = 0.49f * fs;

    const float w0 = 2.0f * (float)M_PI * (fc / fs);
    const float c  = cosf(w0);
    const float s  = sinf(w0);
    const float alpha = s / (2.0f * Q);

    float b0 = (1.0f - c) * 0.5f;
    float b1 = (1.0f - c);
    float b2 = (1.0f - c) * 0.5f;
    float a0 = (1.0f + alpha);
    float a1 = (-2.0f * c);
    float a2 = (1.0f - alpha);

    r->b0 = b0 / a0;
    r->b1 = b1 / a0;
    r->b2 = b2 / a0;
    r->a1 = a1 / a0;
    r->a2 = a2 / a0;

    r->z1 = 0.0f;
    r->z2 = 0.0f;
}

/**
 * @brief Procesa una muestra de audio a través de un filtro biquad.
 * * Implementa la Forma Directa II Transpuesta:
 * \f[ y[n] = b_0 x[n] + z_1[n-1] \f]
 * \f[ z_1[n] = b_1 x[n] - a_1 y[n] + z_2[n-1] \f]
 * \f[ z_2[n] = b_2 x[n] - a_2 y[n] \f]
 *
 * @param[in,out] r Puntero al estado del radio (contiene coeficientes y retardos).
 * @param[in]     x Muestra de audio de entrada.
 * @return float    Muestra de audio filtrada.
 */
static inline float biquad_process(am_radio_t *r, float x) {
    float y = r->b0 * x + r->z1;
    r->z1 = r->b1 * x - r->a1 * y + r->z2;
    r->z2 = r->b2 * x - r->a2 * y;
    return y;
}

/**
 * @brief Aplica un filtro de bloqueo de componente DC.
 * * Sigue la ecuación en diferencias:
 * \f[ y[n] = x[n] - x[n-1] + R \cdot y[n-1] \f]
 * Donde \f$ R \f$ controla la frecuencia de corte cercana a 0 Hz.
 *
 * @param[in,out] r Puntero al estado del radio.
 * @param[in]     x Entrada de audio con offset DC.
 * @return float    Salida de audio sin componente DC.
 */
static inline float dc_block_process(am_radio_t *r, float x) {
    float y = x - r->dc_x1 + r->dc_r * r->dc_y1;
    r->dc_x1 = x;
    r->dc_y1 = y;
    return y;
}

int am_radio_iq_to_pcm(am_radio_t *r, signal_iq_t *sig, int16_t *pcm_out, am_depth_state_t *depth_st) {
    if (!r || !sig || !pcm_out) return 0;

    int out_idx = 0;

    for (size_t i = 0; i < sig->n_signal; i++) {
        // Envelope detector
        float re = (float)creal(sig->signal_iq[i]);
        float im = (float)cimag(sig->signal_iq[i]);
        float env = hypotf(re, im);

        // crude decimation: accumulate then average
        r->audio_acc += (double)env;
        r->samples_in_acc++;

        if (r->samples_in_acc >= r->decim_factor) {
            float env_dec = (float)(r->audio_acc / (double)r->samples_in_acc);
            r->audio_acc = 0.0;
            r->samples_in_acc = 0;

            // --- AM depth metric update (uses envelope BEFORE DC remove) ---
            if (depth_st) {
                update_am_depth_from_env_ctx(depth_st, env_dec);
            }

            float a = env_dec;

            // remove DC (carrier) -> audio
            if (r->enable_dc_block) {
                a = dc_block_process(r, a);
            }

            // audio LPF
            if (r->enable_lpf) {
                a = biquad_process(r, a);
            }

            // gain + clip
            float pcmf = a * r->gain;
            if (pcmf >  32767.0f) pcmf =  32767.0f;
            if (pcmf < -32768.0f) pcmf = -32768.0f;

            pcm_out[out_idx++] = (int16_t)pcmf;
        }
    }

    return out_idx;
}

/** @} */