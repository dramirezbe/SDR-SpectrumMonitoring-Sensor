"""
@file utils/request_util.py
@brief Simple reusable HTTP client helper with rc codes and print-based logging.
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
class ServerRealtimeConfig:
    """
    Validates and holds the configuration for the C-Engine.
    """
    rf_mode: str = "realtime"
    center_freq_hz: int = 98_000_000
    sample_rate_hz: int = 20_000_000
    rbw_hz: int = 10_000
    window: str = "hamming"
    scale: str = "dBm"
    overlap: float = 0.5
    lna_gain: int = 0
    vga_gain: int = 0
    antenna_amp: bool = False
    antenna_port: int = 2
    span: int = 20_000_000
    ppm_error: int = 0

    def __post_init__(self):
        """
        Runs automatically after initialization to validate ranges.
        Raises ValueError if any parameter is invalid.
        """
        # 1. Validate Center Frequency (8 MHz - 6 GHz)
        if not (8_000_000 <= self.center_freq_hz <= 6_000_000_000):
            raise ValueError(f"Center frequency {self.center_freq_hz} Hz is out of range (8MHz - 6GHz).")

        # 2. Validate Sample Rate (1.5 MHz - 2 GHz)
        if not (1_500_000 <= self.sample_rate_hz <= 2_000_000_000):
            raise ValueError(f"Sample rate {self.sample_rate_hz} Hz is out of range (1.5MHz - 2GHz).")

        # 3. Validate Overlap (0.0 to 0.99)
        if not (0.0 <= self.overlap < 1.0):
            raise ValueError(f"Overlap {self.overlap} is invalid. Must be >= 0.0 and < 1.0.")

        # 4. Validate Antenna Port (1, 2, or 3)
        if self.antenna_port not in [1, 2, 3]:
            raise ValueError(f"Antenna port {self.antenna_port} is invalid. Must be 1, 2, or 3.")

        # 5. Sanity Check Span
        if self.span <= 0:
            raise ValueError(f"Span {self.span} must be positive.")

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
        self.verbose = verbose
        self.context = zmq.asyncio.Context()
        self.socket = self.context.socket(zmq.PAIR)
        if is_server:
            # Clean up previous socket file if it exists (Linux specific)
            if addr.startswith("ipc://"):
                path = addr.replace("ipc://", "")
                if os.path.exists(path):
                    os.remove(path)
            
            self.socket.bind(addr)
        else:
            self.socket.connect(addr)

    async def send_command(self, payload: dict):
        msg = json.dumps(payload)
        await self.socket.send_string(msg)
        if self.verbose:
            print(f"[PY] >> Sent CMD")

    async def wait_for_data(self):
        # This awaits until C actually sends something back
        msg = await self.socket.recv_string()
        if self.verbose:
            print(f"[PY] << Received Payload")
        return json.loads(msg)