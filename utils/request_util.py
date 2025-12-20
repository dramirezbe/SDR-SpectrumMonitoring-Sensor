#utils/request_util.py

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
    type: str
    bw_hz: int
    order: int

@dataclass
class DemodulationConfig:
    type: str
    bw_hz: int

@dataclass
class ServerRealtimeConfig:
    rf_mode: str
    method_psd: str
    center_freq_hz: int
    sample_rate_hz: int
    rbw_hz: int
    window: str
    scale: str
    overlap: float
    lna_gain: int
    vga_gain: int
    antenna_amp: bool
    antenna_port: int
    span: int
    ppm_error: int
    demodulation: Optional[DemodulationConfig] = None
    filter: Optional[FilterConfig] = None

    def __post_init__(self):
        # 1. Standard validations
        if not (1_000_000 <= self.center_freq_hz <= 6_000_000_000):
            raise ValueError(f"Center frequency {self.center_freq_hz} Hz out of range (1MHz - 6GHz).")
        
        if self.antenna_port not in [1, 2, 3, 4]:
            raise ValueError(f"Antenna port {self.antenna_port} is invalid. Must be 1-4.")
        
        if self.rf_mode not in ["campaign", "realtime", "fm", "am"]:
            raise ValueError(f"RF mode {self.rf_mode} is invalid. Must be campaign, realtime, fm, or am.")
        
        if self.method_psd not in ["pfb", "welch"]:
            raise ValueError(f"PSD method {self.method_psd} is invalid. Must be pfb or welch.")

        # 2. Nested Validation (only if filter is provided)
        if self.filter is not None:
            if self.filter.type not in ["lowpass", "highpass", "bandpass"]:
                raise ValueError(f"Filter type {self.filter.type} is invalid.")

        if self.demodulation is not None:
            if self.demodulation.type not in ["am", "fm"]:
                raise ValueError(f"Demodulation type {self.demodulation.type} is invalid.")
            
        

        

class RequestClient:
    """
    Lightweight HTTP client with unified return codes and internal logging.

    Return codes:
        0 -> success (HTTP 2xx)
        1 -> known network/server/client error
        2 -> unexpected error
    """

    def __init__(
        self,
        base_url: str,
        mac_wifi: str = "",
        timeout: Tuple[float, float] = (5, 15),
        verbose: bool = False,
        logger=None,
    ):
        # Removed api_key argument
        self.base_url = base_url.rstrip("/")
        self.mac_wifi = mac_wifi
        self.timeout = timeout
        self.verbose = verbose
        self._log = logger

    # -------------------------------------------------------------------------
    # Public methods
    # -------------------------------------------------------------------------
    def get(
        self,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> Tuple[int, Optional[requests.Response]]:
        # Add default Accept header for GET
        hdrs = {"Accept": "application/json"}
        if self._is_valid_mac():
            endpoint = f"/{self.mac_wifi}{endpoint}"
        else:
            if self._log:
                self._log.warning(f"Invalid MAC address {self.mac_wifi}")
        if headers:
            hdrs.update(headers)
        return self._send_request("GET", endpoint, headers=hdrs, params=params)

    def post_json(
        self,
        endpoint: str,
        json_dict: Dict[str, Any],
        headers: Optional[Dict[str, str]] = None,
    ) -> Tuple[int, Optional[requests.Response]]:
        try:
            body = json.dumps(json_dict).encode("utf-8")
        except Exception as e:
            if self._log:
                self._log.error(f"[HTTP] JSON serialization error: {e}")
            return 2, None

        # Add default Content-Type header for POST_JSON
        hdrs = {"Content-Type": "application/json"}
        if headers:
            hdrs.update(headers)
        return self._send_request("POST", endpoint, headers=hdrs, data=body)

    # -------------------------------------------------------------------------
    # Internal unified handler
    # -------------------------------------------------------------------------
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
                # Log URL and method
                self._log.info(f"[HTTP] {method} → {url}")
                # Optional: Log headers
                # self._log.info(f"[HTTP] Headers: {headers}")

            resp = requests.request(
                method,
                url,
                headers=headers, 
                data=data,
                params=params,
                timeout=self.timeout,
            )

            # Success
            if 200 <= resp.status_code < 300:
                if self.verbose and self._log:
                    self._log.info(f"[HTTP] success rc={resp.status_code}")
                return 0, resp

            # Known HTTP errors
            if 300 <= resp.status_code < 400:
                msg = f"[HTTP] redirect rc={resp.status_code}"
            elif 400 <= resp.status_code < 500:
                msg = f"[HTTP] client error rc={resp.status_code}"
            elif 500 <= resp.status_code < 600:
                msg = f"[HTTP] server error rc={resp.status_code}"
            else:
                msg = f"[HTTP] unknown status rc={resp.status_code}"

            if self._log:
                self._log.error(msg)
            return 1, resp

        except requests.exceptions.Timeout:
            if self._log:
                self._log.error("[HTTP] timeout")
            return 1, None

        except requests.exceptions.ConnectionError as e:
            if self._log:
                self._log.error(f"[HTTP] connection error: {e}")
            return 1, None

        except requests.exceptions.RequestException as e:
            if self._log:
                self._log.error(f"[HTTP] request exception: {e}")
            return 1, None

        except Exception as e:
            if self._log:
                self._log.error(f"[HTTP] unexpected error: {e}")
            return 2, None
        
    def _is_valid_mac(self):
        pattern = r"^[0-9a-fA-F]{2}(:[0-9a-fA-F]{2}){5}$"
        
        if re.match(pattern, self.mac_wifi):
            return True
        return False

class ZmqPairController:
    def __init__(self, addr, is_server=True, verbose=False):
        self.addr = addr
        self.is_server = is_server
        self.verbose = verbose
        self.context = None
        self.socket = None

    def start(self):
        """Initializes the context and binds/connects the socket."""
        if self.socket is not None:
            print("[PY] Socket already open.")
            return

        self.context = zmq.asyncio.Context()
        self.socket = self.context.socket(zmq.PAIR)
        
        # Set LINGER to 0 to ensure the socket closes immediately 
        # without waiting for pending messages.
        self.socket.setsockopt(zmq.LINGER, 0)

        if self.is_server:
            # IPC Cleanup: Remove the file if it already exists
            if self.addr.startswith("ipc://"):
                path = self.addr.replace("ipc://", "")
                if os.path.exists(path):
                    try:
                        os.remove(path)
                    except OSError as e:
                        print(f"[PY] Error removing IPC file: {e}")
            
            try:
                self.socket.bind(self.addr)
            except zmq.ZMQError as e:
                print(f"[PY] Failed to bind: {e}")
                raise
        else:
            self.socket.connect(self.addr)
            
        if self.verbose:
            print(f"[PY] ZMQ Started on {self.addr}")

    def close(self):
        """Closes the socket and terminates the context cleanly."""
        if self.socket:
            if self.verbose:
                print("[PY] Closing socket...")
            self.socket.close()
            self.socket = None
        
        if self.context:
            if self.verbose:
                print("[PY] Terminating context...")
            self.context.term()
            self.context = None

        # Optional: Explicitly remove IPC file on close (for Server)
        # This is polite, but the 'start' method already handles cleanup for the next run.
        if self.is_server and self.addr.startswith("ipc://"):
            path = self.addr.replace("ipc://", "")
            if os.path.exists(path):
                try:
                    os.remove(path)
                except OSError:
                    pass

    async def send_command(self, payload: dict):
        if not self.socket:
            raise RuntimeError("Socket is not open. Call start() first.")
        msg = json.dumps(payload)
        await self.socket.send_string(msg)
        if self.verbose:
            print(f"[PY] >> Sent CMD")

    async def wait_for_data(self):
        if not self.socket:
            raise RuntimeError("Socket is not open. Call start() first.")
        msg = await self.socket.recv_string()
        if self.verbose:
            print(f"[PY] << Received Payload")
        return json.loads(msg)

    # --- Context Manager Support (Recommended) ---
    async def __aenter__(self):
        self.start()
        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        self.close()