#ifndef AM_RADIO_LOCAL_H
#define AM_RADIO_LOCAL_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

// Your existing project types:
#include "datatypes.h"   // expected to define: signal_iq_t, am_depth_state_t, etc.

// Local AM demod state (as-is)
typedef struct {
    double env_acc;
    int env_count;
    int decim_factor;

    float gain;

    // DC blocker (audio)
    float dc_r;
    float dc_x1;
    float dc_y1;

    // Biquad LPF (RBJ cookbook)
    float b0, b1, b2, a1, a2;
    float z1, z2;
    int enable_dc_block;
    int enable_lpf;
} am_radio_local_t;

void am_radio_local_init(am_radio_local_t *r, double fs_iq, int audio_fs);

// Returns number of int16 samples written to pcm_out (as-is)
int am_radio_local_iq_to_pcm(am_radio_local_t *r,
                            signal_iq_t *sig,
                            int16_t *pcm_out,
                            am_depth_state_t *depth_st);

#ifdef __cplusplus
}
#endif

#endif // AM_RADIO_LOCAL_H
