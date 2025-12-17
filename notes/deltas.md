# KAL

Escanea bandas GSM y sincroniza sdr, devuelve ppm offset en frecuencia
Despues de una campaña se debe sincronizar.

# GPS

manda el gps con modulo MonRaF2 al servidor
10 seg
demonio

# STATUS

manda métricas del dispositivo, además de estado actual de los errores del dispositivo
30 seg
demonio

# ORCHESTRATOR

1 minuto demonio
mira /campaigns
actualiza cron.
Mete a cron campaign_runner
campaign runner espera -f1 -f2 -w(resolution_hz) -p(antenna_port) -wi(window) -o(overlap) -fs(sample_rate_hz) -l(lna_gain) -g(vga_gain) -a(antenna_amp)
cambiar current_mode.

# REALTIME

5 segundos
mira /realtime
empieza o termina realtime
cambia current_mode

# RETRY

Mira queue dir, empieza a subir archivos a la nube si hay archivos pendientes
5 min
demonio
solo si current_mode idle

# INIT_SYSTEM

al prender sistema
/init GET variables por defecto
Inicia demonios, crea carpetas, etc.
