// libs/parser.h
#ifndef PARSER_H
#define PARSER_H

#include "datatypes.h"
#include "sdr_HAL.h"
#include <cjson/cJSON.h>
#include <inttypes.h>
#include <string.h>
#include <stdlib.h>
#include <ctype.h>
#include <stdio.h>
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

char* strdup_lowercase(const char *str);

void print_config_summary(DesiredCfg_t *des, SDR_cfg_t *hw, PsdConfig_t *psd, RB_cfg_t *rb);
void print_config_summary_DEBUG(DesiredCfg_t *des, SDR_cfg_t *hw, PsdConfig_t *psd, RB_cfg_t *rb);

#endif