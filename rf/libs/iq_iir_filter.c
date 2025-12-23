// libs/iq_iir_filter.c
#include "iq_iir_filter.h"
#include <stdlib.h>
#include <string.h>
#include <math.h>

#ifndef M_PI
#define M_PI 3.14159265358979323846
#endif

static int clamp_int(int v, int lo, int hi) {
    if (v < lo) return lo;
    if (v > hi) return hi;
    return v;
}

static double clamp_double(double v, double lo, double hi) {
    if (v < lo) return lo;
    if (v > hi) return hi;
    return v;
}

static inline float dc_block_1p(float x, float *x1, float *y1, float r) {
    // y[n] = x[n] - x[n-1] + r*y[n-1]
    float y = x - (*x1) + r * (*y1);
    *x1 = x;
    *y1 = y;
    return y;
}

// RBJ lowpass biquad coefficients for given fs, fc, Q
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

// Butterworth Qs for even order N:
// poles: phi_k = (2k+1)π/(2N), Q_k = 1/(2 sin(phi_k)), k=0..N/2-1
static float butterworth_Q(int N, int k) {
    double phi = M_PI * (2.0 * (double)k + 1.0) / (2.0 * (double)N);
    double s   = sin(phi);
    if (s < 1e-9) s = 1e-9;
    double Q = 1.0 / (2.0 * s);
    return (float)Q;
}

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
