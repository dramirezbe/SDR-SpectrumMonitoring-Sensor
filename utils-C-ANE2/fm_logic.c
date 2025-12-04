/**
 * @file Modules/fm_logic.h
 */
#include "fm_logic.h"
#include <math.h>
#include <stdlib.h>
#include <stdio.h>

#ifndef M_PI
#define M_PI 3.14159265358979323846
#endif

// PortAudio dummy callback (we push data, but PA pulls. 
// Ideally we'd use a PA RingBuffer, but for this example we write directly or block)
// NOTE: Real implementations need a buffer between this logic and PA.
// For simplicity, this callback is unused if we use blocking write, 
// OR we just use it to silence output if needed.
static int pa_noop_callback(const void *input, void *output,
                            unsigned long frameCount,
                            const PaStreamCallbackTimeInfo* timeInfo,
                            PaStreamCallbackFlags statusFlags,
                            void *userData) {
    // In a real app, you'd read from a buffer filled by fm_demod_logic
    // For this example, we will assume Blocking Write in the logic thread (easier to code)
    return paContinue;
}

int fm_context_init(FMDemodContext *ctx, int sample_rate_rf, int sample_rate_audio) {
    ctx->last_phase = 0.0f;
    ctx->sum_audio = 0.0f;
    ctx->dec_counter = 0;
    ctx->decimation_factor = sample_rate_rf / sample_rate_audio;
    
    Pa_Initialize();
    
    // We use Blocking Stream for simplicity in the Consumer Thread
    Pa_OpenDefaultStream(&ctx->stream,
                         0, 1, paFloat32,
                         sample_rate_audio,
                         paFramesPerBufferUnspecified, 
                         NULL, NULL); // NULL callback = Blocking Mode
                         
    Pa_StartStream(ctx->stream);
    return 0;
}

void fm_context_cleanup(FMDemodContext *ctx) {
    Pa_StopStream(ctx->stream);
    Pa_CloseStream(ctx->stream);
    Pa_Terminate();
}

// -------------------------------------------------------------
// THIS IS THE CORE LOGIC YOU REQUESTED
// -------------------------------------------------------------
void fm_demod_logic(const uint8_t *data, size_t len, void *ctx_void) {
    FMDemodContext *ctx = (FMDemodContext*)ctx_void;
    
    // Convert bytes to samples (IQ pairs)
    int sample_count = len / 2;
    
    // Prepare a small buffer for the audio output of this chunk
    // Max audio samples = input samples / decimation
    float audio_buffer[4096]; 
    int audio_idx = 0;

    for (int j = 0; j < sample_count; j++) {
        // 1. Convert IQ
        float i = (float)((int8_t)data[2*j]) / 128.0f;
        float q = (float)((int8_t)data[2*j+1]) / 128.0f;

        // 2. Demodulate (Atan + Diff)
        float current_phase = atan2f(q, i);
        float phase_diff = current_phase - ctx->last_phase;
        
        // Wrap
        if (phase_diff > M_PI)  phase_diff -= 2.0f * M_PI;
        if (phase_diff < -M_PI) phase_diff += 2.0f * M_PI;
        
        ctx->last_phase = current_phase;
        
        // 3. Decimate
        ctx->sum_audio += phase_diff;
        ctx->dec_counter++;

        if (ctx->dec_counter == ctx->decimation_factor) {
            float audio_out = ctx->sum_audio / (float)ctx->decimation_factor;
            
            // Gain / Volume
            audio_out *= 0.5f; 

            // Add to batch buffer
            if (audio_idx < 4096) {
                audio_buffer[audio_idx++] = audio_out;
            }
            
            ctx->sum_audio = 0.0f;
            ctx->dec_counter = 0;
        }
    }

    // 4. Send batch to Audio Card (Blocking write)
    if (audio_idx > 0) {
        Pa_WriteStream(ctx->stream, audio_buffer, audio_idx);
    }
}