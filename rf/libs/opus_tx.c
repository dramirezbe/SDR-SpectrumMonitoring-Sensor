/**
 * @file opus_tx.c
 * @brief Implementación interna del transmisor Opus.
 * * Contiene la lógica de red y el encapsulamiento de datos para la transmisión.
 */

#include "opus_tx.h"

/**
 * @addtogroup opus_module
 * @{
 */

/**
 * @brief Cabecera de red para tramas Opus.
 * * Se envía de forma binaria antes de cada payload Opus para permitir
 * la sincronización y reconstrucción en el receptor.
 */
#pragma pack(push, 1)
typedef struct {
    uint32_t magic;       /**< Identificador único 'OPU0' (0x4F505530). */
    uint32_t seq;         /**< Número de secuencia incremental para detectar pérdidas. */
    uint32_t sample_rate; /**< Frecuencia de muestreo de la trama. */
    uint16_t channels;    /**< Cantidad de canales de audio. */
    uint16_t payload_len; /**< Tamaño en bytes de los datos codificados que siguen. */
} OpusFrameHeader;
#pragma pack(pop)

/**
 * @brief Contexto interno del transmisor.
 * * Mantiene el estado de la conexión, el contador de secuencia y el estado del codificador.
 */
struct opus_tx {
    int sock_fd;        /**< Descriptor del socket TCP. */
    uint32_t seq;       /**< Contador para el número de secuencia. */
    OpusEncoder *enc;   /**< Puntero al estado del codificador Opus. */
    opus_tx_cfg_t cfg;  /**< Copia local de la configuración. */
};

/**
 * @brief Garantiza el envío de un bloque completo de datos a través de TCP.
 * * Debido a la naturaleza de los sockets de flujo, una llamada a `send` puede no
 * enviar todos los bytes solicitados. Esta función itera hasta completar el envío.
 * * @param[in] fd  Descriptor del socket.
 * @param[in] buf Puntero a los datos a enviar.
 * @param[in] n   Cantidad de bytes a transmitir.
 * * @return int 0 en caso de éxito, -1 si la conexión se cierra o falla.
 */
static int send_all(int fd, const void *buf, size_t n) {
    const uint8_t *p = (const uint8_t*)buf;
    while (n > 0) {
        ssize_t w = send(fd, p, n, 0);
        if (w <= 0) return -1;
        p += (size_t)w;
        n -= (size_t)w;
    }
    return 0;
}

/**
 * @brief Crea un socket TCP y se conecta al destino especificado.
 * * Realiza la resolución de dirección (vía aton/pton) y establece la comunicación.
 * * @param[in] host Cadena con la dirección IP de destino.
 * @param[in] port Puerto de destino.
 * * @return int Descriptor del socket conectado, o -1 en caso de error.
 */
static int connect_tcp(const char *host, int port) {
    int fd = socket(AF_INET, SOCK_STREAM, 0);
    if (fd < 0) return -1;

    struct sockaddr_in addr;
    memset(&addr, 0, sizeof(addr));
    addr.sin_family = AF_INET;
    addr.sin_port = htons((uint16_t)port);
    if (inet_pton(AF_INET, host, &addr.sin_addr) != 1) {
        close(fd);
        return -1;
    }
    if (connect(fd, (struct sockaddr*)&addr, sizeof(addr)) != 0) {
        close(fd);
        return -1;
    }
    return fd;
}

opus_tx_t* opus_tx_create(const char *host, int port, const opus_tx_cfg_t *cfg) {
    if (!host || !cfg) return NULL;

    opus_tx_t *tx = (opus_tx_t*)calloc(1, sizeof(*tx));
    if (!tx) return NULL;

    tx->cfg = *cfg;
    tx->sock_fd = connect_tcp(host, port);
    if (tx->sock_fd < 0) {
        free(tx);
        return NULL;
    }

    int err = 0;
    tx->enc = opus_encoder_create(cfg->sample_rate, cfg->channels, OPUS_APPLICATION_AUDIO, &err);
    if (!tx->enc || err != OPUS_OK) {
        close(tx->sock_fd);
        free(tx);
        return NULL;
    }

    opus_encoder_ctl(tx->enc, OPUS_SET_BITRATE(cfg->bitrate));
    opus_encoder_ctl(tx->enc, OPUS_SET_COMPLEXITY(cfg->complexity));
    opus_encoder_ctl(tx->enc, OPUS_SET_VBR(cfg->vbr));

    tx->seq = 0;
    return tx;
}

int opus_tx_send_frame(opus_tx_t *tx, const int16_t *pcm, int frame_samples) {
    if (!tx || !pcm) return -1;

    uint8_t opus_out[1500];
    int n = opus_encode(tx->enc, pcm, frame_samples, opus_out, (opus_int32)sizeof(opus_out));
    if (n < 0) return -1;

    OpusFrameHeader h;
    h.magic      = htonl(0x4F505530);
    h.seq        = htonl(tx->seq++);
    h.sample_rate= htonl((uint32_t)tx->cfg.sample_rate);
    h.channels   = htons((uint16_t)tx->cfg.channels);
    h.payload_len= htons((uint16_t)n);

    if (send_all(tx->sock_fd, &h, sizeof(h)) != 0) return -1;
    if (send_all(tx->sock_fd, opus_out, (size_t)n) != 0) return -1;
    return 0;
}

void opus_tx_destroy(opus_tx_t *tx) {
    if (!tx) return;
    if (tx->sock_fd >= 0) close(tx->sock_fd);
    if (tx->enc) opus_encoder_destroy(tx->enc);
    free(tx);
}

int opus_tx_fd(const opus_tx_t *tx) {
    return tx ? tx->sock_fd : -1;
}

/** @} */
