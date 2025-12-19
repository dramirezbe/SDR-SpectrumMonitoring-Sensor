#ifndef FM_RADIO_H
#define FM_RADIO_H

#include "datatypes.h"
#include <stdint.h>
#include <complex.h>

typedef struct {
    // Demodulator state
    double complex prev_sample;
    // Decimation state
    double audio_acc;
    int samples_in_acc;
    int decim_factor;
    // De-emphasis state
    float deemph_acc;
    float deemph_alpha;
    // Output gain
    double gain;
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
int fm_radio_iq_to_pcm(fm_radio_t *radio, signal_iq_t *sig, int16_t *pcm_out);

#endif