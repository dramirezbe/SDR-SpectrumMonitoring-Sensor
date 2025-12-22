// libs/psd.h
#ifndef PSD_H
#define PSD_H

#include "datatypes.h"
#include "parser.h"
#include "sdr_HAL.h"
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include <fftw3.h>
#include <alloca.h>
#include <complex.h>
#include <ctype.h>
#include <stdio.h>
#include <limits.h>

// Constants for final dBm scaling
#define IMPEDANCE_50_OHM 50.0
#define POWER_FLOOR_WATTS 1.0e-20

signal_iq_t* load_iq_from_buffer(const int8_t* buffer, size_t buffer_size);
void iq_compensation(signal_iq_t* signal_data);
void free_signal_iq(signal_iq_t* signal);

double get_window_enbw_factor(PsdWindowType_t type); 

/**
 * @brief Calculates derived parameters (FFT size, overlap) based on desired RBW.
 */
int find_params_psd(DesiredCfg_t desired, SDR_cfg_t *hack_cfg, PsdConfig_t *psd_cfg, RB_cfg_t *rb_cfg);

void execute_pfb_psd(signal_iq_t* signal_data, const PsdConfig_t* config, double* f_out, double* p_out);
/**
 * @brief Executes Welch's method to estimate Power Spectral Density.
 * @param signal_data Complex input signal.
 * @param config Window and FFT configuration.
 * @param f_out Output array for Frequency bins (must be allocated by caller).
 * @param p_out Output array for Power values (must be allocated by caller).
 */
void execute_welch_psd(signal_iq_t* signal_data, const PsdConfig_t* config, double* f_out, double* p_out);

#endif