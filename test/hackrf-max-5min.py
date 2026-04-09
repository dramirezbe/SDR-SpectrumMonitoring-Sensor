import time
import sys
from pathlib import Path
from dataclasses import asdict
import asyncio

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TEST_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(TEST_ROOT) not in sys.path:
    sys.path.insert(0, str(TEST_ROOT))

from test_modules.benckmark import BenchmarkCSV
import cfg
from utils import FilterConfig, ServerRealtimeConfig, ZmqPairController
from functions import AcquireDual

log = cfg.set_logger()

DEFAULT_INTERVAL_BENCH = 0.5

#Helpers

async def _acquire_sdr_lowpower():

    config_obj = ServerRealtimeConfig(
        method_psd="pfb",
        center_freq_hz=int(97.5 * 1e6), # CF: 97.5 MHz
        sample_rate_hz=int(8 * 1e6),
        rbw_hz=int(100 * 1e3),          # RBW: 100 kHz
        window="hamming",                  # Ventana: Hann
        overlap=0.5,                    # Overlap: 50%
        lna_gain=8,                    
        vga_gain=8,                    
        antenna_amp=False,               
        antenna_port=1,                 # Puerto 1
        ppm_error=0,
        cooldown_request=1,
        demodulation=None,              # Demodulación de FM habilitada
        filter=None               # Se inyecta la configuración del filtro
    )
    
    # Convertir el dataclass a diccionario, que es lo que espera get_corrected_data()
    runtime_config = asdict(config_obj)

    # 2. Inicializar el controlador ZMQ y la clase de adquisición
    controller = ZmqPairController(addr=cfg.IPC_ADDR, is_server=True, verbose=False)
    
    async with controller as zmq_ctrl:
        acquirer = AcquireDual(controller=zmq_ctrl, log=log)

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
                return 1
                
        except Exception as exc:
            log.error(f"Error al solicitar datos vía ZMQ: {exc}")
            return 1

async def _acquire_sdr_maxpower():

    # 1. Configurar todos los parámetros hardcodeados (todo habilitado)
    # Filtro habilitado de 87.5 MHz a 107.5 MHz
    filter_cfg = FilterConfig(
        start_freq_hz=int(90 * 1e6), 
        end_freq_hz=int(100 * 1e6)
    )

    config_obj = ServerRealtimeConfig(
        method_psd="pfb",
        center_freq_hz=int(98 * 1e6), # CF: 97.5 MHz
        sample_rate_hz=int(20.0 * 1e6), # Span: 20 MHz
        rbw_hz=int(1 * 1e3),          # RBW: 100 kHz
        window="hamming",                  # Ventana: Hann
        overlap=0.5,                    # Overlap: 50%
        lna_gain=40,                    # Máxima ganancia LNA (ej. 40 dB)
        vga_gain=62,                    # Máxima ganancia VGA (ej. 62 dB)
        antenna_amp=True,               # Amplificador de antena encendido
        antenna_port=1,                 # Puerto 1
        ppm_error=0,
        cooldown_request=0.001,
        demodulation=None, 
        filter=filter_cfg               # Se inyecta la configuración del filtro
    )
    
    # Convertir el dataclass a diccionario, que es lo que espera get_corrected_data()
    runtime_config = asdict(config_obj)

    # 2. Inicializar el controlador ZMQ y la clase de adquisición
    controller = ZmqPairController(addr=cfg.IPC_ADDR, is_server=True, verbose=False)
    
    async with controller as zmq_ctrl:
        acquirer = AcquireDual(controller=zmq_ctrl, log=log)

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
                return 1
                
        except Exception as exc:
            log.error(f"Error al solicitar datos vía ZMQ: {exc}")
            return 1

# Functions bench
def bench_idle(duration_experiment=15,  duration_bench=15) -> int:
    start_experiment = time.perf_counter()
    log.info(f"Starting idle experiment for {duration_experiment} seconds with benchmark duration of {duration_bench} seconds...")
    BenchmarkCSV().start("results", "idle.csv", duration=duration_bench, interval=DEFAULT_INTERVAL_BENCH)

    while time.perf_counter() - start_experiment < duration_experiment:
        pass

    return 0

def bench_just_sdr(duration_experiment=60,  duration_bench=60, max_power=False) -> int:
    start_experiment = time.perf_counter()
    log.info(f"Starting just SDR experiment for {duration_experiment}s ...")
    BenchmarkCSV().start("results", "just_sdr.csv", duration=duration_bench, interval=DEFAULT_INTERVAL_BENCH)

    while time.perf_counter() - start_experiment < duration_experiment:
        if max_power:
            rc = asyncio.run(_acquire_sdr_maxpower())
        else:
            rc = asyncio.run(_acquire_sdr_lowpower())
            
        if rc == 1:
            log.error("Error during SDR acquisition.")
            return 1
            
        time.sleep(0.01)

    return 0

if __name__ == "__main__":
    log.info("Iniciando bench de SDR max por 5 minutos...")
    
    # 300 segundos = 5 minutos
    bench_just_sdr(duration_experiment=300, duration_bench=300, max_power=True)
    
    log.info("Completado.")