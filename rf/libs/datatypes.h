// libs/datatypes.h
#ifndef DATATYPES_H
#define DATATYPES_H

#include <complex.h>
#include <stddef.h>
#include <stdint.h>
#include <stdbool.h>
#include <math.h>

#ifndef M_PI
#define M_PI 3.14159265358979323846
#endif

// --- IQ Data ---
typedef struct {
    double complex* signal_iq;
    size_t n_signal;
} signal_iq_t;

// --- Windowing Enums ---
typedef enum {
    HAMMING_TYPE,
    HANN_TYPE,
    RECTANGULAR_TYPE,
    BLACKMAN_TYPE,
    FLAT_TOP_TYPE,
    KAISER_TYPE,
    TUKEY_TYPE,
    BARTLETT_TYPE
} PsdWindowType_t;

// --- PSD Method ---
typedef enum {
    WELCH,
    PFB
} Psd_method;

// --- PSD Configuration ---
typedef struct {
    PsdWindowType_t window_type;
    double sample_rate;
    int nperseg;
    int noverlap;
} PsdConfig_t;

// --- Buffer Configuration ---
typedef struct {
    size_t total_bytes;
    int rb_size;    
} RB_cfg_t;

typedef struct {
    int start_freq_hz;
    int end_freq_hz;
} filter_t;

typedef enum {
    LOWPASS_TYPE,
    HIGHPASS_TYPE,
    BANDPASS_TYPE
}type_filter_audio_t;


typedef struct {
    float bw_filter_hz;
    type_filter_audio_t type_filter;
    int order_fliter;
    
}filter_audio_t;

typedef enum {
    PSD_MODE, // When demodulation is "None" or invalid
    FM_MODE,  // When demodulation is "fm"
    AM_MODE   // When demodulation is "am"
} rf_mode_t;

// Updated DesiredCfg_t
typedef struct {
    rf_mode_t rf_mode;
    Psd_method method_psd;
    
    // Hardware params
    uint64_t center_freq;
    double sample_rate;
    int lna_gain;
    int vga_gain;
    bool amp_enabled;
    int antenna_port;
    int ppm_error;

    // PSD params
    int rbw;
    double overlap;
    PsdWindowType_t window_type;

    // Filter Block
    bool filter_enabled;
    filter_t filter_cfg; // Contains start_freq_hz and end_freq_hz
} DesiredCfg_t;

// --- RF Metrics (AM depth, FM deviation) ---
typedef struct {
    float env_min;
    float env_max;
    uint32_t counter;
    uint32_t report_samples;   // window length at AUDIO rate
    float depth_ema;           // m in [0..1] after clamp (EMA over windows)
} am_depth_state_t;

typedef struct {
    float dev_max_hz;          // peak within the current reporting window
    float dev_ema_hz;          // smoothed deviation (EMA)
    uint32_t counter;
} fm_dev_state_t;

#endif