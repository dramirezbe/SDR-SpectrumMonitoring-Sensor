import numpy as np
import matplotlib.pyplot as plt
from scipy import signal
import os, subprocess, time

# =========================================================
# 1. POLYPHASE FILTER BANK REAL (FIR + FFT)
# =========================================================

class RealPolyphaseFilterBank:
    """
    Implementación REAL de un Polyphase Filter Bank (PFB)
    basada en un FIR prototipo largo (M*K taps).

    Referencia clásica tipo CASPER / GNU Radio.
    """
    def __init__(self, num_channels=256, taps_per_channel=8, window='kaiser'):
        self.M = num_channels
        self.K = taps_per_channel
        self.L = self.M * self.K  # longitud total del FIR

        # --- Diseño del filtro prototipo ---
        if window == 'kaiser':
            beta = 8.6  # ~80 dB
            h = signal.firwin(self.L, cutoff=1/self.M, window=('kaiser', beta))
        else:
            h = signal.firwin(self.L, cutoff=1/self.M, window=window)

        # Normalización de energía
        h = h / np.sqrt(np.sum(h**2))

        # --- Descomposición polifásica ---
        # h_p[p, m] = h[p + m*M]
        self.h_poly = np.reshape(h, (self.K, self.M))

    def process(self, x):
        """
        Procesa IQ complejos y devuelve PSD promediada.
        """
        # número de FFTs posibles
        n_blocks = (len(x) - self.L) // self.M
        if n_blocks <= 0:
            raise ValueError("No hay suficientes muestras")

        psd_acc = np.zeros(self.M, dtype=np.float64)

        for b in range(n_blocks):
            x_block = x[b*self.M : b*self.M + self.L]

            # Matriz polifásica de datos
            X = np.reshape(x_block, (self.K, self.M))

            # Filtrado polifásico (suma ponderada)
            y = np.sum(X * self.h_poly, axis=0)

            # FFT
            Y = np.fft.fftshift(np.fft.fft(y))
            psd_acc += np.abs(Y)**2

        psd_avg = psd_acc / n_blocks
        return psd_avg

# =========================================================
# 2. ADQUISICIÓN HACKRF
# =========================================================

def adquirir_hackrf(config):
    # Si el archivo existe, lo borramos para asegurar datos frescos
    if os.path.exists(config['nombre_archivo']):
        print(f"[HW] Borrando archivo antiguo...")
        try:
            os.remove(config['nombre_archivo'])
        except OSError:
            pass

    print(f"\n[HW] Capturando {config['nombre_archivo']}...")
    
    cmd = [
        "hackrf_transfer", 
        "-r", config['nombre_archivo'],
        "-f", str(config['frecuencia_central']), 
        "-s", str(config['ancho_banda']),
        "-n", str(config['num_muestras']), 
        "-l", str(config['lna']),
        "-g", str(config['vga']), 
        "-a", str(config['amp'])
    ]
    
    result = subprocess.run(cmd, capture_output=True, text=True)
    
    if result.returncode != 0:
        print(f"❌ Error en hackrf_transfer: {result.stderr}")
        return False
        
    print("✅ Captura completada.")
    return True

# =========================================================
# 3. PROCESAMIENTO
# =========================================================

def procesar_pfb_real(filename, cfg):
    raw = np.fromfile(filename, dtype=np.int8).astype(np.float32)
    i = raw[0::2]
    q = raw[1::2]
    iq = (i + 1j*q) / 128.0

    M = 1024
    K = 8
    fs = cfg['ancho_banda']

    pfb = RealPolyphaseFilterBank(M, K)

    t0 = time.time()
    psd = pfb.process(iq)
    print(f"PFB real en {time.time()-t0:.2f} s")

    # PSD física (W/Hz)
    # psd viene de |FFT|^2 promediado
    # Normalización correcta para PFB (potencia por Hz)
    psd_w_hz = psd / (fs * M)


    # --- Conversión a dBm/Hz ---
    psd_dbm_hz = 10 * np.log10(psd_w_hz + 1e-18) + 30


    # --- Conversión a dBm por bin (más realista para analizador) ---
    bin_bw = fs / M
    psd_dbm_bin = psd_dbm_hz + 10 * np.log10(bin_bw)


    freqs = np.fft.fftshift(np.fft.fftfreq(M, d=1/fs)) + cfg['frecuencia_central']
    return freqs, psd_dbm_bin

def corregir_respuesta_filtro(psd):
    """
    Aproximación simple: Asume que el ruido debería ser plano.
    Calcula la tendencia del piso de ruido y la invierte.
    """
    import scipy.signal
    
    # 1. Usar un filtro de mediana para encontrar el "piso" ignorando los picos (estaciones)
    # El kernel debe ser lo bastante ancho para ignorar señales finas
    noise_floor_shape = scipy.signal.medfilt(psd, kernel_size=101)
    
    # 2. Calcular cuánto se desvía el piso del promedio central
    center_val = np.median(noise_floor_shape)
    correction_curve = center_val - noise_floor_shape
    
    # 3. Aplicar corrección
    return psd + correction_curve


# =========================================================
# 4. PLOT
# =========================================================

def plot(freqs, psd, cfg):
    plt.figure(figsize=(12,6))
    plt.plot(freqs/1e6, psd)
    plt.xlabel("Frecuencia (MHz)")
    plt.ylabel("PSD (dB/Hz)")
    plt.title("Espectro RF con PFB real")
    plt.grid(True)
    plt.show()

# =========================================================
# 5. MAIN
# =========================================================

cfg = {
    "nombre_archivo": "/home/gcpds/Desktop/Procesamiento_ANE2/ANE2_procesamiento/Comparativa_PSD/Polyphase98MHz_FM",
    "frecuencia_central": int(98e6),
    "ancho_banda": 20e6,
    "num_muestras": int(2e6),
    "lna": 20,
    "vga": 0,
    "amp": 0
}

if __name__ == '__main__':
    import time
    t0 = time.time()

    correcion=False  # Activar corrección de respuesta de filtro
    if adquirir_hackrf(cfg):
        t1= time.time()
        f, p = procesar_pfb_real(cfg['nombre_archivo'], cfg)
        p_corregido = corregir_respuesta_filtro(p)

        t2= time.time()
        print(f"[TIME] Tiempo total de adquisición: {t1-t0:.2f} segundos.")
        print(f"[TIME] Tiempo total de procesamiento: {t2-t1:.2f} segundos.")

        if not correcion:
            plot(f, p, cfg)
        else:
            plot(f, p_corregido, cfg)