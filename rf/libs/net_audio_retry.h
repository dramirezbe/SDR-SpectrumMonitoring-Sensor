#ifndef NET_AUDIO_RETRY_H
#define NET_AUDIO_RETRY_H

#include <stddef.h>
#include <stdbool.h>


#define RECONNECT_DELAY_MS 1000

#ifdef __cplusplus
extern "C" {
#endif

int connect_tcp(const char *host, int port);
int send_all(int fd, const void *buf, size_t len);

// <-- agrega esto si rf_audio.c la llama:
void sleep_cancelable_ms(int total_ms, volatile bool *running_flag);

// ensure_tx_with_retry(...)
typedef struct audio_stream_ctx audio_stream_ctx_t;
#include "opus_tx.h"
int ensure_tx_with_retry(audio_stream_ctx_t *ctx, opus_tx_t **ptx, volatile bool *running_flag);

#ifdef __cplusplus
}
#endif

#endif
