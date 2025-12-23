#ifndef AUDIO_STREAM_CTX_H
#define AUDIO_STREAM_CTX_H

#include <stdatomic.h>
#include <stdint.h>

// Tipos base del proyecto
#include "datatypes.h"       // filter_t, fm_dev_state_t, am_depth_state_t, signal_iq_t, etc.
#include "fm_radio.h"        // fm_radio_t

// ESTE ES EL FIX CLAVE: define iq_iir_filter_t
#include "iq_iir_filter.h"   // iq_iir_filter_t

// AM local
#include "am_radio_local.h"  // am_radio_local_t

// ========================= Audio & PSD constants
#define AUDIO_CHUNK_SAMPLES 16384
#define PSD_SAMPLES_TOTAL   2097152
#define AUDIO_FS            48000   // IMPORTANT: must be 48k to match Opus best-practice

// ========================= Opus streaming defaults (to Python gateway)
#define AUDIO_TCP_DEFAULT_HOST "127.0.0.1"
#define AUDIO_TCP_DEFAULT_PORT 9000

#define OPUS_FRAME_MS_DEFAULT  20
#define OPUS_BITRATE_DEFAULT   32000
#define OPUS_COMPLEXITY_DEFAULT 5
#define OPUS_VBR_DEFAULT       0    // 0 = CBR, 1 = VBR

// - WBFM broadcast channel ~200kHz (±100kHz). If you use 75k, audio usually gets worse.
static float  IQ_FILTER_BW_FM_HZ      = 200000.0f;
// Butterworth order (will be forced to even internally). Typical: 4,6,8
static int    IQ_FILTER_ORDER         = 8;


#ifdef __cplusplus
extern "C" {
#endif

typedef struct audio_stream_ctx {
    fm_radio_t *fm_radio;
    am_radio_local_t *am_radio;

    const char *tcp_host;
    int tcp_port;

    int opus_sample_rate;
    int opus_channels;
    int bitrate;
    int complexity;
    int vbr;
    int frame_ms;

    _Atomic int    current_mode;
    _Atomic double current_fs_hz;

    iq_iir_filter_t iqf;
    filter_audio_t        iqf_cfg;
    int             iqf_ready;

    fm_dev_state_t  fm_dev;
    am_depth_state_t am_depth;
} audio_stream_ctx_t;

void audio_stream_ctx_defaults(audio_stream_ctx_t *ctx, fm_radio_t *fm, am_radio_local_t *am);

#ifdef __cplusplus
}
#endif

#endif // AUDIO_STREAM_CTX_H
