# signaling_relay.py
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
import time

from typing import Dict, Optional
import json

app = FastAPI()

class Pair:
    sensor: Optional[WebSocket] = None
    client: Optional[WebSocket] = None

pairs: Dict[str, Pair] = {}

@app.get("/health")
def health():
    """
    Lightweight health check:
    - service is running
    - optionally, report number of active pairs
    """
    # count sensors/clients currently connected
    sensors = sum(1 for p in pairs.values() if p.sensor is not None)
    clients = sum(1 for p in pairs.values() if p.client is not None)

    return {
        "ok": True,
        "ts": time.time(),
        "active_sensor_ws": sensors,
        "active_client_ws": clients,
        "known_ids": len(pairs),
    }


@app.get("/page")
@app.get("/page/")
def root():
    return FileResponse("player_webrtc.html", media_type="text/html")


@app.websocket("/ws/signal/{sensor_id}")
async def ws_signal(ws: WebSocket, sensor_id: str):
    await ws.accept()

    # first message declares role
    hello = await ws.receive_text()
    try:
        obj = json.loads(hello)
        role = obj.get("role")
    except Exception:
        await ws.close(code=1008)
        return

    p = pairs.setdefault(sensor_id, Pair())

    # enforce 1 sensor + 1 client max
    if role == "sensor":
        if p.sensor is not None:
            await ws.close(code=1013)  # try again later
            return
        p.sensor = ws
        peer = lambda: p.client
    elif role == "client":
        if p.client is not None:
            await ws.close(code=1013)
            return
        p.client = ws
        peer = lambda: p.sensor
    else:
        await ws.close(code=1008)
        return

    try:
        while True:
            msg = await ws.receive_text()
            other = peer()
            if other is not None:
                await other.send_text(msg)
    except WebSocketDisconnect:
        pass


    finally:
        if role == "sensor" and p.sensor is ws:
            p.sensor = None
        if role == "client" and p.client is ws:
            p.client = None

        # delete empty pair to avoid unbounded growth
        if p.sensor is None and p.client is None:
            pairs.pop(sensor_id, None)
