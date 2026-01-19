/**
 * @file sdr_HAL.c
 * @brief Implementación de las funciones de control para el hardware SDR.
 */

#include "sdr_HAL.h"

/**
 * @brief Calcula y aplica la frecuencia corregida según el error de reloj.
 * La fórmula utilizada es: $f_{corregida} = f_{objetivo} \times (1 + \frac{PPM}{1,000,000})$
 * * @param dev Puntero al dispositivo.
 * @param target_freq Frecuencia deseada en Hz.
 * @param ppm_error Error de cristal en partes por millón.
 */
static void tune_freq_with_ppm(hackrf_device* dev, uint64_t target_freq, int ppm_error) {
    double correction = 1.0 + ((double)ppm_error / 1000000.0);
    uint64_t corrected_freq = (uint64_t)((double)target_freq * correction);
    
    printf("[HAL] Target: %lu Hz | PPM: %d | Tuning to: %lu Hz\n", 
           target_freq, ppm_error, corrected_freq);

    hackrf_set_freq(dev, corrected_freq);
}

void hackrf_apply_cfg(hackrf_device* dev, SDR_cfg_t *cfg) {
    if (!dev || !cfg) return;

    hackrf_set_amp_enable(dev, cfg->amp_enabled ? 1 : 0);
    hackrf_set_lna_gain(dev, cfg->lna_gain);
    hackrf_set_vga_gain(dev, cfg->vga_gain);
    hackrf_set_sample_rate(dev, cfg->sample_rate);
    hackrf_set_hw_sync_mode(dev, 0);
    tune_freq_with_ppm(dev, cfg->center_freq, cfg->ppm_error);
}