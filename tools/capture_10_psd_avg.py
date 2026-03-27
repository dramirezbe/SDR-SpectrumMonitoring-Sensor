#!/usr/bin/env python3
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parent.parent
print(f"Project root: {PROJECT_ROOT}")
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import asyncio
from dataclasses import asdict

import matplotlib.pyplot as plt
import numpy as np


import cfg
from utils import ServerRealtimeConfig, ZmqPairController



async def main() -> int:
    log = cfg.set_logger()

    rt_cfg = ServerRealtimeConfig(
        method_psd="welch",
        center_freq_hz=95_000_000,
        sample_rate_hz=20_000_000,
        rbw_hz=10_000,
        window="hamming",   
        overlap=0.5,
        lna_gain=0,
        vga_gain=0,
        antenna_amp=True,
        antenna_port=1,
        ppm_error=0,
        cooldown_request=0.001,
        demodulation=None,
        filter=None,
    )

    payload = asdict(rt_cfg)
    captures = []

    async with ZmqPairController(addr=cfg.IPC_ADDR, is_server=True, verbose=False) as zmq_ctrl:
        await asyncio.sleep(0.1)

        for i in range(10):
            await zmq_ctrl.send_command(payload)
            data = await asyncio.wait_for(zmq_ctrl.wait_for_data(), timeout=8)

            if not data or "Pxx" not in data:
                log.warning(f"Capture {i+1}/10 inválida, se omite.")
                continue

            pxx = np.asarray(data["Pxx"], dtype=float)
            if pxx.size == 0:
                log.warning(f"Capture {i+1}/10 vacía, se omite.")
                continue

            captures.append((data, pxx))
            log.info(f"Capture {i+1}/10 OK | bins={pxx.size}")

    if not captures:
        log.error("No se obtuvieron PSD válidos.")
        return 1

    min_bins = min(arr.size for _, arr in captures)
    stack = np.vstack([arr[:min_bins] for _, arr in captures])
    pxx_avg = np.mean(stack, axis=0)

    first = captures[0][0]
    start_hz = float(first.get("start_freq_hz", 0.0))
    end_hz = float(first.get("end_freq_hz", 0.0))

    if end_hz > start_hz and min_bins > 1:
        f_hz = np.linspace(start_hz, end_hz, min_bins)
        x = f_hz / 1e6
        xlabel = "Frecuencia (MHz)"
    else:
        x = np.arange(min_bins)
        xlabel = "Bin"

    plt.figure(figsize=(12, 5))
    plt.plot(x, pxx_avg, linewidth=1.3, label=f"Promedio ({len(captures)} PSD)")
    plt.title("Promedio de 10 PSD (cooldown_request=0.1 s)")
    plt.xlabel(xlabel)
    plt.ylabel("Potencia (dB)")
    plt.grid(True, alpha=0.3)
    plt.legend()
    plt.tight_layout()
    plt.show()

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
