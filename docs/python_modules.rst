.. python_modules.rst

Python Orquestrador & Utilidades
================================

La capa de Python actúa como el cerebro del sensor, gestionando el ciclo de vida de las capturas, la salud del sistema y la comunicación externa.

Núcleo de Control
-----------------
Estos módulos gestionan la lógica principal y el flujo de trabajo de las mediciones de espectro.

.. rubric:: Orquestrador Principal
.. automodule:: orchestrator
   :members:
   :undoc-members:
   :show-inheritance:

.. rubric:: Ejecutor de Campañas
.. automodule:: campaign_runner
   :members:
   :undoc-members:

Streaming y Multimedia
----------------------
Gestión de audio de baja latencia para monitoreo remoto de señales demoduladas.

.. automodule:: server_webrtc
   :members:
   :undoc-members:

.. note::
   **Pipeline de Audio:** El flujo de datos sigue el camino: Captura SDR → Codificación Opus → Transporte TCP (puerto 9000) → GStreamer → Protocolo WebRTC para visualización en navegador.

Sincronización y Calibración
----------------------------
Módulos dedicados a garantizar que los datos recolectados sean precisos tanto en el dominio de la frecuencia como en el tiempo.

.. list-table:: Herramientas de Precisión
   :widths: 25 75
   :header-rows: 1

   * - Módulo
     - Descripción Funcional
   * - ``kal_sync``
     - Algoritmos de calibración de error en PPM (partes por millón) usando estaciones base GSM como referencia.
   * - ``status``
     - Monitoreo de telemetría: deriva de reloj NTP, uso de recursos y estado del hardware.

.. automodule:: kal_sync
   :members:

.. automodule:: status
   :members:

Infraestructura y Resiliencia
-----------------------------
Módulos encargados de la robustez del sistema, manejo de configuraciones y persistencia de datos ante fallos de red.

Gestión de Configuración y Logs
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
.. automodule:: cfg
   :members:
   :undoc-members:

Cola de Reintentos (Offline Storage)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
.. automodule:: retry_queue
   :members:
   :undoc-members:

Paquetes de Bajo Nivel (Utils)
------------------------------
Funciones de utilidad general para manipulación de archivos, peticiones HTTP y estados del sistema.

.. rubric:: Utilidades de Entrada/Salida
.. automodule:: utils.io_util
   :members:

.. rubric:: Cliente de API (Requests)
.. automodule:: utils.request_util
   :members:

.. rubric:: Utilidades de Estado
.. automodule:: utils.status_util
   :members: