
#ifndef BACN_GPIO_H
#define BACN_GPIO_H

#include <stdbool.h>
#include <stdint.h>

#define PWR_MODULE 4
#define RST_MODULE 27
#define ANTENNA_SEL1 23
#define ANTENNA_SEL2 22
#define ANTENNA_SEL3 10
#define ANTENNA_SEL4 24
#define STATUS 18
#define RF1 1
#define RF2 0

uint8_t status_LTE(void);

uint8_t power_ON_LTE(void);
uint8_t power_OFF_LTE(void);
uint8_t reset_LTE(void);

uint8_t select_ANTENNA(uint8_t ANTENNA);

uint8_t switch_ANTENNA1(bool RF);
uint8_t switch_ANTENNA2(bool RF);
uint8_t switch_ANTENNA3(bool RF);
uint8_t switch_ANTENNA4(bool RF);

uint8_t real_time(void);

#endif // BACN_GPIO_H