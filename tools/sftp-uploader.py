#!/usr/bin/env python3

import sys
import os
import asyncio
import json
import argparse
from argparse import RawTextHelpFormatter
from datetime import datetime
from pathlib import Path
from dataclasses import asdict
import numpy as np
import paramiko
import io

FIXED_ROOT = Path("/home/anepi/SDR-SpectrumMonitoring-Sensor").resolve()

os.chdir(FIXED_ROOT)

if str(FIXED_ROOT) not in sys.path:
    sys.path.insert(0, str(FIXED_ROOT))

print(f"Directorio de trabajo actual: {os.getcwd()}")

import cfg
from utils import FilterConfig, ServerRealtimeConfig, ZmqPairController
from functions import AcquireDual

log = cfg.set_logger()

class SimpleSFTP:
    def __init__(self):
        self.host = "PLACEHOLDER"  # Cambia esto por la IP o hostname real del servidor SFTP
        self.port = 1222
        self.user = "root"
        self.password = 'PLACEHOLDER' # Cambia esto por la contraseña real del usuario root en el servidor SFTP

        self.ssh = paramiko.SSHClient()
        self.ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        self.sftp = None

    def connect(self):
        log.debug(f"Iniciando conexión SSH a {self.host}:{self.port} con usuario '{self.user}'")
        self.ssh.connect(
            hostname=self.host,
            port=self.port,
            username=self.user,
            password=self.password,
            look_for_keys=False,
            allow_agent=False
        )
        self.sftp = self.ssh.open_sftp()
        log.debug("Conexión SSH y sesión SFTP establecidas.")

    def close(self):
        log.debug("Cerrando conexiones...")
        if self.sftp:
            self.sftp.close()
        self.ssh.close()

    def create_dir(self, remote_dir):
        self.ssh.exec_command(f'mkdir -p {remote_dir}')

    def upload_memory(self, data_string, remote_path):
        file_obj = io.BytesIO(data_string.encode('utf-8'))
        self.sftp.putfo(file_obj, remote_path)


class FileNaming:
    def __init__(self):
        pass

    def _detect_band(self, cf_hz: float) -> str:
        if 88e6 <= cf_hz <= 108e6: return "FM"
        elif 470e6 <= cf_hz <= 698e6: return "TDT"
        elif (824e6 <= cf_hz <= 894e6) or (1850e6 <= cf_hz <= 1990e6): return "2G"
        elif 20e6 <= cf_hz <= 6e9: return "SDR-WIDE"
        else: return "UNKNOWN"

    def _format_number(self, value: float) -> str:
        return f"{value:g}"

    def upload_data(self, payload: dict, runtime_config: dict, sftp_client: SimpleSFTP, remote_dir: str) -> str:
        cf_hz = runtime_config["center_freq_hz"]
        span_hz = runtime_config["sample_rate_hz"]
        rbw_hz = runtime_config["rbw_hz"]
        lna = runtime_config["lna_gain"]
        vga = runtime_config["vga_gain"]

        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S%f")[:-3]
        band = self._detect_band(cf_hz)
        cf_str = self._format_number(cf_hz / 1e6) + "M"
        span_str = self._format_number(span_hz / 1e6) + "M"
        rbw_str = self._format_number(rbw_hz / 1e3) + "k"

        filename = f"{timestamp}_{band}_{cf_str}_{span_str}_{rbw_str}_{lna}LNA_{vga}VGA.json"
        remote_path = f"{remote_dir}/{filename}"

        payload["acquisition_config"] = runtime_config

        if isinstance(payload["Pxx"], np.ndarray):
            payload["Pxx"] = payload["Pxx"].tolist()

        json_data = json.dumps(payload, ensure_ascii=False)
        sftp_client.upload_memory(json_data, remote_path)

        return remote_path


async def fetch_pxx_data(args):
    # Mapeo de puertos según la imagen
    puertos = {"FM": 1, "2G": 2, "UHF": 3}
    
    config_obj = ServerRealtimeConfig(
        method_psd="pfb",
        center_freq_hz=int(args.f),
        sample_rate_hz=int(args.s),
        rbw_hz=int(args.r),
        window="hamming",
        overlap=0.5,
        lna_gain=int(args.l),
        vga_gain=int(args.g),
        antenna_amp=args.a,
        antenna_port=puertos[args.p],
        ppm_error=0,
        cooldown_request=0.5,
        demodulation=None,
        filter=None
    )

    runtime_config = asdict(config_obj)

    sftp_client = SimpleSFTP()
    remote_dir = "/root/files-database-postprocesamiento"
    file_manager = FileNaming()
    controller = ZmqPairController(addr=cfg.IPC_ADDR, is_server=True, verbose=False)

    try:
        sftp_client.connect()
        sftp_client.create_dir(remote_dir)

        async with controller as zmq_ctrl:
            acquirer = AcquireDual(controller=zmq_ctrl, log=log)
            log.info(f"Conectado a ZMQ en {cfg.IPC_ADDR}")

            for i in range(args.n):
                try:
                    log.info(f"Tomando adquisición {i+1} de {args.n}...")
                    payload = await acquirer.get_corrected_data(runtime_config)

                    if payload and payload.get("Pxx"):
                        remote_saved_path = file_manager.upload_data(payload, runtime_config, sftp_client, remote_dir)
                        print(f"Subida {i+1} Exitosa -> Ruta remota: {remote_saved_path}")
                except Exception as exc:
                    log.error(f"Error en adquisición {i+1}: {exc}")

    except Exception as sftp_exc:
        log.error(f"Fallo SFTP: {sftp_exc}")

    finally:
        sftp_client.close()


if __name__ == "__main__":
    descripcion = "Adquisición de espectro SDR vía ZMQ y subida a RAM remota por SFTP."
    
    ejemplos = """
Ejemplos de uso válidos:
------------------------
  1. Banda FM (97.5 MHz), Span 20MHz, RBW 100kHz, 1 toma:
     ./venv/bin/python upload-to-server.py -f 97500000 -s 20000000 -r 100000 -l 8 -g 8 -p FM
     
  2. Banda 2G (850 MHz), Span 10MHz, RBW 50kHz, 5 tomas continuas:
     ./venv/bin/python upload-to-server.py -f 850000000 -s 10000000 -r 50000 -l 16 -g 8 -p 2G -n 5
     
  3. Banda UHF con Amplificador encendido (-a), 10 tomas:
     ./venv/bin/python upload-to-server.py -f 600000000 -s 5000000 -r 25000 -l 8 -g 8 -p UHF -a -n 10
    """
    
    parser = argparse.ArgumentParser(
        description=descripcion, 
        epilog=ejemplos,
        formatter_class=RawTextHelpFormatter # Permite usar saltos de línea en el texto
    )
    
    parser.add_argument("-f", type=float, required=True, help="Frecuencia central en Hz")
    parser.add_argument("-s", type=float, required=True, help="Sample rate / Span en Hz")
    parser.add_argument("-r", type=float, required=True, help="RBW en Hz")
    parser.add_argument("-l", type=int, required=True, help="Ganancia LNA en dB")
    parser.add_argument("-g", type=int, required=True, help="Ganancia VGA en dB")
    parser.add_argument("-a", action="store_true", help="Habilitar amplificador (opcional, solo pon -a)")
    parser.add_argument("-p", type=str, required=True, choices=["FM", "2G", "UHF"], help="Banda para seleccionar el puerto de antena")
    parser.add_argument("-n", type=int, default=1, help="Cantidad de ciclos a ejecutar (default: 1)")
    
    # Si el usuario ejecuta el script sin argumentos, le mostramos el menú de ayuda detallado
    if len(sys.argv) == 1:
        parser.print_help(sys.stderr)
        sys.exit(1)
        
    args = parser.parse_args()
    
    asyncio.run(fetch_pxx_data(args))