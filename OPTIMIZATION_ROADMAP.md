# Plan de Acción y Optimización (SDR-SpectrumMonitoring-Sensor)

Este documento detalla el plan de optimización a nivel estructural y de código para los agentes y desarrolladores que operen en este repositorio. El objetivo principal es reducir al mínimo la carga de CPU y el uso de memoria en una Raspberry Pi 5, **sin alterar nunca los parámetros de configuración (ZMQ JSON) dictados por el usuario**.

Se actuará sobre las carpetas `rf/` y `gps-lte/` progresivamente de forma escalonada, validando función por función.

---

## 1. Fase Preliminar de Perfilado (Profiling)
Antes de modificar rutinas pesadas, se debe identificar el consumo actual de CPU y memoria.
* **Acción:** Correr la aplicación compilada con `./build.sh -dev` simulando diferentes tasas de muestreo (`sample_rate` = 20Mhz) y evaluar contenciones.
* **Punto focal:** Ciclos activos en theads de demodulación y polling en serial.

## 2. Optimización del Módulo de Radio (`rf/`)

El módulo `rf` es el de mayor carga de DSP y manejo continuo de datos en crudo (IQ). Las optimizaciones se centrarán en la aritmética y la gestión de memoria sin descartar datos.

### A. Gestión In-Place y Evitación de Asignaciones en Caliente
* **`rf/libs/psd.c` y Transformadas de Fourier:** 
  - Asegurar que los planes de FFTW (`fftw_plan`) se creen **una sola vez** en la inicialización y se reusen.
  - Asegurar que las ventanas (Hamming, Hanning, etc.) se pre-calculen al inicio o cuando el `nperseg` cambie, no en cada llamada al cálculo de Densidad Espectral.
* **Parseo JSON y Memoria (`rf/libs/parser.c`, `rf/rf.c`):**
  - Mitigar la creación constante en cascada de estructuras `cJSON` en el loop principal. Serializar a JSON de la forma más directa posible.
  - Los arrays dobles de transmisión de espectro deben allocarse estáticamente o una vez en el heap, y ser reutilizados en un patrón de doble buffer (Double Buffering) antes de enviarse vía ZMQ.

### B. Cálculo Vectorial Ligero y Casting
* **Tipos de Datos:** 
  - Realizar una auditoría de tipos (`double` vs `float`). Si bien la resolución FFT debe mantenerse intrínsecamente como el usuario pida, los pasos intermedios de rotación de fase y filtros en `rf/libs/chan_filter.c` y `rf/libs/iq_iir_filter.c` en procesadores ARM (NEON) rinden considerablemente más usando `float32` si el rango dinámico lo permite y la precisión del cálculo final en PSD no se altera drásticamente.
  - El usuario indicó no alterar las resoluciones geométricas, temporales o frecuenciales. Esto se respetará.

### C. Sistema de Buffers (`rf/libs/ring_buffer.c`)
* Verificar que la inserción de muestras (`rx_callback` ejecutado por `libhackrf`) sea **Lock-Free** o minimamente bloqueante. En arquitecturas de productor único y consumidor único, los _ring buffers_ se pueden manejar de forma atómica sin tener que usar `pthread_mutex_lock` en cada array insertado.

---

## 3. Optimización del Módulo Periférico (`gps-lte/`)

Este binario suele pecar de picos de CPU si existen bucles o lecturas bloqueantes no controlados en los puertos seriales.

### A. I/O Serial y Polling Eficiente
* **`gps-lte/gps-lte.c`:**
  - Evitar los `while(1)` o retardos duros (`sleep`/`usleep`) arbitrarios.
  - Reemplazar esperas activas por interfaces basadas en eventos o I/O bloqueante bien administrado, como `select()` o `poll()`.
  
### B. Manejo de Strings Reutilizables 
* **`gps-lte/libs/bacn_GPS.c` y `gps-lte/libs/bacn_LTE.c`:** 
  - Procesamiento de comandos AT y sentencias NMEA (GPGGA, GPRMC, etc.). 
  - Evitar excesos dinámicos en `sprintf` o duplicación de buffers (por ej. `strdup`) al parsear tramas de señal y coordenadas. 

---

## 4. Estrategia Operativa "Función por Función" (Regla 3)
Cualquier agente debe seguir el siguiente flujo de modificación:
1. **Identificar y Aislar:** Analizar una función clave del plan.
2. **Hacer el Cambio:** Introducir las mejoras de in-place/lock-free propuestas.
3. **Validación de la Interfaz:** Garantizar que los argumentos de entrada y la semántica de respuesta no se quiebren.
4. **Compile Test Local:** Compilar usando `./build.sh -dev` (sin `sudo`).
5. **Validación de Flujo Operativo:** Correr `sudo ./install-local.sh` y certificar la conectividad ZMQ o de sistema antes de avanzar.
