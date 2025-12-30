#ifndef AM_RADIO_LOCAL_H
#define AM_RADIO_LOCAL_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

// Your existing project types:
#include "datatypes.h"   // expected to define: signal_iq_t, am_depth_state_t, etc.

// Local AM demod state (extended, backwards-source-compatible)
typedef struct {
    // -------------------------
    // Existing fields (KEEP)
    // -------------------------
    double env_acc;      // (left as-is; may be unused internally after changes)
    int    env_count;    // reused as CIC decimation counter
    int    decim_factor;

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

    // -------------------------
    // New fields (ADDED)
    // -------------------------

    // CIC decimator order-2 state (very low CPU)
    double cic_i1, cic_i2;       // integrators
    double cic_c1_z, cic_c2_z;   // comb delays

    // Envelope mean tracker for normalization: (env - mean)/mean
    float  env_mean;
    float  env_mean_alpha;

    // Simple RMS AGC (attack/release on gain)
    float  agc_gain;        // adaptive multiplier
    float  agc_rms2;        // EMA of x^2 (RMS^2)
    float  agc_target_rms;  // desired RMS before final r->gain
    float  agc_max_gain;    // clamp
    float  agc_min_gain;    // clamp
    float  agc_attack;      // fast when reducing gain
    float  agc_release;     // slow when increasing gain

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
