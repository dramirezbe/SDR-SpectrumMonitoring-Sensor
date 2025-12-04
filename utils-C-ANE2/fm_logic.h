/**
 * @file Modules/fm_logic.h
 */
#ifndef FM_LOGIC_H
#define FM_LOGIC_H

#include <stdint.h>
#include <stddef.h>
#include <portaudio.h>

// Holds the "Memory" of the DSP (Phase history, filters)
typedef struct {
    float last_phase;
    float sum_audio;
    int dec_counter;
    int decimation_factor;
    PaStream *stream; // PortAudio stream handle
} FMDemodContext;

// Initialize the context and audio
int fm_context_init(FMDemodContext *ctx, int sample_rate_rf, int sample_rate_audio);
void fm_context_cleanup(FMDemodContext *ctx);

// The Callback Logic
void fm_demod_logic(const uint8_t *data, size_t len, void *ctx_void);

#endif