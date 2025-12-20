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

// --- RF Configuration Enums ---
typedef enum {
    REALTIME_MODE,
    CAMPAIGN_MODE,
    FM_MODE,
    AM_MODE
} rf_mode_t;

// Add these to your datatypes.h if not already there
typedef enum {
    DEMOD_OFF,
    DEMOD_FM,
    DEMOD_AM
} demod_type_t;

// --- Demodulation Config ---
typedef struct {
    double center_freq;
    double bw_hz;
} DemodeConfig_t;

// --- Buffer Configuration ---
typedef struct {
    size_t total_bytes;
    int rb_size;    
} RB_cfg_t;

typedef enum {
    LOWPASS_TYPE,
    HIGHPASS_TYPE,
    BANDPASS_TYPE,
    BANDSTOP_TYPE,
} type_filter_t;

typedef struct {
    float bw_filter_hz;
    type_filter_t type_filter;  
    int order_filter;           
    double sample_rate;         
    double prev_output_i;
    double prev_output_q;
} filter_t;

// Updated DesiredCfg_t
typedef struct {
    rf_mode_t rf_mode;
    Psd_method method_psd;  // New: WELCH or PFB
    
    // Hardware params
    uint64_t center_freq;
    double sample_rate;
    double span;
    int lna_gain;
    int vga_gain;
    bool amp_enabled;
    int antenna_port;
    int ppm_error;

    // PSD params
    int rbw;
    double overlap;
    PsdWindowType_t window_type;
    char *scale;

    // Optional Filter Block
    bool filter_enabled;
    filter_t filter_cfg;

    // Optional Demod Block
    bool demod_enabled;
    demod_type_t demod_type;
    DemodeConfig_t demod_cfg;
} DesiredCfg_t;

#endif