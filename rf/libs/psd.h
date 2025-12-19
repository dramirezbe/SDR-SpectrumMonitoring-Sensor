// libs/psd.h
#ifndef PSD_H
#define PSD_H

#include "datatypes.h"
#include "sdr_HAL.h"
#include <cjson/cJSON.h>
#include <inttypes.h>

// --- IQ Management ---
signal_iq_t* load_iq_from_buffer(const int8_t* buffer, size_t buffer_size);
void free_signal_iq(signal_iq_t* signal);

// --- PSD Computation ---

/**
 * @brief Executes Welch's method to estimate Power Spectral Density.
 * @param signal_data Complex input signal.
 * @param config Window and FFT configuration.
 * @param f_out Output array for Frequency bins (must be allocated by caller).
 * @param p_out Output array for Power values (must be allocated by caller).
 */
void execute_welch_psd(signal_iq_t* signal_data, const PsdConfig_t* config, double* f_out, double* p_out);

// --- Processing Helpers ---
double get_window_enbw_factor(PsdWindowType_t type); 

/**
 * @brief Scales the raw PSD power values to the desired unit.
 * @param psd Array of power values.
 * @param nperseg Number of items in array.
 * @param scale_str Target unit (e.g., "dbm", "dbuv"). Case-insensitive.
 * @return 0 on success, -1 on error.
 */
int scale_psd(double* psd, int nperseg, const char* scale_str);

// --- Configuration & Parsing ---

/**
 * @brief Parses a JSON string into a DesiredCfg_t struct.
 * Converts all string fields (mode, window, scale) to lowercase immediately.
 * @return 0 on success, -1 on failure.
 */
int parse_config_rf(const char *json_string, DesiredCfg_t *target);

/**
 * @brief Frees allocated strings inside DesiredCfg_t (specifically 'scale').
 */
void free_desired_psd(DesiredCfg_t *target);

/**
 * @brief Calculates derived parameters (FFT size, overlap) based on desired RBW.
 */
int find_params_psd(DesiredCfg_t desired, SDR_cfg_t *hack_cfg, PsdConfig_t *psd_cfg, RB_cfg_t *rb_cfg);

void print_config_summary(DesiredCfg_t *des, SDR_cfg_t *hw, PsdConfig_t *psd, RB_cfg_t *rb);


static double bessi0(double x);

static void generate_kaiser_proto(double* h, int len, double beta);

void execute_pfb_psd(signal_iq_t* signal_data, const PsdConfig_t* config, double* f_out, double* p_out);



#endif