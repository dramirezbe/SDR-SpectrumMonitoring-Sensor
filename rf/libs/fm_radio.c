#include "fm_radio.h"
#include <math.h>

void fm_radio_init(fm_radio_t *radio, double fs, int audio_fs, int deemph_us) {
    radio->prev_sample = 1.0 + 0.0*I;
    radio->audio_acc = 0;
    radio->samples_in_acc = 0;
    radio->deemph_acc = 0;
    radio->gain = 60000.0;
    
    radio->decim_factor = (int)llround(fs / (double)audio_fs);
    if (radio->decim_factor < 1) radio->decim_factor = 1;

    float tau = (float)deemph_us * 1e-6f;
    float dt = 1.0f / (float)audio_fs;
    radio->deemph_alpha = dt / (tau + dt);
}

int fm_radio_iq_to_pcm(fm_radio_t *radio, signal_iq_t *sig, int16_t *pcm_out) {
    int out_idx = 0;
    for (size_t i = 0; i < sig->n_signal; i++) {
        // 1. Demodulate (Frequency = Change in Phase)
        double complex diff = sig->signal_iq[i] * conj(radio->prev_sample);
        double angle = atan2(cimag(diff), creal(diff));
        radio->prev_sample = sig->signal_iq[i];

        // 2. Decimate (Average samples down to audio rate)
        radio->audio_acc += angle;
        radio->samples_in_acc++;

        if (radio->samples_in_acc >= radio->decim_factor) {
            float val = (float)(radio->audio_acc / radio->samples_in_acc);
            radio->audio_acc = 0;
            radio->samples_in_acc = 0;

            // 3. De-emphasis (LPF)
            radio->deemph_acc += radio->deemph_alpha * (val - radio->deemph_acc);

            // 4. Gain and Clip
            double pcm = (double)radio->deemph_acc * radio->gain;
            if (pcm > 32767) pcm = 32767;
            if (pcm < -32768) pcm = -32768;

            pcm_out[out_idx++] = (int16_t)pcm;
        }
    }
    return out_idx;
}