"""
@file utils/request_util.py
@brief Simple reusable HTTP client helper with rc codes and print-based logging.
"""

import requests
from typing import Optional, Tuple, Dict, Any
import zmq
import zmq.asyncio
import json
import logging

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
        timeout: Tuple[float, float] = (5, 15),
        verbose: bool = False,
        logger=None,
        api_key: str = "",
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.verbose = verbose
        self._log = logger
        self.api_key = api_key # Stored API key

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
        
        # --- NEW LOGIC: Add API Key Authorization Header ---
        final_headers = headers if headers is not None else {}
        if self.api_key and "Authorization" not in final_headers:
            # Ensure the Authorization header is set if an API key exists
            final_headers["Authorization"] = f"ApiKey {self.api_key}"
            
        if self.verbose and self._log:
            self._log.info(f"[HTTP] Authorization header set: {'Authorization' in final_headers}")
        # --------------------------------------------------

        try:
            if self.verbose and self._log:
                # Log URL and method
                self._log.info(f"[HTTP] {method} → {url}")
                # Optional: Log headers to confirm API key is present (use with caution)
                # self._log.info(f"[HTTP] Headers: {final_headers}")

            resp = requests.request(
                method,
                url,
                headers=final_headers, # Use final_headers with API key
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
            # ... (Existing error handling logic remains the same)
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
        
# Shared IPC address
IPC_ADDR = "ipc:///tmp/zmq_feed"

class ZmqPub:
    def __init__(self, verbose=False, log=logging.getLogger(__name__)):
        self.verbose = verbose
        self._log = log
        self.context = zmq.Context()
        self.socket = self.context.socket(zmq.PUB)
        # Bind to IPC address
        self.socket.bind(IPC_ADDR)

        self._log.info(f"ZmqPub initialized at {IPC_ADDR}")
        

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
    def __init__(self, topic: str, verbose=False, log=logging.getLogger(__name__)):
        self.verbose = verbose
        self.topic = topic
        self._log = log
        self.context = zmq.asyncio.Context()
        self.socket = self.context.socket(zmq.SUB)
        # Connect to IPC address
        self.socket.connect(IPC_ADDR)
        self.socket.subscribe(self.topic.encode('utf-8'))

        self._log.info(f"ZmqPub initialized at {IPC_ADDR} with topic {self.topic}")

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

