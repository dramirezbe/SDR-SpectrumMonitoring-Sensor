#ifndef CHAN_FILTER_H
#define CHAN_FILTER_H

#include "datatypes.h"
#include <stddef.h>
#include <stdint.h>

/**
 * Validates if the filter range is within the physical capture range (fc +/- fs/2).
 */
int chan_filter_validate_cfg_abs(
    const filter_t *cfg,
    uint64_t fc_hz,
    double fs_hz,
    char *err,
    size_t err_sz
);

/**
 * Applies a 2-stage frequency domain filter in-place.
 * Stage 1: Peak flattening (anti-blooming) outside the band.
 * Stage 2: Rejection mask with Raised Cosine transitions.
 */
int chan_filter_apply_inplace_abs(
    signal_iq_t *sig,
    const filter_t *cfg,
    uint64_t fc_hz,
    double fs_hz
);

// Returns a string indicating if the band is POSITIVE, NEGATIVE, or CROSS_DC
const char* chan_filter_last_region(void);

// Releases internal FFTW plans and memory
void chan_filter_free_cache(void);

#endif