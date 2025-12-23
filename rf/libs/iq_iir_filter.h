// libs/iq_iir_filter.h
#ifndef IQ_IIR_FILTER_H
#define IQ_IIR_FILTER_H

#include "datatypes.h"

#ifdef __cplusplus
extern "C" {
#endif

// Estado interno del filtro IIR (separado de filter_t para no tocar datatypes.h)
typedef struct {
    int initialized;

    double fs_hz;     // sample rate actual
    float  bw_hz;     // ancho de banda "dos lados" (pasabanda aprox +/- bw/2)
    int    order;     // orden Butterworth (par): 2,4,6,8...

    int sections;     // order/2

    // Coeficientes por sección (RBJ biquad)
    float *b0, *b1, *b2, *a1, *a2;

    // Estados por sección (I y Q separados)
    float *z1_i, *z2_i;
    float *z1_q, *z2_q;

    // DC blocker IQ
    int   enable_dc;
    float dc_r;
    float dc_x1_i, dc_y1_i;
    float dc_x1_q, dc_y1_q;
} iq_iir_filter_t;

// Init / reconfig / reset / free
int  iq_iir_filter_init(iq_iir_filter_t *st, double fs_hz, const filter_audio_t *cfg, int enable_dc_block);
int  iq_iir_filter_config(iq_iir_filter_t *st, double fs_hz, const filter_audio_t *cfg);
void iq_iir_filter_reset(iq_iir_filter_t *st);
void iq_iir_filter_free(iq_iir_filter_t *st);

// Apply in-place to signal_iq_t
void iq_iir_filter_apply_inplace(iq_iir_filter_t *st, signal_iq_t *sig);

#ifdef __cplusplus
}
#endif

#endif
