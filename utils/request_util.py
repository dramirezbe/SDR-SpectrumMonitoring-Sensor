# utils/request_util.py
"""
Utilidades de Comunicación (HTTP y ZMQ).

Este módulo centraliza las interacciones externas e internas del sensor. 
Incluye validadores de configuración de hardware, un cliente HTTP robusto con 
manejo de errores unificado y un controlador de sockets ZeroMQ para la 
comunicación entre procesos (IPC).
"""

import requests
from typing import Optional, Tuple, Dict, Any
import zmq
import zmq.asyncio
import json
import re
import os
from dataclasses import dataclass

@dataclass
class FilterConfig:
    """Configuración de filtrado digital para la señal de RF."""
    start_freq_hz: int #: Frecuencia de inicio en Hertz
    end_freq_hz: int #: Frecuencia de fin en Hertz

@dataclass
class ServerRealtimeConfig:
    """
    Configuración maestra de tiempo real enviada por el servidor.

    Realiza validaciones automáticas mediante `__post_init__` para asegurar 
    que los parámetros solicitados por la nube sean compatibles con el hardware.
    """
    method_psd: str
    center_freq_hz: int
    sample_rate_hz: int
    rbw_hz: int
    window: str
    overlap: float
    lna_gain: int
    vga_gain: int
    antenna_amp: bool
    antenna_port: int
    ppm_error: int
    demodulation: Optional[str] = None
    filter: Optional[FilterConfig] = None

    def __post_init__(self):
        """Valida las restricciones físicas del hardware SDR."""
        # Validación de rango de frecuencia (1MHz a 6GHz)
        if not (1_000_000 <= self.center_freq_hz <= 6_000_000_000):
            if self.center_freq_hz < 1_000_000:
                self.center_freq_hz = 1_000_000
            if self.center_freq_hz > 6_000_000_000:
                self.center_freq_hz = 6_000_000_000
            raise ValueError(f"Frecuencia central {self.center_freq_hz} Hz fuera de rango.")
        
        if not (2_000_000 <= self.sample_rate_hz <= 20_000_000):
            if self.center_freq_hz < 2_000_000:
                self.center_freq_hz = 2_000_000
            if self.center_freq_hz > 20_000_000_000:
                self.center_freq_hz = 20_000_000_000
            raise ValueError(f"Sample rate {self.sample_rate_hz} Hz fuera de rango.")
        
        # Validación de puertos de antena
        if self.antenna_port not in [1, 2, 3, 4]:
            self.antenna_port = 1 #Default
            raise ValueError(f"Puerto de antena {self.antenna_port} inválido.")
        
        # Validación de métodos de Densidad Espectral de Potencia (PSD)
        if self.method_psd not in ["pfb", "welch"]:
            self.method_psd = "pfb"
            raise ValueError(f"Método PSD {self.method_psd} inválido. Debe ser pfb o welch.")

        if self.filter is not None:
           if self.filter.start_freq_hz > self.filter.end_freq_hz:
               raise ValueError(f"La frecuencia de inicio debe ser menor que la de fin.")

        if self.demodulation is not None:
            if self.demodulation not in ["am", "fm"]:
                raise ValueError(f"Tipo de demodulación {self.demodulation} inválido.")

class RequestClient:
    """
    Cliente HTTP ligero con códigos de retorno unificados.

    Esta clase simplifica las peticiones `requests` inyectando automáticamente 
    la dirección MAC en los endpoints y capturando excepciones comunes de red.

    Códigos de Retorno (RC):
        * **0**: Éxito (Respuesta HTTP 2xx).
        * **1**: Error de red conocido (Timeout, Conexión, Error 4xx/5xx).
        * **2**: Error inesperado (Serialización, Excepciones críticas).
    """

    def __init__(
        self,
        base_url: str,
        mac_wifi: str = "",
        timeout: Tuple[float, float] = (5, 15),
        verbose: bool = False,
        logger=None,
    ):
        """
        Args:
            base_url (str): URL base de la API.
            mac_wifi (str): Dirección MAC del sensor para identificación.
            timeout (tuple): Tiempos de espera (conexión, lectura) en segundos.
            verbose (bool): Si es True, imprime detalles de las peticiones.
            logger: Instancia de logging para registro de eventos.
        """
        self.base_url = base_url.rstrip("/")
        self.mac_wifi = mac_wifi
        self.timeout = timeout
        self.verbose = verbose
        self._log = logger

    def get(
        self,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> Tuple[int, Optional[requests.Response]]:
        """Realiza una petición GET inyectando la MAC en la ruta."""
        hdrs = {"Accept": "application/json"}
        if self._is_valid_mac():
            endpoint = f"/{self.mac_wifi}{endpoint}"
        elif self._log:
            self._log.warning(f"Dirección MAC inválida: {self.mac_wifi}")
            
        if headers: hdrs.update(headers)
        return self._send_request("GET", endpoint, headers=hdrs, params=params)

    def post_json(
        self,
        endpoint: str,
        json_dict: Dict[str, Any],
        headers: Optional[Dict[str, str]] = None,
    ) -> Tuple[int, Optional[requests.Response]]:
        """Envía un diccionario JSON mediante una petición POST."""
        try:
            body = json.dumps(json_dict).encode("utf-8")
        except Exception as e:
            if self._log: self._log.error(f"[HTTP] Error de serialización: {e}")
            return 2, None

        hdrs = {"Content-Type": "application/json"}
        if headers: hdrs.update(headers)
        return self._send_request("POST", endpoint, headers=hdrs, data=body)

    def _send_request(
        self,
        method: str,
        endpoint: str,
        headers: Optional[Dict[str, str]] = None,
        data: Optional[bytes] = None,
        params: Optional[Dict[str, Any]] = None,
    ) -> Tuple[int, Optional[requests.Response]]:
        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        
        try:
            if self.verbose and self._log:
                self._log.info(f"[HTTP] {method} → {url}")

            resp = requests.request(
                method, url, headers=headers, data=data, 
                params=params, timeout=self.timeout
            )

            if 200 <= resp.status_code < 300:
                return 0, resp

            if self._log:
                self._log.error(f"[HTTP] Error rc={resp.status_code} en {url}")
            return 1, resp

        except requests.exceptions.ConnectionError as e:
            # Manejo simplificado de DNS / Name Resolution
            msg = str(e)
            if "NameResolutionError" in msg or "Temporary failure in name resolution" in msg:
                error_type = "Error de DNS (Host no encontrado)"
            else:
                error_type = "Error de conexión"
            
            if self._log:
                self._log.warning(f"[HTTP] {error_type} en {url}")
            return 1, None

        except requests.exceptions.Timeout:
            if self._log: self._log.warning(f"[HTTP] Timeout en {url}")
            return 1, None

        except Exception as e:
            if self._log: self._log.error(f"[HTTP] Error inesperado: {e}")
            return 2, None
        
    def _is_valid_mac(self) -> bool:
        """Valida el formato de la dirección MAC mediante regex."""
        pattern = r"^[0-9a-fA-F]{2}(:[0-9a-fA-F]{2}){5}$"
        return bool(re.match(pattern, self.mac_wifi))

class ZmqPairController:
    """
    Controlador asíncrono para sockets ZeroMQ del tipo PAIR.

    Se utiliza para la comunicación Inter-Procesos (IPC) entre este código 
    Python y el motor de procesamiento RF (C++/Rust/Python). Implementa 
    el protocolo de limpieza de archivos IPC para evitar bloqueos.
    """
    def __init__(self, addr: str, is_server: bool = True, verbose: bool = False):
        """
        Args:
            addr (str): Dirección del socket (ej: 'ipc:///tmp/rf_engine').
            is_server (bool): Si es True, realiza un 'bind', de lo contrario 'connect'.
            verbose (bool): Activa logs de depuración para mensajes enviados/recibidos.
        """
        self.addr = addr
        self.is_server = is_server
        self.verbose = verbose
        self.timeout_ms = 15000
        self.context = None
        self.socket = None

    def start(self):
        """Inicializa el contexto y prepara el socket."""
        if self.socket is not None: return

        self.context = zmq.asyncio.Context()
        self.socket = self.context.socket(zmq.PAIR)
        self.socket.setsockopt(zmq.LINGER, 0) # Cierre inmediato

        if self.is_server:
            if self.addr.startswith("ipc://"):
                path = self.addr.replace("ipc://", "")
                if os.path.exists(path):
                    try: os.remove(path)
                    except OSError: pass
            self.socket.bind(self.addr)
        else:
            self.socket.connect(self.addr)
            
        if self.verbose: print(f"[PY] ZMQ Iniciado en {self.addr}")

    def close(self):
        """Libera los recursos de ZeroMQ y limpia archivos temporales IPC."""
        if self.socket:
            self.socket.close()
            self.socket = None
        if self.context:
            self.context.term()
            self.context = None

        if self.is_server and self.addr.startswith("ipc://"):
            path = self.addr.replace("ipc://", "")
            if os.path.exists(path):
                try: os.remove(path)
                except OSError: pass

    async def send_command(self, payload: dict):
        """Envía un comando JSON de forma asíncrona."""
        if not self.socket: raise RuntimeError("Socket no iniciado.")
        await self.socket.send_string(json.dumps(payload))
        if self.verbose: print(f"[PY] >> Comando enviado")

    async def wait_for_data(self) -> dict:
        """Espera y recibe una respuesta con un timeout para evitar bloqueos."""
        if not self.socket: raise RuntimeError("Socket no iniciado.")
        
        # Use poller to wait with a timeout
        if await self.socket.poll(self.timeout_ms, zmq.POLLIN):
            msg = await self.socket.recv_string()
            if self.verbose: print(f"[PY] << Datos recibidos")
            return json.loads(msg)
        else:
            # Return None or raise error so run_realtime_logic can handle the silence
            if self.verbose: print(f"[PY] !! Timeout esperando datos de C")
            return None

    async def __aenter__(self):
        """Soporte para gestor de contexto asíncrono (`async with`)."""
        self.start()
        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        self.close()