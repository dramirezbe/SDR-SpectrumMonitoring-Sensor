"""
@file utils/request_util.py
@brief Simple reusable HTTP client helper with rc codes and print-based logging.
"""

import json
import requests
from typing import Optional, Tuple, Dict, Any

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
    ):
        self.base_url = base_url.rstrip("/")
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
                self._log.info(f"[HTTP] {method} → {url}")

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
