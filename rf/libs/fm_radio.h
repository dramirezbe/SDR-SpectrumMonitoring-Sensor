#ifndef FM_RADIO_H
#define FM_RADIO_H

#include "datatypes.h"
#include <stdint.h>
#include <complex.h>

typedef struct {
    double complex prev_sample;

    double audio_acc;
    int samples_in_acc;
    int decim_factor;

    float deemph_acc;
    float deemph_alpha;

    float gain;

    // --- DC blocker (high-pass) ---
    float dc_r;
    float dc_x1;
    float dc_y1;

    // --- Biquad LPF (RBJ cookbook) ---
    float b0, b1, b2, a1, a2;
    float z1, z2; // Direct Form II transposed state

    // enable flags
    int enable_dc_block;
    int enable_lpf;
} fm_radio_t;


/**
 * @brief Setup the radio state.
 * @param fs Input rate (e.g., 2e6), @param audio_fs Output rate (e.g., 48000), @param deemph_us (75)
 */
void fm_radio_init(fm_radio_t *radio, double fs, int audio_fs, int deemph_us);

/**
 * @brief Processes an IQ block and fills a PCM16 buffer. 
 * @return Number of audio samples generated.
 */
int fm_radio_iq_to_pcm(fm_radio_t *radio, signal_iq_t *sig, int16_t *pcm_out, fm_dev_state_t *dev_st, int fs_demod);

#endif