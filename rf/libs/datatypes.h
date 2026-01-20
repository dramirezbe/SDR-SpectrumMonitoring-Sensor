/**
 * @file datatypes.h
 * @brief Definiciones de tipos de datos globales y estructuras para el sistema SDR.
 *
 * Este archivo centraliza las estructuras de datos fundamentales para el manejo de 
 * señales IQ, algoritmos de Densidad Espectral de Potencia (PSD), configuraciones 
 * de hardware y métricas de calidad de señal.
 */

#ifndef DATATYPES_H
#define DATATYPES_H

#include <complex.h>
#include <stddef.h>
#include <stdint.h>
#include <stdbool.h>
#include <math.h>

#ifndef M_PI
/** @brief Valor de la constante pi. */
#define M_PI 3.14159265358979323846
#endif

/**
 * @defgroup rf_datatypes Data Types
 * @ingroup rf_binary
 * @brief Tipos de datos globales y estructuras para el sistema SDR.
 * @{
 */

/**
 * @brief Estructura para el manejo de señales en cuadratura (IQ).
 */
typedef struct {
    double _Complex* signal_iq; /**< Puntero al buffer de muestras complejas. */
    size_t n_signal;          /**< Número total de muestras en el buffer. */
} signal_iq_t;

/**
 * @brief Tipos de ventanas de suavizado para el procesamiento espectral.
 */
typedef enum {
    HAMMING_TYPE,     /**< Ventana Hamming. */
    HANN_TYPE,        /**< Ventana Hann. */
    RECTANGULAR_TYPE, /**< Sin ventana (Rectangular). */
    BLACKMAN_TYPE,    /**< Ventana Blackman. */
    FLAT_TOP_TYPE,    /**< Ventana Flat Top (alta precisión de amplitud). */
    KAISER_TYPE,      /**< Ventana Kaiser. */
    TUKEY_TYPE,       /**< Ventana Tukey. */
    BARTLETT_TYPE     /**< Ventana Bartlett. */
} PsdWindowType_t;

/**
 * @brief Métodos disponibles para el cálculo de la Densidad Espectral de Potencia (PSD).
 */
typedef enum {
    WELCH, /**< Método de Welch (promediado de periodogramas). */
    PFB    /**< Polyphase Filter Bank (Banco de filtros polifase). */
} Psd_method;

/**
 * @brief Configuración de parámetros para el algoritmo PSD.
 */
typedef struct {
    PsdWindowType_t window_type; /**< Tipo de ventana a aplicar. */
    double sample_rate;          /**< Frecuencia de muestreo del hardware (Hz). */
    int nperseg;                 /**< Número de muestras por segmento. */
    int noverlap;                /**< Número de muestras solapadas entre segmentos. */
} PsdConfig_t;

/**
 * @brief Configuración del Ring Buffer y gestión de memoria.
 */
typedef struct {
    size_t total_bytes; /**< Tamaño total asignado en bytes. */
    int rb_size;        /**< Número de elementos en el ring buffer. */
} RB_cfg_t;

/**
 * @brief Configuración de límites de frecuencia para filtrado digital.
 */
typedef struct {
    int start_freq_hz; /**< Frecuencia de corte inferior (Hz). */
    int end_freq_hz;   /**< Frecuencia de corte superior (Hz). */
} filter_t;

/**
 * @brief Tipos de filtros de audio disponibles.
 */
typedef enum {
    LOWPASS_TYPE,  /**< Filtro Paso Bajo. */
    HIGHPASS_TYPE, /**< Filtro Paso Alto. */
    BANDPASS_TYPE  /**< Filtro Paso Banda. */
} type_filter_audio_t;

/**
 * @brief Estructura de configuración para filtros de audio.
 */
typedef struct {
    float bw_filter_hz;             /**< Ancho de banda del filtro (Hz). */
    type_filter_audio_t type_filter; /**< Topología del filtro. */
    int order_fliter;               /**< Orden del filtro (número de polos). */
} filter_audio_t;

/**
 * @brief Modos de operación del receptor RF.
 */
typedef enum {
    PSD_MODE, /**< Modo espectrograma/visualización (sin audio). */
    FM_MODE,  /**< Demodulación de Frecuencia. */
    AM_MODE   /**< Demodulación de Amplitud. */
} rf_mode_t;

/**
 * @brief Configuración maestra deseada para el hardware y procesamiento.
 */
typedef struct {
    rf_mode_t rf_mode;      /**< Modo de operación actual. */
    Psd_method method_psd;  /**< Algoritmo PSD seleccionado. */
    
    /** @name Parámetros de Hardware */
    /**@{*/
    uint64_t center_freq; /**< Frecuencia central de sintonía (Hz). */
    double sample_rate;   /**< Frecuencia de muestreo (Sps). */
    int lna_gain;         /**< Ganancia del amplificador de bajo ruido (LNA). */
    int vga_gain;         /**< Ganancia del amplificador de ganancia variable (VGA). */
    bool amp_enabled;     /**< Estado del amplificador de potencia interno. */
    int antenna_port;     /**< Puerto de antena seleccionado. */
    int ppm_error;        /**< Corrección de error del oscilador en PPM. */
    /**@}*/

    /** @name Parámetros de Análisis Espectral */
    /**@{*/
    int rbw;                     /**< Resolution Bandwidth (Hz). */
    double overlap;              /**< Porcentaje de solapamiento (0.0 a 1.0). */
    PsdWindowType_t window_type; /**< Ventana aplicada al PSD. */
    /**@}*/

    /** @name Bloque de Filtrado */
    /**@{*/
    bool filter_enabled;  /**< Habilitación del filtro digital. */
    filter_t filter_cfg;  /**< Configuración de frecuencias de corte. */
    /**@}*/
} DesiredCfg_t;

/**
 * @brief Estado de las métricas de profundidad de modulación AM.
 * * La profundidad de modulación \f$ m \f$ se calcula como:
 * \f[ m = \frac{A_{max} - A_{min}}{A_{max} + A_{min}} \f]
 */
typedef struct {
    float env_min;           /**< Amplitud mínima de la envolvente detectada. */
    float env_max;           /**< Amplitud máxima de la envolvente detectada. */
    uint32_t counter;        /**< Contador de muestras procesadas en la ventana actual. */
    uint32_t report_samples; /**< Tamaño de la ventana de reporte a tasa de audio. */
    float depth_ema;         /**< Profundidad de modulación suavizada por EMA. */
} am_depth_state_t;

/**
 * @brief Estado de las métricas de desviación de frecuencia FM.
 * * La desviación se estima a partir de la frecuencia instantánea \f$ f_i \f$:
 * \f[ EMA_n = (1 - \alpha) \cdot EMA_{n-1} + \alpha \cdot f_i \f]
 */
typedef struct {
    float dev_max_hz; /**< Desviación pico registrada en la ventana actual (Hz). */
    float dev_ema_hz; /**< Desviación promedio suavizada (EMA) en Hz. */
    uint32_t counter; /**< Contador de muestras procesadas. */
} fm_dev_state_t;

/** @} */

#endif