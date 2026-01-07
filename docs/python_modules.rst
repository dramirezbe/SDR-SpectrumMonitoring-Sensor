Python Orchestration & Utils
============================

Este proyecto gestiona la orquestación de sensores, el streaming de audio WebRTC y la sincronización de datos con el servidor central.

Main Orchestrator
-----------------
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

Campaign Runner
---------------
.. automodule:: campaign_runner
   :members:
   :undoc-members:

.. note::
   Captura datos PSD y los sube a la API según la demanda de campañas.

Calibration & Sync
---------------------------
.. automodule:: kal_sync
   :members:

.. note::
   Usa la utilidad `kalibrate-hackrf` para realizar la calibración en frecuencia del hardware HackRF.

Device Status & Health
----------------------
.. automodule:: status
   :members:

.. note::
   Recopila telemetría crítica: sincronización NTP, uso de recursos (CPU/RAM) y tiempos de calibración.

Global System Config
--------------------
.. automodule:: cfg
   :members:

.. note::
   Maneja el logger global, la validación de la MAC del sensor y la carga de variables de entorno (.env).

Init System Script
------------------
.. automodule:: init_sys
   :members:

Retry Queue Manager
-------------------
.. automodule:: retry_queue
   :members:

General Purpose Functions
-------------------------
.. automodule:: functions
   :members:

Utilities Package
-----------------

.. automodule:: utils.io_util
   :members:

.. automodule:: utils.request_util
   :members:

.. automodule:: utils.status_util
   :members: