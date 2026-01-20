/**
 * @file opus_tx.h
 * @brief Interfaz para la transmisión de audio codificado en Opus sobre TCP.
 * * Este módulo proporciona una abstracción para inicializar un codificador Opus,
 * establecer una conexión TCP y enviar tramas de audio con una cabecera personalizada.
 */

#pragma once
#include <stdint.h>
#include <stddef.h>
#include <opus/opus.h>

#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <arpa/inet.h>
#include <sys/socket.h>

#ifdef __cplusplus
extern "C" {
#endif

/**
 * @defgroup opus_module Opus Module
 * @ingroup rf_binary
 * @brief Modulo para la transmision de audio codificado en Opus sobre TCP
 * @{}
 */

/**
 * @brief Estructura opaca que representa el contexto del transmisor Opus.
 */
typedef struct opus_tx opus_tx_t;

/**
 * @brief Configuración para el codificador Opus.
 * * Define los parámetros de calidad y comportamiento del flujo de audio.
 */
typedef struct {
    int sample_rate;    /**< Frecuencia de muestreo en Hz (8000, 12000, 16000, 24000, 48000). */
    int channels;       /**< Número de canales (1 para mono, 2 para estéreo). */
    int bitrate;        /**< Tasa de bits en bps (ej. 64000). */
    int complexity;     /**< Complejidad computacional (0-10). */
    int vbr;            /**< Variable Bitrate: 1 para habilitar, 0 para CBR (Constant Bitrate). */
} opus_tx_cfg_t;

/**
 * @brief Crea una instancia del transmisor e inicia la conexión de red.
 * * Reserva memoria para el contexto, inicializa el motor Opus con la configuración
 * proporcionada e intenta establecer una conexión TCP con el host remoto.
 * * @param[in] host Dirección IP o nombre de dominio del servidor destino.
 * @param[in] port Puerto TCP de destino.
 * @param[in] cfg  Puntero a la estructura de configuración del codificador.
 * * @return opus_tx_t* Puntero al contexto creado, o NULL en caso de error (red, memoria o parámetros).
 * @note La memoria retornada debe ser liberada con opus_tx_destroy().
 */
opus_tx_t* opus_tx_create(const char *host, int port, const opus_tx_cfg_t *cfg);

/**
 * @brief Codifica y envía una trama de audio PCM.
 * * Toma una muestra de audio en crudo, la comprime usando el formato Opus y la
 * transmite a través del socket TCP precedida por una cabecera de protocolo @ref OpusFrameHeader.
 * * @param[in,out] tx            Contexto del transmisor.
 * @param[in]     pcm           Puntero al buffer con muestras de audio (int16_t).
 * @param[in]     frame_samples Número de muestras por canal (ej. 960 para 20ms a 48kHz).
 * * @return int 0 si la operación fue exitosa, -1 si ocurrió un error en la codificación o envío.
 */
int  opus_tx_send_frame(opus_tx_t *tx, const int16_t *pcm, int frame_samples);

/**
 * @brief Cierra la conexión y libera los recursos asociados.
 * * Finaliza la conexión TCP, destruye el codificador interno de Opus y libera
 * la memoria del contexto.
 * * @param[in] tx Contexto a destruir. Si es NULL, la función no hace nada.
 */
void opus_tx_destroy(opus_tx_t *tx);

/**
 * @brief Obtiene el descriptor de archivo (socket) asociado al transmisor.
 * * Útil para integrar el transmisor en bucles de eventos (select/poll/epoll)
 * o para configurar opciones de socket adicionales.
 * * @param[in] tx Contexto del transmisor.
 * @return int Descriptor del socket, o -1 si el contexto es inválido.
 */
int  opus_tx_fd(const opus_tx_t *tx);

/** @} */

#ifdef __cplusplus
}
#endif