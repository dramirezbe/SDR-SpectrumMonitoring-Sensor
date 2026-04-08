#!/usr/bin/env python3

import sys
import asyncio
from pathlib import Path
from dataclasses import asdict

# Asegurar que el directorio raíz está en el path (igual que en tu código original)
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import cfg
from utils import FilterConfig, ServerRealtimeConfig, ZmqPairController
from functions import AcquireDual

log = cfg.set_logger()

async def fetch_pxx_data():
    # 1. Configurar todos los parámetros hardcodeados (todo habilitado)
    # Filtro habilitado de 87.5 MHz a 107.5 MHz
    filter_cfg = FilterConfig(
        start_freq_hz=int(87.5 * 1e6), 
        end_freq_hz=int(107.5 * 1e6)
    )

    config_obj = ServerRealtimeConfig(
        method_psd="pfb",
        center_freq_hz=int(97.5 * 1e6), # CF: 97.5 MHz
        sample_rate_hz=int(20.0 * 1e6), # Span: 20 MHz
        rbw_hz=int(100 * 1e3),          # RBW: 100 kHz
        window="hann",                  # Ventana: Hann
        overlap=0.5,                    # Overlap: 50%
        lna_gain=40,                    # Máxima ganancia LNA (ej. 40 dB)
        vga_gain=62,                    # Máxima ganancia VGA (ej. 62 dB)
        antenna_amp=True,               # Amplificador de antena encendido
        antenna_port=1,                 # Puerto 1
        ppm_error=0,
        cooldown_request=0.1,
        demodulation="fm",              # Demodulación de FM habilitada
        filter=filter_cfg               # Se inyecta la configuración del filtro
    )
    
    # Convertir el dataclass a diccionario, que es lo que espera get_corrected_data()
    runtime_config = asdict(config_obj)

    # 2. Inicializar el controlador ZMQ y la clase de adquisición
    controller = ZmqPairController(addr=cfg.IPC_ADDR, is_server=True, verbose=False)
    
    async with controller as zmq_ctrl:
        acquirer = AcquireDual(controller=zmq_ctrl, log=log)
        log.info(f"Conectado a ZMQ en {cfg.IPC_ADDR}")

        # 3. Pedir el payload de datos al sistema RF
        try:
            log.info("Solicitando datos al sistema RF...")
            payload = await acquirer.get_corrected_data(runtime_config)
            
            # 4. Extraer exclusivamente la Pxx
            if payload and payload.get("Pxx"):
                pxx = payload["Pxx"]
                start_f = payload.get("start_freq_hz", 0)
                end_f = payload.get("end_freq_hz", 0)
                
                print("\n--- Adquisición Exitosa ---")
                print(f"Frecuencia Inicial : {start_f / 1e6} MHz")
                print(f"Frecuencia Final   : {end_f / 1e6} MHz")
                print(f"Tamaño de Pxx      : {len(pxx)} bins")
                print(f"Primeros 5 valores : {pxx[:5]}")
            else:
                log.warning("No se recibió Pxx en el payload o el payload está vacío.")
                
        except Exception as exc:
            log.error(f"Error al solicitar datos vía ZMQ: {exc}")

if __name__ == "__main__":
    # Ejecutar el loop asíncrono
    asyncio.run(fetch_pxx_data())