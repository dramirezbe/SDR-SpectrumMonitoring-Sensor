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

// --- Demodulation Config ---
typedef struct {
    double center_freq;
    double bw_hz;
} DemodeConfig_t;

// --- Desired User Config (from JSON) ---
typedef struct {
    rf_mode_t rf_mode;
    DemodeConfig_t demode_config;
    uint64_t center_freq;
    double sample_rate;
    double span;
    int lna_gain;
    int vga_gain;
    bool amp_enabled;
    int antenna_port;      
    
    // PSD Processing Config
    int rbw;
    double overlap;
    PsdWindowType_t window_type;
    char *scale;    // Will be stored in lowercase
    int ppm_error;
} DesiredCfg_t;

// --- Buffer Configuration ---
typedef struct {
    size_t total_bytes;
    int rb_size;    
} RB_cfg_t;

#endif