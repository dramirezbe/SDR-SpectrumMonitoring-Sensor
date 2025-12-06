/**
 * @file Modules/datatypes.h
 * @brief Shared types for PSD and Signal processing
 */

#ifndef DATATYPES_H
#define DATATYPES_H

#include <complex.h>
#include <stddef.h>
#include <stdint.h>
#include <stdbool.h>

typedef struct {
    double complex* signal_iq;
    size_t n_signal;
}signal_iq_t;

typedef enum {
    HAMMING_TYPE,
    HANN_TYPE,
    RECTANGULAR_TYPE,
    BLACKMAN_TYPE,
    FLAT_TOP_TYPE,
    KAISER_TYPE,
    TUKEY_TYPE,
    BARTLETT_TYPE
}PsdWindowType_t;

typedef struct {
    PsdWindowType_t window_type;
    double sample_rate;
    int nperseg;
    int noverlap;
}PsdConfig_t;


typedef struct {
    double sample_rate;
    uint64_t center_freq;
    bool amp_enabled;
    int lna_gain;
    int vga_gain;
    double overlap;
    int ppm_error;
    PsdWindowType_t window_type;
    double span;
    int rbw;
    char *scale;
}DesiredCfg_t;

typedef struct {
    size_t total_bytes;
    int rb_size;    
}RB_cfg_t;

#endif