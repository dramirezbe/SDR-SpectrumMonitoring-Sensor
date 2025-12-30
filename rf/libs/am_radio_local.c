#include "am_radio_local.h"

#include <string.h>
#include <math.h>
#include <complex.h>
#include <float.h>

// =========================================================
// AM audio demod (robust)
//
// - Envelope
// - CIC decimator (order 2) to 48k
// - Envelope mean normalization: (env - mean)/mean
// - Optional DC blocker
// - Audio LPF (biquad)
// - Simple RMS AGC (attack/release)
// - Gain/clip
// =========================================================

static void am_biquad_lowpass(am_radio_local_t *r, float fs, float fc, float Q) {
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

static inline float am_biquad_process(am_radio_local_t *r, float x) {
    float y = r->b0 * x + r->z1;
    r->z1 = r->b1 * x - r->a1 * y + r->z2;
    r->z2 = r->b2 * x - r->a2 * y;
    return y;
}

static inline float am_dc_block_process(am_radio_local_t *r, float x) {
    float y = x - r->dc_x1 + r->dc_r * r->dc_y1;
    r->dc_x1 = x;
    r->dc_y1 = y;
    return y;
}

// ====== AM DEPTH METRIC (kept) ======
#define DEPTH_EMA_ALPHA 0.15f

static inline float update_am_depth_from_env_ctx(am_depth_state_t *st, float env_decimated)
{
    if (!st) return 0.0f;
    if (!isfinite(env_decimated)) return st->depth_ema;

    if (env_decimated < st->env_min) st->env_min = env_decimated;
    if (env_decimated > st->env_max) st->env_max = env_decimated;

    st->counter++;
    if (st->counter >= st->report_samples) {
        float denom = (st->env_max + st->env_min);
        float m = 0.0f;

        if (denom > 1e-9f) {
            m = (st->env_max - st->env_min) / denom; // [0..1] ideal
            if (m < 0.0f) m = 0.0f;
            if (m > 1.0f) m = 1.0f;
        }

        st->depth_ema = (1.0f - DEPTH_EMA_ALPHA) * st->depth_ema
                      + (DEPTH_EMA_ALPHA) * m;

        st->env_min = 1e9f;
        st->env_max = 0.0f;
        st->counter = 0;
    }

    return st->depth_ema;
}

// ---- Robust helpers ----

// Fast-ish magnitude (still sqrt). You may replace with hypot() if desired.
static inline double am_env_mag(double re, double im) {
    // sqrt(re^2+im^2) but with some numerical safety
    double a = re*re + im*im;
    if (!(a > 0.0)) return 0.0;
    return sqrt(a);
}

// CIC order-2 decimator at integer factor R.
// Gain = R^2, so we divide by (R*R) for roughly unity gain.
static inline float am_cic2_decim_push(am_radio_local_t *r, double x, int R, int *ready)
{
    // 2 integrators
    r->cic_i1 += x;
    r->cic_i2 += r->cic_i1;

    r->env_count++;
    if (r->env_count < (size_t)R) {
        *ready = 0;
        return 0.0f;
    }

    r->env_count = 0;
    *ready = 1;

    // 2 combs (at decimated rate)
    double c1 = r->cic_i2 - r->cic_c1_z;
    r->cic_c1_z = r->cic_i2;

    double c2 = c1 - r->cic_c2_z;
    r->cic_c2_z = c1;

    // normalize CIC gain
    double y = c2 / ((double)R * (double)R);
    if (!isfinite(y)) y = 0.0;
    return (float)y;
}

// Envelope mean tracker: slow EMA to estimate carrier level.
// alpha ~ 1/(tau*fs_audio). For tau ~ 0.5..2 s, alpha small.
static inline float am_update_env_mean(am_radio_local_t *r, float env_dec)
{
    float m = r->env_mean;
    if (!isfinite(m) || m < 0.0f) m = 0.0f;
    if (!isfinite(env_dec) || env_dec < 0.0f) env_dec = 0.0f;

    m += r->env_mean_alpha * (env_dec - m);
    r->env_mean = m;
    return m;
}

// Simple RMS AGC with attack/release on gain.
// - attack: fast when we need to REDUCE gain (signal got larger)
// - release: slow when we need to INCREASE gain (signal got smaller)
static inline float am_agc_process(am_radio_local_t *r, float x)
{
    // Update RMS^2 EMA
    float x2 = x * x;
    if (!isfinite(x2)) x2 = 0.0f;
    // Choose a moderate smoothing; reuse existing fields
    // (r->agc_rms2 is the EMA state; coefficients in init)
    r->agc_rms2 = 0.9990f * r->agc_rms2 + 0.0010f * x2;

    float rms = sqrtf(r->agc_rms2 + 1e-12f);
    float desired = r->agc_target_rms / (rms + 1e-12f);

    // Clamp desired gain
    if (desired > r->agc_max_gain) desired = r->agc_max_gain;
    if (desired < r->agc_min_gain) desired = r->agc_min_gain;

    // Smooth gain
    float g = r->agc_gain;
    if (!isfinite(g) || g <= 0.0f) g = 1.0f;

    // If desired < current => need to reduce gain quickly (attack)
    float coeff = (desired < g) ? r->agc_attack : r->agc_release;
    g += coeff * (desired - g);

    // Safety clamp
    if (g > r->agc_max_gain) g = r->agc_max_gain;
    if (g < r->agc_min_gain) g = r->agc_min_gain;

    r->agc_gain = g;
    return x * g;
}

void am_radio_local_init(am_radio_local_t *r, double fs_iq, int audio_fs) {
    memset(r, 0, sizeof(*r));

    // Keep your original fixed gain as "final scalar" (post-AGC still applies)
    r->gain = 20000.0f;

    r->decim_factor = (int)llround(fs_iq / (double)audio_fs);
    if (r->decim_factor < 1) r->decim_factor = 1;

    r->enable_dc_block = 1;
    r->enable_lpf = 1;

    // DC blocker (~30 Hz @ 48k)
    r->dc_r  = 0.996f;
    r->dc_x1 = 0.0f;
    r->dc_y1 = 0.0f;

    // Audio LPF: en aeronáutica AM suele ser suficiente 3–4 kHz,
    // pero conservamos tu 5 kHz para no romper el caso del generador.
    am_biquad_lowpass(r, (float)audio_fs, 5000.0f, 0.707f);

    // ---- New robust state init ----
    r->cic_i1 = r->cic_i2 = 0.0;
    r->cic_c1_z = r->cic_c2_z = 0.0;

    // Envelope mean tracker:
    // tau ~ 1.0 s => alpha ~ 1/(fs_audio*tau)
    // fs_audio=48k => ~2.08e-5. Usamos un poco más rápido para seguir fading.
    r->env_mean = 0.0f;
    r->env_mean_alpha = 5.0e-5f; // ~0.4 s de constante de tiempo aprox.

    // AGC:
    r->agc_gain = 1.0f;
    r->agc_rms2 = 1e-6f;

    // Target RMS en el dominio "val" antes de r->gain.
    // (Ajustado para que no rompa el caso tono AM; el clipping final protege.)
    r->agc_target_rms = 0.08f;

    r->agc_max_gain = 25.0f;
    r->agc_min_gain = 0.2f;

    // Attack/release como coeficientes de suavizado por muestra audio:
    // attack más grande => reacciona más rápido al bajar ganancia
    // release más pequeño => sube ganancia lentamente
    r->agc_attack  = 0.10f;  // rápido
    r->agc_release = 0.005f; // lento
}

int am_radio_local_iq_to_pcm(am_radio_local_t *r, signal_iq_t *sig, int16_t *pcm_out, am_depth_state_t *depth_st) {
    int out_idx = 0;
    const int R = r->decim_factor;

    for (size_t i = 0; i < sig->n_signal; i++) {

        // Envelope
        double re = creal(sig->signal_iq[i]);
        double im = cimag(sig->signal_iq[i]);
        double env = am_env_mag(re, im);

        // CIC2 decimation to audio_fs
        int ready = 0;
        float env_dec = am_cic2_decim_push(r, env, R, &ready);
        if (!ready) continue;

        // AM depth metric uses RAW decimated envelope (unchanged behavior)
        if (depth_st) {
            update_am_depth_from_env_ctx(depth_st, env_dec);
        }

        // ---- Key improvement: normalize envelope by its slow mean ----
        float mean = am_update_env_mean(r, env_dec);

        // Avoid division blow-up if mean is tiny (no carrier)
        const float MEAN_FLOOR = 1e-6f;
        float denom = (mean > MEAN_FLOOR) ? mean : MEAN_FLOOR;

        // Relative envelope: (env - mean)/mean  ~= modulation (small m becomes audible)
        float val = (env_dec - mean) / denom;

        // Optional DC blocker still useful against very slow mean drift / AGC interactions
        if (r->enable_dc_block) {
            val = am_dc_block_process(r, val);
        }

        // Audio LPF
        if (r->enable_lpf) {
            val = am_biquad_process(r, val);
        }

        // Simple RMS AGC
        val = am_agc_process(r, val);

        // Final fixed gain + clip
        double pcm = (double)val * (double)r->gain;
        if (pcm >  32767.0) pcm =  32767.0;
        if (pcm < -32768.0) pcm = -32768.0;

        pcm_out[out_idx++] = (int16_t)pcm;
    }

    return out_idx;
}
