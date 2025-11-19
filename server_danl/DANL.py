import csv
import numpy as np
import matplotlib.pyplot as plt
import os
import re
from pathlib import Path
from collections import defaultdict


def load_psd_csv(filepath, RBW=None):
    """Carga un archivo CSV con columnas: Frequency (Hz), PSD (dBFS o dBm)."""
    freqs, psd = [], []

    if not os.path.exists(filepath):
        print(f"[ERROR] Archivo no encontrado: {filepath}")
        return np.array([]), np.array([])

    with open(filepath, 'r', newline='') as f_csv:
        reader = csv.reader(f_csv)
        try:
            next(reader)  # encabezado
        except StopIteration:
            print(f"[WARN] Archivo vacío: {filepath}")
            return np.array([]), np.array([])

        for row in reader:
            if len(row) < 2:
                continue
            try:
                freq = float(row[0].strip())
                val = float(row[1].strip())
                freqs.append(freq)
                psd.append(val)
            except ValueError:
                continue

    if len(freqs) == 0 or len(psd) == 0:
        print(f"[WARN] Sin datos válidos en {filepath}")
        return np.array([]), np.array([])

    freqs = np.array(freqs)
    psd = np.array(psd)

    # Ajuste por RBW
    if RBW is not None and RBW > 0:
        psd = psd + 10 * np.log10(RBW)
        print(f"[INFO] PSD ajustada a DANL con RBW = {RBW:.2f} Hz")

    psd = np.nan_to_num(psd, nan=-200, posinf=-200, neginf=-200)
    return freqs, psd


def detect_noise_floor_from_psd(Pxx, delta_dB=2.0):
    """Detecta el piso de ruido en una PSD (en dB)."""
    if len(Pxx) == 0:
        raise ValueError("PSD vacía, no se puede detectar piso de ruido.")

    Pmin = np.min(Pxx)
    Pmax = np.max(Pxx)
    centers = np.arange(Pmin + delta_dB/2, Pmax, delta_dB/2)
    results = []

    for c in centers:
        lower = c - delta_dB/2
        upper = c + delta_dB/2
        segment = Pxx[(Pxx >= lower) & (Pxx < upper)]
        if len(segment) == 0:
            continue
        results.append({"center_dB": c, "count": len(segment)})

    if not results:
        raise RuntimeError("No se generaron histogramas válidos.")

    best_segment = max(results, key=lambda x: x["count"])
    return best_segment["center_dB"]


def analyze_noise_floor_all(folder_path, delta_dB=0.5, plot=True):
    """Analiza todos los archivos CSV de PSD, promedia repeticiones por frecuencia,
       guarda resultados y un resumen estadístico (min, max, mean, var)."""
    all_files = [
        f for f in os.listdir(folder_path)
        if f.lower().startswith("psd_output_") and f.lower().endswith(".csv")
    ]

    if not all_files:
        print(f"[ERROR] No se encontraron archivos CSV válidos en {folder_path}")
        return None

    # Regex mejorada para aceptar sufijos (_1, _2, etc.)
    def extract_fc(filename):
        match = re.search(r"psd_output_[a-zA-Z]+_([0-9]+)(?:_\d+)?\.csv$", filename)
        return int(match.group(1)) if match else None

    files_with_fc = [(extract_fc(f), f) for f in all_files if extract_fc(f) is not None]

    if not files_with_fc:
        print("[ERROR] No se pudieron extraer frecuencias centrales.")
        return None

    # Agrupar archivos por frecuencia
    grupos = defaultdict(list)
    for fc, fname in files_with_fc:
        grupos[fc].append(fname)

    frecs_MHz, pisos_prom = [], []

    print("🔎 Iniciando análisis de piso de ruido (promediado por frecuencia)...\n")

    for fc, archivos in sorted(grupos.items()):
        pisos_individuales = []
        for fname in archivos:
            filepath = os.path.join(folder_path, fname)
            f, Pxx = load_psd_csv(filepath)
            if len(Pxx) == 0:
                continue
            try:
                piso_dB = detect_noise_floor_from_psd(Pxx, delta_dB=delta_dB)
                pisos_individuales.append(piso_dB)
                print(f"  • {fname:<30} → Piso de ruido = {piso_dB:.2f} dB")
            except Exception as e:
                print(f"[WARN] {fname}: {e}")

        if pisos_individuales:
            piso_prom = float(np.mean(pisos_individuales))
            frecs_MHz.append(fc)
            pisos_prom.append(piso_prom)
            print(f"  → Promedio {fc} MHz = {piso_prom:.2f} dB ({len(pisos_individuales)} mediciones)\n")

    if len(pisos_prom) == 0:
        print("[ERROR] No se pudieron obtener pisos de ruido válidos.")
        return None

    # Estadísticas globales (sobre los DANL promediados por frecuencia)
    pisos_arr = np.array(pisos_prom)
    piso_prom_global = float(np.mean(pisos_arr))
    piso_min = float(np.min(pisos_arr))
    piso_max = float(np.max(pisos_arr))
    piso_var = float(np.var(pisos_arr))  # varianza poblacional; para muestra usar ddof=1
    fc_min = int(frecs_MHz[np.argmin(pisos_arr)])
    fc_max = int(frecs_MHz[np.argmax(pisos_arr)])

    print("\n📊 Resultados globales:")
    print(f"  Promedio global DANL: {piso_prom_global:.2f} dB")
    print(f"  Mínimo: {piso_min:.2f} dBFS en {fc_min} MHz")
    print(f"  Máximo: {piso_max:.2f} dBFS en {fc_max} MHz")
    print(f"  Varianza: {piso_var:.4f} (dB^2)")

    # === Crear carpeta de resultados ===
    results_dir = Path(folder_path) / "results"
    results_dir.mkdir(exist_ok=True)

    # === Guardar CSV con resultados por frecuencia ===
    csv_path = results_dir / "danl_results.csv"
    with open(csv_path, "w", newline="") as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow(["Frecuencia_MHz", "DANL_promedio_dBFS"])
        for fc, danl in zip(frecs_MHz, pisos_prom):
            writer.writerow([fc, f"{danl:.2f}"])
    print(f"\n💾 Resultados por frecuencia guardados en: {csv_path}")

    # === Guardar CSV resumen con métricas globales ===
    summary_path = results_dir / "danl_summary.csv"
    with open(summary_path, "w", newline="") as sfile:
        writer = csv.writer(sfile)
        writer.writerow(["Metric", "Value"])
        writer.writerow(["piso_promedio_global_dBFS", f"{piso_prom_global:.2f}"])
        writer.writerow(["piso_min_dBFS", f"{piso_min:.2f}"])
        writer.writerow(["piso_max_dBFS", f"{piso_max:.2f}"])
        writer.writerow(["piso_var_dB2", f"{piso_var:.6f}"])
        writer.writerow(["fc_min_MHz", f"{fc_min}"])
        writer.writerow(["fc_max_MHz", f"{fc_max}"])
    print(f"💾 Resumen global guardado en: {summary_path}")

    # === Graficar y guardar imagen ===
    if plot and len(frecs_MHz) > 0:
        plt.figure(figsize=(9, 5))
        plt.plot(frecs_MHz, pisos_prom, marker='o', lw=1.5)
        plt.axhline(piso_prom_global, color='r', linestyle='--', lw=1.2,
                    label=f"Promedio global = {piso_prom_global:.2f} dB")
        plt.title("DANL promedio vs frecuencia central")
        plt.xlabel("Frecuencia central [MHz]")
        plt.ylabel("DANL [dBFS]")
        plt.grid(True)
        plt.legend()
        plt.tight_layout()

        plot_path = results_dir / "DANL_vs_Frecuencia.png"
        plt.savefig(plot_path, dpi=300)
        plt.close()
        print(f"🖼️ Gráfica guardada en: {plot_path}")

    return {
        "frecs_MHz": np.array(frecs_MHz),
        "pisos_dB": np.array(pisos_prom),
        "piso_prom_global": piso_prom_global,
        "piso_min": piso_min,
        "piso_max": piso_max,
        "piso_var": piso_var,
        "fc_min": fc_min,
        "fc_max": fc_max,
    }


# === Ejemplo de uso ===
if __name__ == "__main__":
    base_dir = Path(__file__).resolve().parent
    ruta_outputs = base_dir / "Output_DANL_dbfs2"
    resultados = analyze_noise_floor_all(ruta_outputs, delta_dB=0.5)
