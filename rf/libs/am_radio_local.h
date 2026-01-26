/**
 * @file am_radio_local.h
 * @brief Demodulador AM robusto con diezmado CIC, normalización y AGC.
 * * Esta versión extiende el demodulador básico incorporando un filtro CIC de orden 2,
 * seguimiento de la media de la portadora para normalización y un control automático
 * de ganancia (AGC) por RMS.
 */

#ifndef AM_RADIO_LOCAL_H
#define AM_RADIO_LOCAL_H

#include <stdint.h>
#include <string.h>
#include <math.h>
#include <complex.h>
#include <float.h>

#ifdef __cplusplus
extern "C" {
#endif

#include "datatypes.h"

/**
 * @defgroup am_radio_local_module AM Radio Local Demodulator
 * @ingroup rf_binary
 * @brief Demodulador AM robusto con diezmado CIC, normalización y AGC.
 */

/** * @brief Factor de suavizado (EMA) para el cálculo de la profundidad de modulación AM.
 * * Un valor de 0.15 da peso al 15% de la nueva medición, filtrando variaciones rápidas.
 */
#define DEPTH_EMA_ALPHA 0.15f

/**
 * @brief Estructura de estado extendida para el demodulador AM local.
 * * Contiene los estados para el diezmador CIC, el estimador de portadora (mean tracker)
 * y el sistema de control de ganancia adaptativo.
 */
typedef struct {
    // --- Campos heredados / Compatibilidad ---
    double env_acc;             /**< @deprecated Uso interno de acumulación (mantenido por compatibilidad). */
    int    env_count;           /**< Contador de muestras para el diezmador CIC. */
    int    decim_factor;        /**< Factor de diezmado \f$ R = \frac{f_{iq}}{f_{audio}} \f$. */

    float gain;                 /**< Ganancia escalar final aplicada antes de la salida PCM. */

    // --- DC blocker (audio) ---
    float dc_r;                 /**< Coeficiente de realimentación del bloqueador DC. */
    float dc_x1;                /**< Estado anterior de entrada del bloqueador DC. */
    float dc_y1;                /**< Estado anterior de salida del bloqueador DC. */

    // --- Biquad LPF (Filtro de audio) ---
    float b0, b1, b2, a1, a2;   /**< Coeficientes del filtro biquad (RBJ). */
    float z1, z2;               /**< Estados de la Forma Directa II Transpuesta. */
    int enable_dc_block;        /**< Flag para activar/desactivar el filtro DC. */
    int enable_lpf;             /**< Flag para activar/desactivar el filtro pasa bajos. */

    // --- Diezmador CIC (Cascaded Integrator-Comb) ---
    double cic_i1, cic_i2;      /**< Acumuladores de los integradores de orden 2. */
    double cic_c1_z, cic_c2_z;  /**< Memorias de los peines (combs) del diezmador. */

    // --- Seguimiento de envolvente (Normalización) ---
    float  env_mean;            /**< Valor medio estimado de la envolvente (nivel de portadora). */
    float  env_mean_alpha;      /**< Coeficiente de suavizado para el tracker de la media. */

    // --- AGC por RMS (Control Automático de Ganancia) ---
    float  agc_gain;            /**< Multiplicador adaptativo actual del AGC. */
    float  agc_rms2;            /**< Estado del estimador RMS al cuadrado (EMA). */
    float  agc_target_rms;      /**< Valor RMS de referencia deseado para la señal. */
    float  agc_max_gain;        /**< Límite superior de ganancia del AGC. */
    float  agc_min_gain;        /**< Límite inferior de ganancia del AGC. */
    float  agc_attack;          /**< Velocidad de respuesta ante incrementos de señal (reducción de ganancia). */
    float  agc_release;         /**< Velocidad de respuesta ante decrementos de señal (aumento de ganancia). */

} am_radio_local_t;

/**
 * @brief Inicializa el estado del demodulador AM robusto.
 * * Configura los filtros, el diezmador CIC y los parámetros del AGC basándose en
 * las frecuencias de muestreo proporcionadas.
 * * @param[out] r         Puntero a la estructura de estado.
 * @param[in]  fs_iq     Frecuencia de muestreo de la señal IQ de entrada.
 * @param[in]  audio_fs  Frecuencia de muestreo de audio de salida (típicamente 48000).
 */
void am_radio_local_init(am_radio_local_t *r, double fs_iq, int audio_fs);

/**
 * @brief Procesa un bloque IQ y produce audio PCM de 16 bits.
 * * El proceso sigue la cadena: Detección -> CIC -> Normalización -> DC Block -> LPF -> AGC -> Gain.
 * * @param[in,out] r         Puntero al estado del radio.
 * @param[in]     sig       Buffer de entrada IQ.
 * @param[out]    pcm_out   Buffer de salida para audio PCM16.
 * @param[in,out] depth_st  Estado opcional para métricas de profundidad de modulación.
 * @return int              Cantidad de muestras escritas en pcm_out.
 */
int am_radio_local_iq_to_pcm(am_radio_local_t *r,
                            signal_iq_t *sig,
                            int16_t *pcm_out,
                            am_depth_state_t *depth_st);

/** @} */

#ifdef __cplusplus
}
#endif

#endif // AM_RADIO_LOCAL_H