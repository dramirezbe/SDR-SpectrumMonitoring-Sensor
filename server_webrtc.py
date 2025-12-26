#!/usr/bin/env python3
import asyncio
import json
import threading
import struct
import traceback
import time
import cfg

# Initialize Logger
log = cfg.set_logger()

import websockets
from websockets.exceptions import ConnectionClosed
from websockets.protocol import State
try:
    from websockets.legacy.exceptions import InvalidStatusCode, InvalidHandshake
except ImportError:
    # Handle versions where legacy is merged or named differently
    from websockets.exceptions import InvalidStatusCode, InvalidHandshake

import gi
gi.require_version("Gst", "1.0")
gi.require_version("GstWebRTC", "1.0")
gi.require_version("GstSdp", "1.0")
gi.require_version("GLib", "2.0")
from gi.repository import Gst, GstWebRTC, GstSdp, GLib

# =========================
# Config
# =========================
SENSOR_ID   = cfg.get_mac()
SIGNAL_URL  = f"wss://rsm.ane.gov.co:12443/ws/signal/{SENSOR_ID}"
STUN_SERVER = "stun://stun.l.google.com:19302"

TCP_HOST = "0.0.0.0"
TCP_PORT = 9000
PT = 96
HDR_FMT  = "!IIIHH"
HDR_SIZE = struct.calcsize(HDR_FMT)
MAGIC    = 0x4F505530
DEFAULT_FRAME_MS = 20
RETRY_SECONDS = 5 # Increased slightly for stability

Gst.init(None)

PIPELINE_DESC = f"""
webrtcbin name=wb bundle-policy=max-bundle stun-server="{STUN_SERVER}"
appsrc name=opussrc is-live=true format=time do-timestamp=true !
  queue !
  opusparse !
  rtpopuspay pt={PT} !
  queue !
  wb.sink_0
"""

class Publisher:
    def __init__(self, loop, ws):
        self.loop = loop
        self.ws = ws
        self.cand_out = 0
        self.glib_loop = GLib.MainLoop()
        self.glib_thread = threading.Thread(target=self.glib_loop.run, daemon=True)
        
        self.pipe = Gst.parse_launch(PIPELINE_DESC)
        self.webrtc = self.pipe.get_by_name("wb")
        self.appsrc = self.pipe.get_by_name("opussrc")
        
        caps = Gst.Caps.from_string("audio/x-opus, rate=(int)48000, channels=(int)1, channel-mapping-family=(int)0")
        self.appsrc.set_property("caps", caps)

        self.webrtc.connect("on-negotiation-needed", self.on_negotiation_needed)
        self.webrtc.connect("on-ice-candidate", self.on_ice_candidate)

        bus = self.pipe.get_bus()
        bus.add_signal_watch()
        bus.connect("message", self.on_bus)

        self._running = False
        self._pts = 0

    def start(self):
        self.glib_thread.start()
        self.pipe.set_state(Gst.State.PLAYING)
        self._running = True
        log.info("[SENSOR] WebRTC Pipeline PLAYING")

    def stop(self):
        self._running = False
        self.pipe.set_state(Gst.State.NULL)
        if self.glib_loop.is_running():
            GLib.idle_add(self.glib_loop.quit)

    def on_bus(self, bus, msg):
        if msg.type == Gst.MessageType.ERROR:
            err, dbg = msg.parse_error()
            log.error(f"[GST] {err}")

    def _ws_send(self, obj):
     # Check the internal state machine directly
     if self.ws and self.ws.state is State.OPEN:
         asyncio.run_coroutine_threadsafe(self.ws.send(json.dumps(obj)), self.loop)

    def on_ice_candidate(self, element, mline, candidate):
        self._ws_send({"type":"candidate","mlineindex":int(mline),"candidate":candidate})

    def on_negotiation_needed(self, element):
        promise = Gst.Promise.new_with_change_func(self.on_offer_created, element, None)
        element.emit("create-offer", None, promise)

    def on_offer_created(self, promise, element, _):
        reply = promise.get_reply()
        offer = reply.get_value("offer") if reply and reply.has_field("offer") else None
        if offer:
            element.emit("set-local-description", offer, Gst.Promise.new())
            self._ws_send({"type":"offer","sdp":offer.sdp.as_text()})

    def set_answer(self, sdp_text):
        def _do():
            res, sdp = GstSdp.sdp_message_new()
            GstSdp.sdp_message_parse_buffer(sdp_text.encode("utf-8"), sdp)
            ans = GstWebRTC.WebRTCSessionDescription.new(GstWebRTC.WebRTCSDPType.ANSWER, sdp)
            self.webrtc.emit("set-remote-description", ans, Gst.Promise.new())
            return False
        GLib.idle_add(_do)

    def add_candidate(self, mline, cand):
        GLib.idle_add(lambda: self.webrtc.emit("add-ice-candidate", int(mline), cand) and False)

    def push_opus_frame(self, opus_bytes: bytes):
        if not self._running: return
        dur_ns = int(DEFAULT_FRAME_MS * 1e6)
        def _do():
            buf = Gst.Buffer.new_allocate(None, len(opus_bytes), None)
            buf.fill(0, opus_bytes)
            buf.pts = buf.dts = self._pts
            buf.duration = dur_ns
            self._pts += dur_ns
            self.appsrc.emit("push-buffer", buf)
            return False
        GLib.idle_add(_do)

# =========================
# Shared State for TCP -> WebRTC
# =========================
current_publisher = None

async def tcp_reader_task():
    """ Keeps the TCP server alive regardless of WebRTC status """
    async def handle_client(reader, writer):
        log.info("[TCP] C Motor connected")
        try:
            while True:
                hdr = await reader.readexactly(HDR_SIZE)
                magic, seq, sr, ch, plen = struct.unpack(HDR_FMT, hdr)
                payload = await reader.readexactly(plen)
                
                # Push to the GLOBAL publisher if one is active
                if current_publisher and current_publisher._running:
                    current_publisher.push_opus_frame(payload)
        except Exception as e:
            log.warning(f"[TCP] Client disconnected: {e}")
        finally:
            writer.close()

    server = await asyncio.start_server(handle_client, TCP_HOST, TCP_PORT)
    async with server:
        await server.serve_forever()

async def run_signaling_session():
    global current_publisher
    
    # added connect_timeout and start_timeout to prevent hanging if DNS is slow
    async with websockets.connect(SIGNAL_URL, open_timeout=10, close_timeout=5) as ws:
        await ws.send(json.dumps({"role":"sensor","sensor_id":SENSOR_ID}))
        log.info("[WS] Connected and registered")
        
        loop = asyncio.get_running_loop()
        pub = Publisher(loop, ws)
        current_publisher = pub
        pub.start()

        try:
            async for msg in ws:
                obj = json.loads(msg)
                if obj.get("type") == "answer":
                    pub.set_answer(obj["sdp"])
                elif obj.get("type") == "candidate":
                    pub.add_candidate(obj["mlineindex"], obj["candidate"])
        finally:
            log.info("[WS] Connection closed, cleaning up publisher...")
            pub.stop()
            current_publisher = None

async def main():
    # Start TCP server once and let it run forever
    asyncio.create_task(tcp_reader_task())
    
    while True:
        try:
            await run_signaling_session()
        except (OSError, ConnectionClosed, InvalidStatusCode, InvalidHandshake, asyncio.TimeoutError) as e:
            log.error(f"[SYSTEM] Connection failed: {type(e).__name__}. Retrying in {RETRY_SECONDS}s...")
        except Exception as e:
            log.critical(f"[SYSTEM] Unexpected error: {e}\n{traceback.format_exc()}")
        
        await asyncio.sleep(RETRY_SECONDS)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass