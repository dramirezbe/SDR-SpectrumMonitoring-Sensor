#!/usr/bin/env python3
import sys
from pathlib import Path
from dataclasses import asdict
import time
import asyncio
import threading
import os
import argparse

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TEST_ROOT = (Path(__file__).resolve().parent / ".." / "test").resolve()
if str(PROJECT_ROOT) not in sys.path: sys.path.insert(0, str(PROJECT_ROOT))
if str(TEST_ROOT) not in sys.path: sys.path.insert(0, str(TEST_ROOT))

import cfg
log = cfg.set_logger()
from test_modules.benchmark import BPFBenchmark
from utils import FilterConfig, ServerRealtimeConfig, ZmqPairController
from functions import AcquireDual

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
                #pxx = payload["Pxx"]
                #start_f = payload.get("start_freq_hz", 0)
                #end_f = payload.get("end_freq_hz", 0)
                
                log.info("\n--- Adquisición Exitosa ---")
                #print(f"Frecuencia Inicial : {start_f / 1e6} MHz")
                #print(f"Frecuencia Final   : {end_f / 1e6} MHz")
                #print(f"Tamaño de Pxx      : {len(pxx)} bins")
                #print(f"Primeros 5 valores : {pxx[:5]}")
            else:
                log.warning("No se recibió Pxx en el payload o el payload está vacío.")
                return 1
                
        except Exception as exc:
            log.error(f"Error al solicitar datos vía ZMQ: {exc}")
            return 1
        
def bench_function_sdr(duration_experiment=60) -> int:
    start_experiment = time.perf_counter()
    log.info(f"Starting SDR experiment for {duration_experiment}s ...")
    while time.perf_counter() - start_experiment < duration_experiment:
        rc = asyncio.run(_acquire_sdr_maxpower())
        if rc == 1:
            log.error("Error during SDR acquisition.")
            return 1
            
        time.sleep(0.01)

    return 0


DEFAULT_BINARY_ROUTE = str(cfg.PROJECT_ROOT / "rf_app")
DEFAULT_FUNCTION_NAME = "execute_pfb_psd"


def _build_parser() -> argparse.ArgumentParser:
    """Build CLI argument parser."""
    parser = argparse.ArgumentParser(
        description="BPF benchmark for RF function execution."
    )
    parser.add_argument(
        "-b",
        "--binary",
        dest="binary_route",
        default=DEFAULT_BINARY_ROUTE,
        help="Ruta relativa o absoluta al binario objetivo (default: rf_app).",
    )
    parser.add_argument(
        "-f",
        "--function",
        dest="function_name",
        default=DEFAULT_FUNCTION_NAME,
        help="Nombre de la función símbolo a trazar.",
    )
    parser.add_argument(
        "-p",
        "--pid",
        dest="target_pid",
        type=int,
        default=int(os.getenv("RF_APP_PID", "-1")),
        help="PID objetivo (default: RF_APP_PID env or -1 para ALL).",
    )
    parser.add_argument(
        "-r",
        "--output-csv",
        dest="output_csv",
        default="benchmarks/bpftrace_execute_pfb_psd.csv",
        help="Ruta del CSV de salida para eventos BPF.",
    )
    parser.add_argument(
        "--duration",
        dest="duration_experiment",
        type=int,
        default=60,
        help="Duración del experimento SDR en segundos (default: 60).",
    )
    return parser


if __name__ == "__main__":
    args = _build_parser().parse_args()

    # Resolve binary path: if relative, resolve from PROJECT_ROOT
    binary_route = args.binary_route
    if not os.path.isabs(binary_route):
        binary_route = str((cfg.PROJECT_ROOT / binary_route).resolve())
    else:
        binary_route = str(Path(binary_route).resolve())

    benchmark = BPFBenchmark(
        binary_route=binary_route,
        function_name=args.function_name,
        log=log,
        target_pid=args.target_pid,
        output_csv=args.output_csv,
    )
    benchmark.init()

    workload_thread = threading.Thread(
        target=bench_function_sdr,
        kwargs={"duration_experiment": args.duration_experiment},
        daemon=True,
    )
    workload_thread.start()

    try:
        while workload_thread.is_alive():
            benchmark.b.perf_buffer_poll(timeout=100)
    except KeyboardInterrupt:
        pass
    finally:
        benchmark._log.info("Exiting BPF benchmark.")
