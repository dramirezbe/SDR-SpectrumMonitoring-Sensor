Python Orquestrador & Utilidades
================================

Este proyecto gestiona la orquestación de sensores, el streaming de audio WebRTC y la sincronización de datos con el servidor central.

Orquestrador Principal
----------------------
.. automodule:: orchestrator
   :members:
   :undoc-members:
   :show-inheritance:

WebRTC Gateway (Audio Streaming)
--------------------------------
.. automodule:: server_webrtc
   :members:
   :undoc-members:
   :show-inheritance:

.. note::
   Este módulo recibe audio Opus vía TCP (puerto 9000) y lo retransmite mediante GStreamer.

Adquisición de Campañas
-----------------------
.. automodule:: campaign_runner
   :members:
   :undoc-members:

.. note::
   Captura datos PSD y los sube a la API según la demanda de campañas.

Calibración en frecuencia
-------------------------
.. automodule:: kal_sync
   :members:

.. note::
   Usa la utilidad `kalibrate-hackrf` para realizar la calibración en frecuencia del hardware HackRF.

Estado de Sensor
----------------
.. automodule:: status
   :members:

.. note::
   Recopila telemetría crítica: sincronización NTP, uso de recursos (CPU/RAM) y tiempos de calibración.

Configuración global y Logging
------------------------------
.. automodule:: cfg
   :members:

.. note::
   Maneja el logger global, la validación de la MAC del sensor y la carga de variables de entorno (.env).

Script de Instalación e Inicialización
--------------------------------------
.. automodule:: init_sys
   :members:

.. note::
   Instala las dependencias y realiza la configuración inicial del sistema.

Cola de Reintentos
------------------
.. automodule:: retry_queue
   :members:

Funciones Principales
---------------------
.. automodule:: functions
   :members:

Paquete de Utilidades
---------------------

.. automodule:: utils.io_util
   :members:

.. automodule:: utils.request_util
   :members:

.. automodule:: utils.status_util
   :members: