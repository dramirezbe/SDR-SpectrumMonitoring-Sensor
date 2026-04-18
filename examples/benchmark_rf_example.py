#!/usr/bin/env python3
"""
Ejemplo de Integración para Módulo RF

Muestra cómo integrar el benchmarking en tu código RF existente
sin modificar nada del código original.
"""

import sys
from pathlib import Path

# Setup path
sys.path.insert(0, str(Path(__file__).parent.parent))

import logging
from utils.benchmarking import (
    create_benchmark_decorator,
    benchmark_context,
    analyze_benchmarks,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ============================================================================
# PASO 1: CREAR EL DECORADOR (una sola vez en tu módulo)
# ============================================================================

benchmark_rf = create_benchmark_decorator(
    output_file="benchmarks/rf_module.json",
    verbose=True,  # Cambia a False si no quieres ver logs en tiempo real
    logger=logger,
)

# ============================================================================
# PASO 2: DECORAR TUS FUNCIONES (sin cambiar el código)
# ============================================================================

# Simula funciones que ya existen en tu código
import time
import numpy as np


@benchmark_rf
def acquire_iq_samples(duration_ms: int, center_freq: float, sample_rate: int = 2400000):
    """
    Adquiere muestras IQ del hardware.
    
    Esta función permanece EXACTAMENTE igual - solo le agregamos @benchmark_rf
    encima sin tocar nada del código interno.
    """
    # Simula lectura de hardware
    num_samples = int(sample_rate * duration_ms / 1000)
    iq_data = np.random.randn(num_samples) + 1j * np.random.randn(num_samples)
    time.sleep(0.01)  # Simula I/O
    return iq_data


@benchmark_rf
def demodulate_fm(iq_data: np.ndarray, center_freq: float = 100.0e6):
    """Demodula FM - SIN CAMBIOS al código original."""
    # Tu código original aquí
    audio = np.real(iq_data)
    time.sleep(0.01)  # Simula procesamiento
    return audio


@benchmark_rf
def demodulate_am(iq_data: np.ndarray):
    """Demodula AM - SIN CAMBIOS al código original."""
    audio = np.abs(iq_data)
    time.sleep(0.01)
    return audio


@benchmark_rf
def calculate_psd(signal: np.ndarray, nperseg: int = 1024, window: str = 'hann'):
    """Calcula PSD - SIN CAMBIOS al código original."""
    # Simula FFT y cálculo de potencia
    psd = np.abs(np.fft.fft(signal[:nperseg])) ** 2
    time.sleep(0.005)
    return psd


@benchmark_rf
def filter_channel(samples: np.ndarray, freq_start: float, freq_end: float, order: int = 50):
    """Filtra un canal - SIN CAMBIOS al código original."""
    # Simula filtrado
    filtered = samples * 0.9  # Dummy filter
    time.sleep(0.02)
    return filtered


# ============================================================================
# PASO 3: USAR LAS FUNCIONES NORMALMENTE
# ============================================================================

def main():
    """Ejemplo de uso - exactamente como si no hubiera benchmarking."""
    
    print("\n" + "="*70)
    print("EJEMPLO: BENCHMARKING DE MÓDULO RF")
    print("="*70 + "\n")
    
    # Crea directorio
    Path("benchmarks").mkdir(exist_ok=True)
    
    # Tus operaciones normales - ahora con benchmarking automático
    logger.info("Adquiriendo muestras IQ...")
    iq_data = acquire_iq_samples(
        duration_ms=100,
        center_freq=104.5e6,
        sample_rate=2400000
    )
    
    logger.info("Demodulando FM...")
    fm_audio = demodulate_fm(iq_data, center_freq=104.5e6)
    
    logger.info("Demodulando AM...")
    am_audio = demodulate_am(iq_data)
    
    logger.info("Calculando PSD...")
    psd = calculate_psd(iq_data, nperseg=2048, window='hamming')
    
    logger.info("Filtrando canal...")
    filtered = filter_channel(iq_data, 103e6, 105e6, order=128)
    
    # ====================================================================
    # PASO 4: ANALIZAR RESULTADOS
    # ====================================================================
    
    print("\n" + "="*70)
    print("ANÁLISIS DE BENCHMARKS")
    print("="*70 + "\n")
    
    results = analyze_benchmarks("benchmarks/rf_module.json")
    
    for func_name, stats in results.items():
        print(f"📊 {func_name}")
        print(f"   Ejecuciones:        {stats['count']}")
        print(f"   Tiempo (ms):")
        print(f"      Promedio:       {stats['time_ms']['mean']:.3f} ms")
        print(f"      Min/Max:        {stats['time_ms']['min']:.3f} / {stats['time_ms']['max']:.3f} ms")
        print(f"   Memoria (MB):")
        print(f"      Promedio:       {stats['memory_mb']['mean']:.3f} MB")
        print(f"      Pico:           {stats['memory_mb']['max']:.3f} MB")
        print(f"   CPU (%):")
        print(f"      Promedio:       {stats['cpu_percent']['mean']:.2f}%")
        print(f"      Máximo:         {stats['cpu_percent']['max']:.2f}%")
        print()


# ============================================================================
# PASO 5: USO CON CONTEXT MANAGER (alternativa para bloques)
# ============================================================================

def example_with_context():
    """Usa context manager para benchmarkear bloques de código."""
    
    with benchmark_context("complete_capture", "benchmarks/rf_context.json", logger):
        # Simula una secuencia completa
        iq_data = acquire_iq_samples(50, 104.5e6)
        audio = demodulate_fm(iq_data)
        spectrum = calculate_psd(audio)


# ============================================================================
# PASO 6: COMPARACIÓN ENTRE CONFIGURACIONES
# ============================================================================

def compare_configurations():
    """Compara rendimiento entre configuraciones."""
    
    print("\n" + "="*70)
    print("COMPARACIÓN: FM vs AM (en el mismo dataset)")
    print("="*70 + "\n")
    
    # Crea datos comunes
    iq_data = acquire_iq_samples(50, 104.5e6, sample_rate=2400000)
    
    # Ejecuta ambos
    logger.info("Ejecutando FM...")
    fm_result = demodulate_fm(iq_data)
    
    logger.info("Ejecutando AM...")
    am_result = demodulate_am(iq_data)
    
    # Analiza
    results = analyze_benchmarks("benchmarks/rf_module.json")
    
    fm_stats = results.get('demodulate_fm', {})
    am_stats = results.get('demodulate_am', {})
    
    if fm_stats and am_stats:
        fm_time = fm_stats['time_ms']['mean']
        am_time = am_stats['time_ms']['mean']
        
        print(f"FM:  {fm_time:.2f} ms")
        print(f"AM:  {am_time:.2f} ms")
        print(f"Diferencia: {abs(fm_time - am_time):.2f} ms")
        print()


# ============================================================================
# INTEGRACIÓN EN TU CÓDIGO EXISTENTE
# ============================================================================

"""
Para integrar esto en tu código actual:

1. En functions.py o donde llames a funciones RF:
   
   from utils.benchmarking import create_benchmark_decorator
   
   benchmark = create_benchmark_decorator(
       output_file="benchmarks/rf_functions.json",
       verbose=False
   )
   
2. Decora tus funciones críticas:
   
   @benchmark
   def your_rf_function(param1, param2):
       # Tu código sin cambios
       return result
   
3. Usa normalmente - el benchmarking es transparente
   
4. Analiza con:
   
   results = analyze_benchmarks("benchmarks/rf_functions.json")

¡Eso es todo! Sin modificar código existente.
"""


if __name__ == '__main__':
    Path("benchmarks").mkdir(exist_ok=True)
    
    main()
    print("\n✅ Benchmarks guardados en: benchmarks/rf_module.json")
    print("📊 Revisa los archivos JSON para análisis detallado")
