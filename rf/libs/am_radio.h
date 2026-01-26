/**
 * @file am_radio.h
 * @brief Demodulador de Amplitud Modulada (AM).
 *
 * Proporciona las estructuras y funciones necesarias para realizar la detección 
 * de envolvente, diezmado de señal, eliminación de componente DC (portadora) 
 * y filtrado de audio para señales AM.
 */

#ifndef AM_RADIO_H
#define AM_RADIO_H

#include "datatypes.h"
#include <stdint.h>
#include <math.h>
#include <string.h>

#ifndef M_PI
#define M_PI 3.14159265358979323846
#endif

/**
 * @defgroup am_module AM Radio Demodulator
 * @ingroup rf_binary
 * @brief Procesamiento de señales AM desde IQ a PCM.
 * @{
 */

/** @brief Frecuencia de corte del filtro pasa bajos de audio (estilo voz conservador). */
#define AM_AUDIO_LPF_HZ   4000.0f

/** @brief Factor de calidad Q para el filtro Biquad (0.707 = Butterworth). */
#define AM_AUDIO_Q        0.707f

/** @brief Factor alfa para el promedio móvil exponencial (EMA) de la profundidad de modulación. */
#define DEPTH_EMA_ALPHA   0.20f

/**
 * @brief Estructura de estado para el demodulador AM.
 * * Contiene los acumuladores para diezmado, estados del filtro DC-Blocker
 * y coeficientes del filtro Biquad para la etapa de audio.
 */
typedef struct {
    double audio_acc;       /**< Acumulador para el promedio de muestras (decimation). */
    int samples_in_acc;     /**< Contador de muestras acumuladas. */
    int decim_factor;       /**< Factor de diezmado calculado \f$ M = \frac{f_s}{f_{audio}} \f$. */

    float gain;             /**< Ganancia de salida para el ajuste de volumen PCM. */

    // --- DC blocker (high-pass) ---
    float dc_r;             /**< Coeficiente de realimentación del DC blocker. */
    float dc_x1;            /**< Estado anterior de la entrada \f$ x[n-1] \f$. */
    float dc_y1;            /**< Estado anterior de la salida \f$ y[n-1] \f$. */

    // --- Biquad LPF (RBJ cookbook) ---
    float b0, b1, b2, a1, a2; /**< Coeficientes del filtro biquad. */
    float z1, z2;             /**< Estados del filtro (Direct Form II Transposed). */

    int enable_dc_block;    /**< Flag para habilitar/deshabilitar el filtro DC blocker. */
    int enable_lpf;         /**< Flag para habilitar/deshabilitar el filtro pasa bajos. */
} am_radio_t;

/**
 * @brief Configura el estado inicial del demodulador AM.
 * * Calcula el factor de diezmado y pre-calcula los coeficientes de los filtros
 * basándose en las frecuencias de entrada y salida.
 *
 * @param[in,out] r         Puntero a la instancia de la estructura de radio AM.
 * @param[in]     fs        Frecuencia de muestreo de la señal IQ de entrada (Hz).
 * @param[in]     audio_fs  Frecuencia de muestreo de audio deseada (Hz).
 */
void am_radio_init(am_radio_t *r, double fs, int audio_fs);

/**
 * @brief Procesa un bloque de señal IQ y genera muestras de audio PCM16.
 * * Realiza la detección de envolvente mediante:
 * \f[ E[n] = \sqrt{I[n]^2 + Q[n]^2} \f]
 * * @param[in,out] r         Puntero al estado del radio AM.
 * @param[in]     sig       Estructura con el buffer de señal IQ de entrada.
 * @param[out]    pcm_out   Buffer para almacenar las muestras de audio generadas.
 * @param[in,out] depth_st  Estado opcional para actualizar métricas de profundidad AM.
 * @return int              Número de muestras de audio generadas en esta llamada.
 */
int am_radio_iq_to_pcm(am_radio_t *r, signal_iq_t *sig, int16_t *pcm_out, am_depth_state_t *depth_st);

/** @} */

#endif
