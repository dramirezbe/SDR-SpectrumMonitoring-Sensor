import cfg
log = cfg.set_logger()
from utils import ZmqPub, ZmqSub, RequestClient
import asyncio
import numpy as np
import json
import os
import time
from datetime import datetime
from dataclasses import dataclass

@dataclass
class ParamsFilter:
    type: str
    filter_bw_hz: int
    order_filter: int

@dataclass
class ParamsDemodulation:
    type: str
    center_freq_hz: int
    bw_hz: int
    with_metrics: bool

@dataclass
class ParamsAcquisition:
    rf_mode: str
    center_freq_hz: int
    rbw_hz: int
    span: int
    window: str
    overlap: float
    sample_rate_hz: int
    lna_gain: int
    vga_gain: int
    antenna_amp: bool
    antenna_port: int
    demodulation: ParamsDemodulation
    filter: ParamsFilter



topic_data = "data"
topic_sub = "acquire"
pub = ZmqPub(addr=cfg.IPC_CMD_ADDR)
sub = ZmqSub(addr=cfg.IPC_DATA_ADDR, topic=topic_data)
client = RequestClient(cfg.API_URL, timeout=(5, 15), verbose=True, logger=log)

def fetch_params_acquisition(client):
    rc_returned, resp = client.get(f"/{cfg.get_mac()}/configuration")
    json_payload = {}
    
    if resp is not None and resp.status_code == 200:
        try:
            json_payload = resp.json()
        except Exception:
            json_payload = {}
    
    if not json_payload:
        return {}, resp
    
    return {
        "center_freq_hz": int(json_payload.get("center_frequency") or 0),
        "rbw_hz": json_payload.get("resolution_hz"),
        "port": json_payload.get("antenna_port"),
        "win": json_payload.get("window"),
        "overlap": json_payload.get("overlap"),
        "sample_rate_hz": json_payload.get("sample_rate_hz"),
        "lna_gain": json_payload.get("lna_gain"),
        "vga_gain": json_payload.get("vga_gain"),
        "antenna_amp": json_payload.get("antenna_amp"),
    }, resp