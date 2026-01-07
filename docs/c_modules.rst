C Hardware & Módulos de Radio
=============================

Este módulo contiene los controladores de bajo nivel desarrollados en C para la interacción 
directa con el hardware de radio y el receptor GPS.

Arquitectura de Integración
---------------------------
La comunicación se realiza mediante el bus SPI y GPIOs dedicados. El flujo de datos 
sigue el estándar de tramas binarias para optimizar el rendimiento.

.. doxygengroup:: gps_binary
   :content-only:

.. note::
   El módulo GPS requiere que el dispositivo esté en un lugar al aire libre.

Módulo de Radio (RF)
--------------------
Gestión del hardware HackRF One con el procesamiento DSP

.. doxygengroup:: rf_binary
   :content-only:

Control de GPIO
---------------
Definiciones de pines y funciones de control para el hardware BACN.

.. doxygenfile:: bacn_gpio.h