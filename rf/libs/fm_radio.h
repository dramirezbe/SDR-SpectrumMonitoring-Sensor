/**
 * @file fm_radio.h
 * @brief Demodulador FM y procesamiento de audio para señales IQ.
 *
 * Proporciona las estructuras y funciones necesarias para convertir una señal 
 * compleja (IQ) en audio PCM, incluyendo etapas de filtrado y métricas de desviación.
 */

#ifndef FM_RADIO_H
#define FM_RADIO_H

#include "datatypes.h"
#include <stdint.h>
#include <fftw3.h>
#include <complex.h>
#include <math.h>
#include <string.h>

#ifndef M_PI
#define M_PI 3.14159265358979323846
#endif

/**
 * @defgroup fm_module FM Module
 * @ingroup rf_binary
 * @brief Demodulador FM y procesamiento de audio para señales IQ.
 * @{
 */

#define DEV_EMA_ALPHA 0.10f  /**< Factor de suavizado para la métrica de desviación. */

/**
 * @brief Estructura de estado del demodulador FM.
 * * Mantiene los registros necesarios para el discriminador de fase y la cadena de filtrado.
 */
typedef struct {
    double _Complex prev_sample; /**< Almacena la muestra anterior para el cálculo de \f$ \Delta\phi \f$. */

    double audio_acc;           /**< Acumulador para diezmado. */
    int samples_in_acc;         /**< Contador de muestras acumuladas. */
    int decim_factor;           /**< Factor de diezmado \f$ M = f_{in} / f_{out} \f$. */

    float deemph_acc;           /**< Estado del filtro de de-énfasis. */
    float deemph_alpha;         /**< Coeficiente \f$ \alpha \f$ del de-énfasis. */

    float gain;                 /**< Escalamiento para salida PCM16. */

    /** @name DC Blocker */
    /**@{*/
    float dc_r;                 /**< Radio del polo \f$ r \f$. */
    float dc_x1;                /**< Estado de entrada \f$ x[n-1] \f$. */
    float dc_y1;                /**< Estado de salida \f$ y[n-1] \f$. */
    /**@}*/

    /** @name Biquad LPF */
    /**@{*/
    float b0, b1, b2;           /**< Coeficientes del numerador del biquad. */
    float a1, a2;               /**< Coeficientes del denominador (con \f$ a_0 = 1 \f$). */
    float z1, z2;               /**< Registros de estado de la Forma Directa II Transpuesta. */
    /**@}*/

    int enable_dc_block;        /**< Flag de activación del bloqueador de DC. */
    int enable_lpf;             /**< Flag de activación del filtro paso bajo. */
} fm_radio_t;

/**
 * @brief Inicializa el estado del radio y calcula coeficientes de filtrado.
 * * @param radio      Puntero a la estructura de estado.
 * @param fs         Frecuencia de muestreo de entrada (Hz).
 * @param audio_fs   Frecuencia de muestreo de audio deseada (Hz).
 * @param deemph_us  Constante de tiempo de de-énfasis (\f$ \mu s \f$).
 */
void fm_radio_init(fm_radio_t *radio, double fs, int audio_fs, int deemph_us);

/**
 * @brief Procesa un bloque IQ y genera muestras de audio PCM de 16 bits.
 * * @param[in,out] radio     Contexto de estado del radio.
 * @param[in]     sig       Buffer de señal IQ de entrada.
 * @param[out]    pcm_out   Buffer de salida para muestras PCM16.
 * @param[in,out] dev_st    Métricas de desviación FM (opcional).
 * @param[in]     fs_demod  Tasa de muestreo de la etapa de demodulación.
 * @return int              Número de muestras de audio escritas en pcm_out.
 */
int fm_radio_iq_to_pcm(fm_radio_t *radio, signal_iq_t *sig, int16_t *pcm_out, fm_dev_state_t *dev_st, int fs_demod);

/** @} */

#endif