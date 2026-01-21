.. c_modules.rst

C Hardware & Módulos de Radio
=============================

Esta sección documenta los controladores de bajo nivel desarrollados en **C99**. Estos módulos constituyen la capa de abstracción de hardware (HAL) necesaria para la interacción directa con los periféricos críticos del sensor.

Arquitectura de Integración
---------------------------
La comunicación entre el procesador host y los componentes de radio se realiza mediante una arquitectura de bus de alto rendimiento, optimizada para minimizar la latencia en la captura de muestras.

* **Bus SPI:** Utilizado para el streaming de tramas binarias del receptor GPS.
* **GPIO Dedicados:** Control de líneas de interrupción, reset de hardware y gestión de estados del transceptor.
* **Flujo Binario:** Se implementa un protocolo de tramas para maximizar el rendimiento del bus.

Controlador de GPS (Binario)
----------------------------
Gestión del módulo GPS para la obtención de coordenadas geográficas y, fundamentalmente, la señal de tiempo de precisión para la sincronización de capturas.

.. doxygengroup:: gps_binary
   :content-only:

.. important::
   El módulo GPS requiere una antena externa con vista clara al cielo. La precisión de la marca de tiempo PPS es crítica para la integridad de los datos espectrales.

Módulo de Radio (RF)
--------------------
Controlador principal para el hardware **HackRF One** y la lógica de procesamiento DSP (Digital Signal Processing). Este módulo se encarga de la sintonización, ganancia y transferencia de muestras de IQ.

.. doxygengroup:: rf_binary
   :project: spectrum_sensor
   :members:
   :protected-members:
   :private-members:
   :undoc-members:
   :inner:

.. note::
   Este módulo incluye funciones internas de gestión de buffers que son vitales para evitar el desbordamiento de datos durante barridos de frecuencia rápidos.

Control de GPIO (BACN)
----------------------
Definiciones de mapeo de pines y funciones de control lógico para el hardware específico de la plataforma BACN.

.. doxygenfile:: bacn_gpio.h
   :project: spectrum_sensor

.. tip::
   Consulte este módulo si necesita modificar la asignación de pines para una nueva revisión de la PCB o realizar depuración de señales lógicas.