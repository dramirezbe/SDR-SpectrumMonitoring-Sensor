#include "audio_stream_ctx.h"

#include <string.h>
#include <stdlib.h>


void audio_stream_ctx_defaults(audio_stream_ctx_t *ctx, fm_radio_t *fm, am_radio_local_t *am) {
    memset(ctx, 0, sizeof(*ctx));
    ctx->fm_radio = fm;
    ctx->am_radio = am;

    // allow overrides via env for convenience
    const char *env_host = getenv("AUDIO_TCP_HOST");
    const char *env_port = getenv("AUDIO_TCP_PORT");
    const char *env_br   = getenv("OPUS_BITRATE");
    const char *env_cplx = getenv("OPUS_COMPLEXITY");
    const char *env_vbr  = getenv("OPUS_VBR");
    const char *env_fms  = getenv("OPUS_FRAME_MS");

    ctx->tcp_host = (env_host && env_host[0]) ? env_host : AUDIO_TCP_DEFAULT_HOST;

    ctx->tcp_port = AUDIO_TCP_DEFAULT_PORT;
    if (env_port && env_port[0]) {
        int p = atoi(env_port);
        if (p > 0 && p < 65536) ctx->tcp_port = p;
    }

    ctx->opus_sample_rate = AUDIO_FS;
    ctx->opus_channels    = 1;

    ctx->bitrate    = (env_br && env_br[0]) ? atoi(env_br) : OPUS_BITRATE_DEFAULT;
    ctx->complexity = (env_cplx && env_cplx[0]) ? atoi(env_cplx) : OPUS_COMPLEXITY_DEFAULT;
    ctx->vbr        = (env_vbr && env_vbr[0]) ? atoi(env_vbr) : OPUS_VBR_DEFAULT;
    ctx->frame_ms   = (env_fms && env_fms[0]) ? atoi(env_fms) : OPUS_FRAME_MS_DEFAULT;

    if (ctx->complexity < 0) ctx->complexity = 0;
    if (ctx->complexity > 10) ctx->complexity = 10;
    if (ctx->frame_ms <= 0) ctx->frame_ms = OPUS_FRAME_MS_DEFAULT;
    if (ctx->bitrate <= 0) ctx->bitrate = OPUS_BITRATE_DEFAULT;
    ctx->vbr = ctx->vbr ? 1 : 0;

    // init current mode / fs (will be updated by main on first config)
    atomic_store(&ctx->current_mode, (int)FM_MODE);
    atomic_store(&ctx->current_fs_hz, 2000000.0);

    // init FM deviation state (already zero via memset, but explicit is ok)
    memset(&ctx->fm_dev, 0, sizeof(ctx->fm_dev));

    // init AM depth state (windowed, at AUDIO_FS)
    memset(&ctx->am_depth, 0, sizeof(ctx->am_depth));
    ctx->am_depth.env_min = 1e9f;
    ctx->am_depth.env_max = 0.0f;
    ctx->am_depth.counter = 0;
    ctx->am_depth.report_samples = (uint32_t)ctx->opus_sample_rate; // ~1s window @48k
    ctx->am_depth.depth_ema = 0.0f;

    // IQ filter cfg defaults (uses datatypes filter_t)
    ctx->iqf_cfg.type_filter  = BANDPASS_TYPE;   // "channel LP" semantics
    ctx->iqf_cfg.order_fliter = IQ_FILTER_ORDER;
    ctx->iqf_cfg.bw_filter_hz = IQ_FILTER_BW_FM_HZ;
    ctx->iqf_ready = 0;
}
