/**
 * @file bacn_gpio.c
 * @brief Interfaz de control GPIO para el módulo LTE y selección de antenas.
 */

#include "bacn_gpio.h"

/**
 * @addtogroup gpio_module
 * @{
 */

/**
 * @brief Solicita y configura una línea de GPIO como salida.
 * * @param chip_path Ruta del dispositivo de chip GPIO (ej. "/dev/gpiochip0").
 * @param offset Número del pin dentro del chip.
 * @param value Valor inicial de la salida (activo/inactivo).
 * @param consumer Etiqueta para identificar quién usa la línea en el sistema.
 * @return struct gpiod_line_request* Puntero al objeto de solicitud, NULL si falla.
 */
static struct gpiod_line_request *
request_output_line(const char *chip_path, unsigned int offset,
		    enum gpiod_line_value value, const char *consumer)
{
	struct gpiod_request_config *req_cfg = NULL;
	struct gpiod_line_request *request = NULL;
	struct gpiod_line_settings *settings;
	struct gpiod_line_config *line_cfg;
	struct gpiod_chip *chip;
	int ret;

	chip = gpiod_chip_open(chip_path);
	if (!chip)
		return NULL;

	settings = gpiod_line_settings_new();
	if (!settings)
		goto close_chip;

	gpiod_line_settings_set_direction(settings,
					  GPIOD_LINE_DIRECTION_OUTPUT);
	gpiod_line_settings_set_output_value(settings, value);

	line_cfg = gpiod_line_config_new();
	if (!line_cfg)
		goto free_settings;

	ret = gpiod_line_config_add_line_settings(line_cfg, &offset, 1,
						  settings);
	if (ret)
		goto free_line_config;

	if (consumer) {
		req_cfg = gpiod_request_config_new();
		if (!req_cfg)
			goto free_line_config;

		gpiod_request_config_set_consumer(req_cfg, consumer);
	}

	request = gpiod_chip_request_lines(chip, req_cfg, line_cfg);

	gpiod_request_config_free(req_cfg);

free_line_config:
	gpiod_line_config_free(line_cfg);

free_settings:
	gpiod_line_settings_free(settings);

close_chip:
	gpiod_chip_close(chip);

	return request;
}

/**
 * @brief Solicita y configura una línea de GPIO como entrada.
 * * @param chip_path Ruta del dispositivo de chip GPIO.
 * @param offset Número del pin dentro del chip.
 * @param consumer Etiqueta para identificar el consumidor.
 * @return struct gpiod_line_request* Puntero al objeto de solicitud, NULL si falla.
 */
static struct gpiod_line_request *request_input_line(const char *chip_path,
						     unsigned int offset,
						     const char *consumer)
{
	struct gpiod_request_config *req_cfg = NULL;
	struct gpiod_line_request *request = NULL;
	struct gpiod_line_settings *settings;
	struct gpiod_line_config *line_cfg;
	struct gpiod_chip *chip;
	int ret;

	chip = gpiod_chip_open(chip_path);
	if (!chip)
		return NULL;

	settings = gpiod_line_settings_new();
	if (!settings)
		goto close_chip;

	gpiod_line_settings_set_direction(settings, GPIOD_LINE_DIRECTION_INPUT);
	gpiod_line_settings_set_edge_detection(settings, GPIOD_LINE_EDGE_BOTH);
	gpiod_line_settings_set_bias(settings, GPIOD_LINE_BIAS_DISABLED);

	line_cfg = gpiod_line_config_new();
	if (!line_cfg)
		goto free_settings;

	ret = gpiod_line_config_add_line_settings(line_cfg, &offset, 1,
						  settings);
	if (ret)
		goto free_line_config;

	if (consumer) {
		req_cfg = gpiod_request_config_new();
		if (!req_cfg)
			goto free_line_config;

		gpiod_request_config_set_consumer(req_cfg, consumer);
	}

	request = gpiod_chip_request_lines(chip, req_cfg, line_cfg);

	gpiod_request_config_free(req_cfg);

free_line_config:
	gpiod_line_config_free(line_cfg);

free_settings:
	gpiod_line_settings_free(settings);

close_chip:
	gpiod_chip_close(chip);

	return request;
}

uint8_t status_LTE(void)
{
	static const char *const chip_path = "/dev/gpiochip0";
	static const unsigned int line_offset = STATUS;

	struct gpiod_line_request *request;
	enum gpiod_line_value value;
	int ret;

	request = request_input_line(chip_path, line_offset,
				     "status-LTE");
	if (!request) {
		fprintf(stderr, "failed to request line: %s\n",
			strerror(errno));
		return EXIT_FAILURE;
	}

	value = gpiod_line_request_get_value(request, line_offset);

	/* not strictly required here, but if the app wasn't exiting... */
	gpiod_line_request_release(request);

	return value;
}

uint8_t power_ON_LTE(void)
{
	static const char *const chip_path = "/dev/gpiochip0";
	static const unsigned int line_offset = PWR_MODULE;

	enum gpiod_line_value value = GPIOD_LINE_VALUE_ACTIVE;
	struct gpiod_line_request *request;

	request = request_output_line(chip_path, line_offset, value,
				      "power-LTE");
	if (!request) {
		fprintf(stderr, "failed to request line: %s\n",
			strerror(errno));
		return EXIT_FAILURE;
	}
	
	printf("Turn on LTE\n", line_offset);		
	gpiod_line_request_set_value(request, line_offset, GPIOD_LINE_VALUE_ACTIVE);
	usleep(500000);
	gpiod_line_request_set_value(request, line_offset, GPIOD_LINE_VALUE_INACTIVE);

	gpiod_line_request_release(request);

	return EXIT_SUCCESS;
}

uint8_t power_OFF_LTE(void)
{
	static const char *const chip_path = "/dev/gpiochip0";
	static const unsigned int line_offset = PWR_MODULE;

	enum gpiod_line_value value = GPIOD_LINE_VALUE_ACTIVE;
	struct gpiod_line_request *request;

	request = request_output_line(chip_path, line_offset, value,
				      "power-LTE");
	if (!request) {
		fprintf(stderr, "failed to request line: %s\n",
			strerror(errno));
		return EXIT_FAILURE;
	}
	
	printf("Turn off LTE\n");		
	gpiod_line_request_set_value(request, line_offset, GPIOD_LINE_VALUE_ACTIVE);
	sleep(2);
	gpiod_line_request_set_value(request, line_offset, GPIOD_LINE_VALUE_INACTIVE);

	gpiod_line_request_release(request);

	return EXIT_SUCCESS;
}

uint8_t reset_LTE(void) 
{
	static const char *const chip_path = "/dev/gpiochip0";
	static const unsigned int line_offset = RST_MODULE;

	enum gpiod_line_value value = GPIOD_LINE_VALUE_ACTIVE;
	struct gpiod_line_request *request;

	request = request_output_line(chip_path, line_offset, value,
				      "reset-LTE");
	if (!request) {
		fprintf(stderr, "failed to request line: %s\n",
			strerror(errno));
		return EXIT_FAILURE;
	}
	
	printf("Reset LTE\n");		
	gpiod_line_request_set_value(request, line_offset, GPIOD_LINE_VALUE_ACTIVE);
	usleep(200000);
	gpiod_line_request_set_value(request, line_offset, GPIOD_LINE_VALUE_INACTIVE);

	gpiod_line_request_release(request);

	return EXIT_SUCCESS;
}

uint8_t select_ANTENNA(uint8_t ANTENNA)
{	
	switch (ANTENNA)
	{
		case 1:
			switch_ANTENNA2(false);
			switch_ANTENNA3(false);
			switch_ANTENNA4(false);
			switch_ANTENNA1(true);
		break;
		case 2:
			switch_ANTENNA1(false);
			switch_ANTENNA3(false);
			switch_ANTENNA4(false);
			switch_ANTENNA2(true);
		break;
		case 3:
			switch_ANTENNA1(false);
			switch_ANTENNA2(false);
			switch_ANTENNA4(false);
			switch_ANTENNA3(true);
		break;
		case 4:
			switch_ANTENNA1(false);
			switch_ANTENNA2(false);
			switch_ANTENNA3(false);
			switch_ANTENNA4(true);
		break;
		default:
		break;
	}
}

uint8_t switch_ANTENNA1(bool RF) 
{
	static const char *const chip_path = "/dev/gpiochip0";
	static const unsigned int line_offset = ANTENNA_SEL1;

	enum gpiod_line_value value = GPIOD_LINE_VALUE_ACTIVE;
	struct gpiod_line_request *request;

	request = request_output_line(chip_path, line_offset, value,
				      "switch-ANTENNA1");
	if (!request) {
		fprintf(stderr, "failed to request line: %s\n",
			strerror(errno));
		return EXIT_FAILURE;
	}
	
	if(RF) {
		gpiod_line_request_set_value(request, line_offset, GPIOD_LINE_VALUE_ACTIVE);
		printf("ANTENNA1 ON\n");
	} else { 
		gpiod_line_request_set_value(request, line_offset, GPIOD_LINE_VALUE_INACTIVE);
		printf("ANTENNA1 OFF\n");
	}
	
	gpiod_line_request_release(request);

	return EXIT_SUCCESS;
}

uint8_t switch_ANTENNA2(bool RF) 
{
	static const char *const chip_path = "/dev/gpiochip0";
	static const unsigned int line_offset = ANTENNA_SEL2;

	enum gpiod_line_value value = GPIOD_LINE_VALUE_ACTIVE;
	struct gpiod_line_request *request;

	request = request_output_line(chip_path, line_offset, value,
				      "switch-ANTENNA2");
	if (!request) {
		fprintf(stderr, "failed to request line: %s\n",
			strerror(errno));
		return EXIT_FAILURE;
	}
	
	if(RF) {
		gpiod_line_request_set_value(request, line_offset, GPIOD_LINE_VALUE_ACTIVE);
		printf("ANTENNA2 ON\n");
	} else { 
		gpiod_line_request_set_value(request, line_offset, GPIOD_LINE_VALUE_INACTIVE);
		printf("ANTENNA2 OFF\n");
	}
	
	gpiod_line_request_release(request);

	return EXIT_SUCCESS;
}

uint8_t switch_ANTENNA3(bool RF) 
{
	static const char *const chip_path = "/dev/gpiochip0";
	static const unsigned int line_offset = ANTENNA_SEL3;

	enum gpiod_line_value value = GPIOD_LINE_VALUE_ACTIVE;
	struct gpiod_line_request *request;

	request = request_output_line(chip_path, line_offset, value,
				      "switch-ANTENNA3");
	if (!request) {
		fprintf(stderr, "failed to request line: %s\n",
			strerror(errno));
		return EXIT_FAILURE;
	}
	
	if(RF) {
		gpiod_line_request_set_value(request, line_offset, GPIOD_LINE_VALUE_ACTIVE);
		printf("ANTENNA3 ON\n");
	} else { 
		gpiod_line_request_set_value(request, line_offset, GPIOD_LINE_VALUE_INACTIVE);
		printf("ANTENNA3 OFF\n");
	}
	
	gpiod_line_request_release(request);

	return EXIT_SUCCESS;
}

uint8_t switch_ANTENNA4(bool RF) 
{
	static const char *const chip_path = "/dev/gpiochip0";
	static const unsigned int line_offset = ANTENNA_SEL4;

	enum gpiod_line_value value = GPIOD_LINE_VALUE_ACTIVE;
	struct gpiod_line_request *request;

	request = request_output_line(chip_path, line_offset, value,
				      "switch-ANTENNA4");
	if (!request) {
		fprintf(stderr, "failed to request line: %s\n",
			strerror(errno));
		return EXIT_FAILURE;
	}
	
	if(RF) {
		gpiod_line_request_set_value(request, line_offset, GPIOD_LINE_VALUE_ACTIVE);
		printf("ANTENNA4 ON\n");
	} else { 
		gpiod_line_request_set_value(request, line_offset, GPIOD_LINE_VALUE_INACTIVE);
		printf("ANTENNA4 OFF\n");
	}
	
	gpiod_line_request_release(request);

	return EXIT_SUCCESS;
}

uint8_t real_time(void)
{
	static const char *const chip_path = "/dev/gpiochip0";
	static const unsigned int line_offset = 16;

	enum gpiod_line_value value = GPIOD_LINE_VALUE_ACTIVE;
	struct gpiod_line_request *request;

	request = request_output_line(chip_path, line_offset, value,
				      "realTime");
	if (!request) {
		fprintf(stderr, "failed to request line: %s\n",
			strerror(errno));
		return EXIT_FAILURE;
	}
	
	printf("Real Time test\n", line_offset);		
	gpiod_line_request_set_value(request, line_offset, GPIOD_LINE_VALUE_ACTIVE);
	gpiod_line_request_set_value(request, line_offset, GPIOD_LINE_VALUE_INACTIVE);

	gpiod_line_request_release(request);

	return EXIT_SUCCESS;
}

/** @} */