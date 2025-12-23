#ifndef AM_RADIO_H
#define AM_RADIO_H

#include "datatypes.h"
#include <stdint.h>

typedef struct {
    double audio_acc;
    int samples_in_acc;
    int decim_factor;

    float gain;

    // --- DC blocker (high-pass) ---
    float dc_r;
    float dc_x1;
    float dc_y1;

    // --- Biquad LPF (RBJ cookbook) ---
    float b0, b1, b2, a1, a2;
    float z1, z2; // Direct Form II transposed state

    int enable_dc_block;
    int enable_lpf;
} am_radio_t;

/**
 * @brief Setup the AM radio state (envelope detector + decim + DC block + LPF).
 * @param fs        Input IQ rate (e.g., 2e6)
 * @param audio_fs  Output rate (e.g., 48000)
 */
void am_radio_init(am_radio_t *r, double fs, int audio_fs);

/**
 * @brief Processes an IQ block and fills a PCM16 buffer.
 *        Updates AM depth metrics if depth_st != NULL.
 * @return Number of audio samples generated.
 */
int am_radio_iq_to_pcm(am_radio_t *r, signal_iq_t *sig, int16_t *pcm_out, am_depth_state_t *depth_st);

#endif
