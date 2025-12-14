"""
@file utils/request_util.py
@brief Simple reusable HTTP client helper with rc codes and print-based logging.
"""

import requests
from typing import Optional, Tuple, Dict, Any, List
import zmq
import zmq.asyncio
import json
import logging
import re
from dataclasses import dataclass, field

# -------------------------
# Shared / Nested Objects
# -------------------------

@dataclass
class Timeframe:
    start: int  # Unix ms
    end: int    # Unix ms

@dataclass
class Filter:
    type: str
    filter_bw_hz: int
    order_filter: int

@dataclass
class Demodulation:
    type: str
    with_metrics: bool
    bw_hz: int
    center_freq_hz: int
    port_socket: str

@dataclass
class ExcursionObj:
    unit: str
    peak_to_peak_hz: float
    peak_deviation_hz: float
    rms_deviation_hz: float

@dataclass
class DepthObj:
    unit: str
    peak_to_peak: float
    peak_deviation: float
    rms_deviation: float

# -------------------------
# Base Configuration
# -------------------------
@dataclass(kw_only=True)  # <--- ADD kw_only=True HERE
class SpectrumConfig:
    """
    Base parameters shared between Campaign and Realtime.
    """
    center_freq_hz: int
    rbw_hz: int
    sample_rate_hz: int
    span: int
    scale: str
    window: str
    overlap: float
    lna_gain: int
    vga_gain: int
    antenna_amp: bool
    # antenna_port has a default, so it "poisoned" the inheritance order for subclasses
    antenna_port: Optional[int] = None 

# -------------------------
# GET: Responses
# -------------------------

@dataclass(kw_only=True) # <--- ADD kw_only=True HERE
class Campaign(SpectrumConfig):
    campaign_id: int
    status: str
    acquisition_period_s: int
    timeframe: Timeframe
    filter: Optional[Filter] = None

@dataclass(kw_only=True) # <--- ADD kw_only=True HERE
class Realtime(SpectrumConfig):
    demodulation: Optional[Demodulation] = None
    filter: Optional[Filter] = None

@dataclass
class CampaignListResponse:
    campaigns: List[Campaign]

# -------------------------
# POST: Requests
# -------------------------

@dataclass
class DataPost:
    mac: str
    Pxx: List[float]
    start_freq_hz: int
    end_freq_hz: int
    timestamp: int  # Unix ms
    campaign_id: Optional[int] = None
    excursion: Optional[ExcursionObj] = None
    depth: Optional[DepthObj] = None

@dataclass
class StatusPost:
    mac: str
    ram_mb: int
    swap_mb: int
    disk_mb: int
    temp_c: float
    total_ram_mb: int
    total_swap_mb: int
    total_disk_mb: int
    delta_t_ms: int
    ping_ms: float
    timestamp_ms: int
    last_kal_ms: int
    last_ntp_ms: int
    logs: str
    
    # We don't define cpu_0, cpu_1 here.
    # We store them in a list after processing.
    cpu_loads: List[float] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]):
        """
        Custom constructor to handle dynamic flat keys.
        """
        # 1. Separate known fields from dynamic CPU fields
        known_fields = {
            "mac", "ram_mb", "swap_mb", "disk_mb", "temp_c",
            "total_ram_mb", "total_swap_mb", "total_disk_mb",
            "delta_t_ms", "ping_ms", "timestamp_ms",
            "last_kal_ms", "last_ntp_ms", "logs"
        }
        
        # Filter for the arguments that match our dataclass fields
        init_args = {k: v for k, v in data.items() if k in known_fields}
        
        # 2. Instantiate the class
        obj = cls(**init_args)
        
        # 3. Dynamically find and sort CPU keys (cpu_0, cpu_1, ..., cpu_N)
        # We look for keys starting with 'cpu_' and followed by an integer
        cpu_keys = [k for k in data.keys() if k.startswith("cpu_") and k[4:].isdigit()]
        
        # Sort them numerically by the index (the part after 'cpu_')
        cpu_keys.sort(key=lambda x: int(x.split('_')[1]))
        
        # 4. Populate the cpu_loads list
        obj.cpu_loads = [data[k] for k in cpu_keys]
        
        return obj


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

class ZmqPub:
    def __init__(self, addr, verbose=False, log=logging.getLogger(__name__)):
        self.verbose = verbose
        self._log = log
        self.context = zmq.Context()
        self.socket = self.context.socket(zmq.PUB)
        # Bind to IPC address
        self.socket.bind(addr)

        self._log.info(f"ZmqPub initialized at {addr}")
        

    def public_client(self, topic: str, payload: dict):
        json_msg = json.dumps(payload)
        full_msg = f"{topic} {json_msg}"
        self.socket.send_string(full_msg)
        if self.verbose:
            self._log.info(f"[ZmqPub]Sent: {full_msg}")

    def close(self):
        self.socket.close()
        self.context.term()

class ZmqSub:
    def __init__(self, addr, topic: str, verbose=False, log=logging.getLogger(__name__)):
        self.verbose = verbose
        self.topic = topic
        self._log = log
        self.context = zmq.asyncio.Context()
        self.socket = self.context.socket(zmq.SUB)
        # Connect to IPC address
        self.socket.connect(addr)
        self.socket.subscribe(self.topic.encode('utf-8'))

        self._log.info(f"ZmqPub initialized at {addr} with topic {self.topic}")

    async def wait_msg(self):
        while True:
            full_msg = await self.socket.recv_string()
            pub_topic, json_msg = full_msg.split(" ", 1)

            if pub_topic == self.topic:
                if self.verbose:
                    print(f"[ZmqSub-{self.topic}] Received: {json_msg}")
                return json.loads(json_msg)

    def close(self):
        self.socket.close()
        self.context.term()

