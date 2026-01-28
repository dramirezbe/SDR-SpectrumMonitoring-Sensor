/**
 * @file net_audio_retry.c
 * @brief Implementación de lógica de red robusta y reconexión automática.
 *
 * Incluye la configuración avanzada de sockets (timeouts y keep-alive) y la 
 * orquestación del ciclo de vida del transmisor de audio.
 */

#include "net_audio_retry.h"

/**
 * @addtogroup net_audio_retry_module
 * @{
 */

#ifndef MSG_NOSIGNAL
/** @brief Flag para evitar señales SIGPIPE en sistemas que no lo soportan nativamente. */
#define MSG_NOSIGNAL 0
#endif

/**
 * @brief Configura los tiempos de espera (timeouts) para operaciones de envío y recepción.
 * * @param[in] fd     Descriptor del socket.
 * @param[in] snd_ms Tiempo de espera para envío en milisegundos.
 * @param[in] rcv_ms Tiempo de espera para recepción en milisegundos.
 * @return int 0 en éxito, -1 si falló @c setsockopt.
 */
static int set_sock_timeouts(int fd, int snd_ms, int rcv_ms) {
    struct timeval tv;

    tv.tv_sec  = snd_ms / 1000;
    tv.tv_usec = (snd_ms % 1000) * 1000;
    if (setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &tv, sizeof(tv)) != 0) return -1;

    tv.tv_sec  = rcv_ms / 1000;
    tv.tv_usec = (rcv_ms % 1000) * 1000;
    if (setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv)) != 0) return -1;

    return 0;
}

/**
 * @brief Habilita y configura el mecanismo de Keep-Alive de TCP.
 * * Configura el socket para enviar sondas de mantenimiento de conexión, permitiendo
 * detectar desconexiones "silenciosas" o caídas de red de forma proactiva.
 * * @param[in] fd Descriptor del socket.
 * @note Los tiempos están hardcodeados para detectar fallos en aproximadamente 19 segundos 
 * (10s idle + 3 probes * 3s).
 */
static void enable_tcp_keepalive(int fd) {
    int yes = 1;
    setsockopt(fd, SOL_SOCKET, SO_KEEPALIVE, &yes, sizeof(yes));

#ifdef TCP_KEEPIDLE
    int idle = 10;   // segundos antes de empezar probes
    setsockopt(fd, IPPROTO_TCP, TCP_KEEPIDLE, &idle, sizeof(idle));
#endif
#ifdef TCP_KEEPINTVL
    int intvl = 3;   // intervalo entre probes
    setsockopt(fd, IPPROTO_TCP, TCP_KEEPINTVL, &intvl, sizeof(intvl));
#endif
#ifdef TCP_KEEPCNT
    int cnt = 3;     // probes fallidos antes de declarar muerto
    setsockopt(fd, IPPROTO_TCP, TCP_KEEPCNT, &cnt, sizeof(cnt));
#endif
}

int connect_tcp_net_audio(const char *host, int port) {
    if (!host || port <= 0 || port > 65535) return -1;

    char port_str[16];
    snprintf(port_str, sizeof(port_str), "%d", port);

    struct addrinfo hints;
    memset(&hints, 0, sizeof(hints));
    hints.ai_family   = AF_UNSPEC;
    hints.ai_socktype = SOCK_STREAM;

    struct addrinfo *res = NULL;
    if (getaddrinfo(host, port_str, &hints, &res) != 0) return -1;

    int fd = -1;
    for (struct addrinfo *ai = res; ai; ai = ai->ai_next) {
        fd = socket(ai->ai_family, ai->ai_socktype, ai->ai_protocol);
        if (fd < 0) continue;

        // timeouts para evitar bloqueos largos
        (void)set_sock_timeouts(fd, 1500, 1500); // 1.5s send/recv timeout
        enable_tcp_keepalive(fd);

        if (connect(fd, ai->ai_addr, ai->ai_addrlen) == 0) {
            break; // ok
        }
        close(fd);
        fd = -1;
    }

    freeaddrinfo(res);
    return fd;
}

int send_all_net_audio(int fd, const void *buf, size_t len) {
    const uint8_t *p = (const uint8_t*)buf;
    size_t sent = 0;

    while (sent < len) {
        ssize_t n = send(fd, p + sent, len - sent, MSG_NOSIGNAL);
        if (n > 0) {
            sent += (size_t)n;
            continue;
        }
        if (n == 0) {
            errno = ECONNRESET;
            return -1;
        }
        // n < 0
        if (errno == EINTR) continue;

        // timeout / pipe / reset / network down: falla y deja que arriba reconecte
        return -1;
    }
    return 0;
}
void sleep_cancelable_ms(int total_ms, volatile bool *running_flag) {
    const int step = 100; // 100 ms
    int left = total_ms;
    while (left > 0) {
        if (running_flag && !(*running_flag)) break;
        int s = (left > step) ? step : left;
        usleep(s * 1000);
        left -= s;
    }
}

int ensure_tx_with_retry(audio_stream_ctx_t *ctx, opus_tx_t **ptx, volatile bool *running_flag) {
    if (*ptx) return 0;

    opus_tx_cfg_t cfg;
    memset(&cfg, 0, sizeof(cfg));
    cfg.sample_rate = ctx->opus_sample_rate;
    cfg.channels    = ctx->opus_channels;
    cfg.bitrate     = ctx->bitrate;
    cfg.complexity  = ctx->complexity;
    cfg.vbr         = ctx->vbr;

    while (running_flag && *running_flag) {
        opus_tx_t *tx = opus_tx_create(ctx->tcp_host, ctx->tcp_port, &cfg);
        if (tx) {
            *ptx = tx;
            fprintf(stderr,
                    "[AUDIO] Connected Opus TX to %s:%d (sr=%d ch=%d frame_ms=%d bitrate=%d vbr=%d cplx=%d)\n",
                    ctx->tcp_host, ctx->tcp_port,
                    cfg.sample_rate, cfg.channels, ctx->frame_ms, cfg.bitrate, cfg.vbr, cfg.complexity);
            return 0;
        }

        fprintf(stderr,
                "[AUDIO] Waiting for TCP/Opus in (%s:%d)\n",
                ctx->tcp_host, ctx->tcp_port);

        sleep_cancelable_ms(RECONNECT_DELAY_MS, running_flag);
    }
    return -1;
}

/** @} */