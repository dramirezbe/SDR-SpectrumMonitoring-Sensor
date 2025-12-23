#include "am_radio_local.h"

#include <string.h>
#include <math.h>
#include <complex.h>

// =========================================================
// AM audio demod (local, minimal)
//
// Envelope -> decimate to 48k -> DC blocker -> audio LPF -> gain/clip
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

void am_radio_local_init(am_radio_local_t *r, double fs_iq, int audio_fs) {
    memset(r, 0, sizeof(*r));
    r->gain = 20000.0f;

    r->decim_factor = (int)llround(fs_iq / (double)audio_fs);
    if (r->decim_factor < 1) r->decim_factor = 1;

    r->enable_dc_block = 1;
    r->enable_lpf = 1;

    // DC blocker (~30 Hz @ 48k)
    r->dc_r  = 0.996f;
    r->dc_x1 = 0.0f;
    r->dc_y1 = 0.0f;

    // AM audio LPF: conservative voice-ish (5 kHz).
    am_biquad_lowpass(r, (float)audio_fs, 5000.0f, 0.707f);
}

// ====== AM DEPTH METRIC (added only for metrics) ======
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
            m = (st->env_max - st->env_min) / denom; // m in [0..1] ideal
            if (m < 0.0f) m = 0.0f;
            if (m > 1.0f) m = 1.0f;
        }

        st->depth_ema = (1.0f - DEPTH_EMA_ALPHA) * st->depth_ema
                      + (DEPTH_EMA_ALPHA) * m;

        /* reset ventana */
        st->env_min = 1e9f;
        st->env_max = 0.0f;
        st->counter = 0;
    }

    return st->depth_ema;
}

int am_radio_local_iq_to_pcm(am_radio_local_t *r, signal_iq_t *sig, int16_t *pcm_out, am_depth_state_t *depth_st) {
    int out_idx = 0;

    for (size_t i = 0; i < sig->n_signal; i++) {
        // envelope
        double re = creal(sig->signal_iq[i]);
        double im = cimag(sig->signal_iq[i]);
        double env = sqrt(re*re + im*im);

        r->env_acc += env;
        r->env_count++;

        if (r->env_count >= r->decim_factor) {
            float val = (float)(r->env_acc / (double)r->env_count);
            r->env_acc = 0.0;
            r->env_count = 0;

            // update AM depth metric (use decimated envelope BEFORE DC block)
            if (depth_st) {
                update_am_depth_from_env_ctx(depth_st, val);
            }

            // remove DC / carrier component
            if (r->enable_dc_block) {
                val = am_dc_block_process(r, val);
            }

            // audio low-pass
            if (r->enable_lpf) {
                val = am_biquad_process(r, val);
            }

            double pcm = (double)val * (double)r->gain;
            if (pcm >  32767.0) pcm =  32767.0;
            if (pcm < -32768.0) pcm = -32768.0;

            pcm_out[out_idx++] = (int16_t)pcm;
        }
    }
    return out_idx;
}
