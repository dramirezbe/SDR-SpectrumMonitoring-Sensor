/**
 * @file audio_stream_ctx.h
 * @brief Gestión del contexto de streaming de audio y configuración de Opus.
 *
 * Este archivo define la estructura de control principal para el flujo de audio
 * desde la demodulación hasta la salida hacia el gateway TCP en formato Opus.
 */

#ifndef AUDIO_STREAM_CTX_H
#define AUDIO_STREAM_CTX_H

#include <stdatomic.h>
#include <stdint.h>
#include <string.h>
#include <stdlib.h>
#include "datatypes.h"
#include "fm_radio.h"
#include "iq_iir_filter.h"
#include "am_radio_local.h"

/**
 * @defgroup audio_module Audio Streaming Context
 * @ingroup rf_binary
 * @brief Módulo de gestión de audio, filtrado IIR y compresión Opus.
 * @{
 */

/** @name Constantes de Audio y PSD */
/**@{*/
#define AUDIO_CHUNK_SAMPLES 16384 /**< Tamaño del bloque de procesamiento de audio. */
#define PSD_SAMPLES_TOTAL   2097152 /**< Total de muestras para el cálculo de la PSD. */
#define AUDIO_FS            48000   /**< Frecuencia de muestreo estándar para el codificador Opus (Hz). */
/**@}*/

/** @name Defaults de Streaming Opus */
/**@{*/
#define AUDIO_TCP_DEFAULT_HOST "127.0.0.1" /**< IP por defecto para el gateway de audio. */
#define AUDIO_TCP_DEFAULT_PORT 9000        /**< Puerto TCP por defecto. */
#define OPUS_FRAME_MS_DEFAULT  20          /**< Duración por defecto del frame Opus (ms). */
#define OPUS_BITRATE_DEFAULT   32000       /**< Bitrate por defecto para el stream de voz/audio (bps). */
#define OPUS_COMPLEXITY_DEFAULT 5          /**< Complejidad computacional del encoder (0-10). */
#define OPUS_VBR_DEFAULT       0           /**< Modo por defecto: CBR (0). */
/**@}*/

/**
 * @brief Parámetros de diseño para el filtro de canal IQ.
 * El ancho de banda para WBFM se define típicamente como \f[ BW = 200 \text{ kHz} \f]
 */
static float  IQ_FILTER_BW_FM_HZ      = 200000.0f;
static int    IQ_FILTER_ORDER         = 6;         /**< Orden del filtro Butterworth (par). */

#ifdef __cplusplus
extern "C" {
#endif

/**
 * @brief Estructura de contexto para el stream de audio.
 * * Mantiene el estado de los demoduladores, la configuración de red y las métricas
 * de calidad de señal (AM/FM).
 */
typedef struct audio_stream_ctx {
    fm_radio_t *fm_radio;       /**< Instancia del demodulador FM. */
    am_radio_local_t *am_radio; /**< Instancia del demodulador AM. */

    const char *tcp_host;       /**< Dirección del servidor TCP. */
    int tcp_port;               /**< Puerto del servidor TCP. */

    int opus_sample_rate;       /**< Tasa de muestreo de salida (usualmente 48kHz). */
    int opus_channels;          /**< Número de canales (1 = Mono). */
    int bitrate;                /**< Bitrate configurado para Opus. */
    int complexity;             /**< Nivel de complejidad del codificador. */
    int vbr;                    /**< Flag de Variable Bitrate (1=VBR, 0=CBR). */
    int frame_ms;               /**< Latencia del frame en milisegundos. */

    _Atomic int    current_mode;   /**< Modo RF actual (fm_mode_t casted to int). */
    _Atomic double current_fs_hz;  /**< Frecuencia de muestreo de entrada (IQ rate). */

    iq_iir_filter_t iqf;           /**< Filtro IIR para pre-procesamiento de señal IQ. */
    filter_audio_t  iqf_cfg;       /**< Configuración del filtro IQ. */
    int             iqf_ready;     /**< Flag que indica si el filtro está inicializado. */

    fm_dev_state_t  fm_dev;        /**< Estado de la medición de desviación FM. */
    am_depth_state_t am_depth;     /**< Estado de la medición de profundidad AM. */
} audio_stream_ctx_t;

/**
 * @brief Inicializa el contexto de audio con valores por defecto y variables de entorno.
 * * Realiza la puesta a cero de la estructura y carga las configuraciones iniciales,
 * priorizando las variables de entorno si están presentes.
 *
 * @param[out] ctx Puntero a la estructura de contexto a inicializar.
 * @param[in]  fm  Puntero a una instancia válida de fm_radio_t.
 * @param[in]  am  Puntero a una instancia válida de am_radio_local_t.
 */
void audio_stream_ctx_defaults(audio_stream_ctx_t *ctx, fm_radio_t *fm, am_radio_local_t *am);

/** @} */

#ifdef __cplusplus
}
#endif

#endif // AUDIO_STREAM_CTX_H
