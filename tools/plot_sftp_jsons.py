#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np


def build_param_text(data: dict) -> str:
    acq = data.get("acquisition_config", {})
    dc = data.get("dc_correction", {})

    lines = [
        f"CF: {acq.get('center_freq_hz', 'n/a')/1e6:.3f} MHz" if acq.get("center_freq_hz") is not None else "CF: n/a",
        f"SR: {acq.get('sample_rate_hz', 'n/a')/1e6:.3f} MHz" if acq.get("sample_rate_hz") is not None else "SR: n/a",
        f"RBW: {acq.get('rbw_hz', 'n/a')/1e3:.1f} kHz" if acq.get("rbw_hz") is not None else "RBW: n/a",
        f"Window: {acq.get('window', 'n/a')}",
        f"Overlap: {acq.get('overlap', 'n/a')}",
        f"LNA/VGA: {acq.get('lna_gain', 'n/a')}/{acq.get('vga_gain', 'n/a')}",
        f"Antenna amp: {acq.get('antenna_amp', 'n/a')}",
        f"Antenna port: {acq.get('antenna_port', 'n/a')}",
        f"PPM: {acq.get('ppm_error', 'n/a')}",
        f"DC corr: {dc.get('applied', 'n/a')} ({dc.get('mode', 'n/a')})",
    ]
    return "\n".join(lines)


def plot_one_json(json_path: Path, out_dir: Path) -> Path:
    with json_path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    pxx = np.asarray(data.get("Pxx", []), dtype=float)
    if pxx.size == 0:
        raise ValueError(f"{json_path.name}: no contiene 'Pxx' valido")

    f0 = data.get("start_freq_hz")
    f1 = data.get("end_freq_hz")
    if f0 is None or f1 is None:
        raise ValueError(f"{json_path.name}: faltan 'start_freq_hz' o 'end_freq_hz'")

    freqs_mhz = np.linspace(float(f0), float(f1), pxx.size) / 1e6

    fig, ax = plt.subplots(figsize=(12, 6))
    ax.plot(freqs_mhz, pxx, linewidth=1.1, color="#1f77b4")
    ax.set_title(json_path.stem)
    ax.set_xlabel("Frecuencia (MHz)")
    ax.set_ylabel("PSD (dB)")
    ax.grid(True, alpha=0.3)

    ax.text(
        0.98,
        0.98,
        build_param_text(data),
        transform=ax.transAxes,
        ha="right",
        va="top",
        fontsize=9,
        family="monospace",
        bbox={"facecolor": "white", "alpha": 0.85, "edgecolor": "gray", "boxstyle": "round,pad=0.4"},
    )

    out_path = out_dir / f"{json_path.stem}.png"
    fig.tight_layout()
    fig.savefig(out_path, dpi=160)
    plt.close(fig)
    return out_path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Genera PNGs de los JSON de sdr-sftp-test con parametros incrustados en el plot"
    )
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=Path("sdr-sftp-test"),
        help="Directorio con JSON de entrada",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("sdr-sftp-test/plots"),
        help="Directorio donde guardar PNGs",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Cantidad maxima de JSON a procesar (orden alfabetico)",
    )
    args = parser.parse_args()

    input_dir = args.input_dir
    out_dir = args.output_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    json_files = sorted(input_dir.glob("*.json"))[: args.limit]
    if not json_files:
        raise FileNotFoundError(f"No se encontraron JSON en {input_dir}")

    print(f"Procesando {len(json_files)} archivos JSON...")
    for path in json_files:
        png = plot_one_json(path, out_dir)
        print(f"OK -> {png}")

    print("Listo.")


if __name__ == "__main__":
    main()
