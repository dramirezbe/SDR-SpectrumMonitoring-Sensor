/**
 * @file bacn_gpio.h
 * @brief Interfaz de control GPIO para el módulo LTE y selección de antenas.
 * @author BACN, GCPDS
 * @date 2026
 */

#ifndef BACN_GPIO_H
#define BACN_GPIO_H

#include <stdbool.h>
#include <stdint.h>
#include <errno.h>
#include <gpiod.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <time.h>

/**
 * @defgroup gpio_module GPIO
 * @brief Interfaz de control GPIO para el módulo LTE y selección de antenas.
 * @{
 */

/**
 * @name Definición de Pines (Offsets)
 * @{
 */
#define PWR_MODULE 4      /**< Pin para control de encendido del módulo. */
#define RST_MODULE 27     /**< Pin para el reset físico del módulo. */
#define ANTENNA_SEL1 23   /**< Selector de RF para la Antena 1. */
#define ANTENNA_SEL2 22   /**< Selector de RF para la Antena 2. */
#define ANTENNA_SEL3 10   /**< Selector de RF para la Antena 3. */
#define ANTENNA_SEL4 24   /**< Selector de RF para la Antena 4. */
#define STATUS 18         /**< Pin de entrada para verificar estado del módulo. */
#define RF1 1             /**< Valor lógico para RF Activo. */
#define RF2 0             /**< Valor lógico para RF Inactivo. */
/** @} */

/**
 * @brief Obtiene el estado actual del módulo LTE.
 * @return uint8_t Valor leído del pin STATUS (0 o 1).
 */
uint8_t status_LTE(void);

/**
 * @brief Realiza la secuencia de encendido del módulo LTE.
 * @return uint8_t EXIT_SUCCESS si fue exitoso, EXIT_FAILURE en caso de error.
 */
uint8_t power_ON_LTE(void);

/**
 * @brief Realiza la secuencia de apagado controlado del módulo LTE.
 * @return uint8_t EXIT_SUCCESS si fue exitoso, EXIT_FAILURE en caso de error.
 */
uint8_t power_OFF_LTE(void);

/**
 * @brief Envía un pulso de reset al módulo LTE.
 * @return uint8_t EXIT_SUCCESS si fue exitoso, EXIT_FAILURE en caso de error.
 */
uint8_t reset_LTE(void);

/**
 * @brief Selecciona una antena específica desactivando las demás.
 * @param ANTENNA Número de antena a seleccionar (1-4).
 * @return uint8_t Estado de la operación.
 */
uint8_t select_ANTENNA(uint8_t ANTENNA);

/**
 * @brief Controla el switch de la Antena 1.
 * @param RF true para activar, false para desactivar.
 * @return uint8_t EXIT_SUCCESS o EXIT_FAILURE.
 */
uint8_t switch_ANTENNA1(bool RF);

/**
 * @brief Controla el switch de la Antena 2.
 * @param RF true para activar, false para desactivar.
 * @return uint8_t EXIT_SUCCESS o EXIT_FAILURE.
 */
uint8_t switch_ANTENNA2(bool RF);

/**
 * @brief Controla el switch de la Antena 3.
 * @param RF true para activar, false para desactivar.
 * @return uint8_t EXIT_SUCCESS o EXIT_FAILURE.
 */
uint8_t switch_ANTENNA3(bool RF);

/**
 * @brief Controla el switch de la Antena 4.
 * @param RF true para activar, false para desactivar.
 * @return uint8_t EXIT_SUCCESS o EXIT_FAILURE.
 */
uint8_t switch_ANTENNA4(bool RF);

/**
 * @brief Genera un pulso rápido en el pin 16 para pruebas de tiempo real.
 * @return uint8_t EXIT_SUCCESS o EXIT_FAILURE.
 */
uint8_t real_time(void);

/** @} */

#endif // BACN_GPIO_H