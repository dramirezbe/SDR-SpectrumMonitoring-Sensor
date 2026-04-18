#!/usr/bin/env python3
"""
Script de Demostración: Benchmarking Sin Ensuciar el Código

Demuestra cómo usar decoradores y context managers sin modificar
el código existente del proyecto rf/.
"""

import sys
import logging
from pathlib import Path
import json
import time
import os

# Añade el directorio padre al path
sys.path.insert(0, str(Path(__file__).parent.parent))

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Importa el módulo de benchmarking
from utils.benchmarking import (
    create_benchmark_decorator,
    benchmark_context,
    analyze_benchmarks,
    SystemMonitor,
)


# ============================================================================
# SIMULAR FUNCIONES RF (sin modificar)
# ============================================================================

def demodulate_fm(iq_data, center_freq=100.0e6, sample_rate=2400000):
    """Simula demodulación FM."""
    import numpy as np
    time.sleep(0.01)  # Simula procesamiento
    return np.ones(len(iq_data))


def calculate_psd(signal, nperseg=1024, window='hann'):
    """Simula cálculo de PSD."""
    time.sleep(0.005)  # Simula procesamiento
    return signal[:nperseg] ** 2


def filter_channel(samples, freq_start, freq_end, order=50):
    """Simula filtrado de canal."""
    time.sleep(0.015)  # Simula procesamiento
    return samples


# ============================================================================
# DEMOSTRACIÓN 1: DECORADOR
# ============================================================================

def demo_decorator():
    """Demuestra el uso de decoradores."""
    print("\n" + "="*70)
    print("DEMO 1: DECORADOR (Sin modificar funciones)")
    print("="*70)
    
    # Crea el decorador
    benchmark = create_benchmark_decorator(
        output_file="benchmarks/demo_decorator.json",
        verbose=True,
        logger=logger,
    )
    
    # Decora las funciones SIN MODIFICAR SU CÓDIGO
    decorated_demodulate = benchmark(demodulate_fm)
    decorated_psd = benchmark(calculate_psd)
    decorated_filter = benchmark(filter_channel)
    
    # Ejecuta (igual que antes, pero ahora con métricas)
    import numpy as np
    iq_data = np.random.randn(2400000) + 1j * np.random.randn(2400000)
    
    logger.info("Ejecutando demodulación FM...")
    result1 = decorated_demodulate(iq_data, center_freq=104.5e6)
    
    logger.info("Ejecutando PSD...")
    result2 = decorated_psd(iq_data, nperseg=2048)
    
    logger.info("Ejecutando filtrado...")
    result3 = decorated_filter(iq_data, 100e6, 102e6, order=128)
    
    print("\n✅ Resultados guardados en: benchmarks/demo_decorator.json")


# ============================================================================
# DEMOSTRACIÓN 2: CONTEXT MANAGER
# ============================================================================

def demo_context_manager():
    """Demuestra el uso de context managers."""
    print("\n" + "="*70)
    print("DEMO 2: CONTEXT MANAGER (Benchmarkea bloques)")
    print("="*70)
    
    logger.info("Benchmarking de bloque de código IQ...")
    
    with benchmark_context("iq_data_processing", "benchmarks/demo_context.json", logger):
        # Código original sin cambios
        import numpy as np
        time.sleep(0.02)
        data = np.random.randn(1000000)
        result = data ** 2
    
    logger.info("Benchmarking de operación de red...")
    
    with benchmark_context("network_transmission", "benchmarks/demo_context.json", logger):
        time.sleep(0.01)
        # Simula transmisión de datos
        dummy_data = bytes(100000)
    
    print("\n✅ Resultados guardados en: benchmarks/demo_context.json")


# ============================================================================
# DEMOSTRACIÓN 3: ANÁLISIS DE RESULTADOS
# ============================================================================

def demo_analysis():
    """Analiza los benchmarks guardados."""
    print("\n" + "="*70)
    print("DEMO 3: ANÁLISIS DE RESULTADOS")
    print("="*70)
    
    results_file = "benchmarks/demo_decorator.json"
    
    if not Path(results_file).exists():
        logger.warning(f"Archivo {results_file} no existe. Ejecuta demo_decorator() primero.")
        return
    
    # Analiza
    analysis = analyze_benchmarks(results_file)
    
    print("\n📊 ESTADÍSTICAS POR FUNCIÓN:\n")
    for func_name, stats in analysis.items():
        print(f"🔹 {func_name}")
        print(f"   Ejecuciones:      {stats['count']}")
        print(f"   Tiempo (ms):")
        print(f"      • Promedio:    {stats['time_ms']['mean']:.3f} ms")
        print(f"      • Mediana:     {stats['time_ms']['median']:.3f} ms")
        print(f"      • Min/Max:     {stats['time_ms']['min']:.3f} / {stats['time_ms']['max']:.3f} ms")
        print(f"      • Std Dev:     {stats['time_ms']['stdev']:.3f} ms")
        print(f"   Memoria (MB):")
        print(f"      • Promedio:    {stats['memory_mb']['mean']:.3f} MB")
        print(f"      • Pico:        {stats['memory_mb']['max']:.3f} MB")
        print(f"   CPU (%):")
        print(f"      • Promedio:    {stats['cpu_percent']['mean']:.2f}%")
        print(f"      • Máximo:      {stats['cpu_percent']['max']:.2f}%")
        print()


# ============================================================================
# DEMOSTRACIÓN 4: MONITOREO MANUAL
# ============================================================================

def demo_manual_monitoring():
    """Demuestra monitoreo manual con SystemMonitor."""
    print("\n" + "="*70)
    print("DEMO 4: MONITOREO MANUAL (Bajo nivel)")
    print("="*70)
    
    monitor = SystemMonitor()
    
    # Operación 1: Procesamiento de datos
    logger.info("Operación 1: Procesamiento intensivo...")
    monitor.start()
    
    import numpy as np
    data = np.random.randn(10000000)
    result = np.fft.fft(data)
    
    monitor.stop()
    
    elapsed = monitor.get_elapsed_time_ms()
    metrics = monitor.get_metrics_delta()
    
    print(f"\n⏱️  Tiempo total:        {elapsed:.2f} ms")
    print(f"💾 Cambio de memoria:   {metrics.get('memory_mb', 0):.2f} MB")
    print(f"🔴 CPU utilizado:       {metrics.get('cpu_percent', 0):.2f}%")
    print(f"🧵 Threads activos:     {metrics.get('thread_count', 0)}")


# ============================================================================
# DEMOSTRACIÓN 5: COMPARACIÓN ANTES/DESPUÉS
# ============================================================================

def demo_comparison():
    """Compara rendimiento con y sin benchmarking."""
    print("\n" + "="*70)
    print("DEMO 5: COMPARACIÓN - CON vs SIN BENCHMARKING")
    print("="*70)
    
    import numpy as np
    
    def operation():
        """Operación simple para medir overhead."""
        data = np.random.randn(1000000)
        return np.sum(data ** 2)
    
    # Sin benchmarking
    logger.info("Midiendo tiempo sin benchmarking...")
    start = time.perf_counter()
    for _ in range(3):
        operation()
    time_without = (time.perf_counter() - start) * 1000
    
    # Con benchmarking
    logger.info("Midiendo tiempo con benchmarking...")
    benchmark = create_benchmark_decorator(verbose=False)
    monitored_op = benchmark(operation)
    
    start = time.perf_counter()
    for _ in range(3):
        monitored_op()
    time_with = (time.perf_counter() - start) * 1000
    
    overhead = ((time_with - time_without) / time_without) * 100
    
    print(f"\n⚡ SIN benchmarking:      {time_without:.2f} ms (3 ejecuciones)")
    print(f"⚡ CON benchmarking:      {time_with:.2f} ms (3 ejecuciones)")
    print(f"📊 Overhead:            {overhead:+.1f}%")


# ============================================================================
# MAIN
# ============================================================================

def main():
    """Ejecuta todas las demostraciones."""
    
    print("\n" + "="*70)
    print("DEMOSTRACIÓN: MÓDULO DE BENCHMARKING SIN ENSUCIAR EL CÓDIGO")
    print("="*70)
    
    # Crea directorio de benchmarks
    Path("benchmarks").mkdir(exist_ok=True)
    
    # Ejecuta demostraciones
    try:
        demo_decorator()
        demo_context_manager()
        demo_manual_monitoring()
        demo_comparison()
        demo_analysis()
        
        print("\n" + "="*70)
        print("✅ TODAS LAS DEMOSTRACIONES COMPLETADAS")
        print("="*70)
        print("\n📁 Archivos generados:")
        for json_file in Path("benchmarks").glob("*.json"):
            size = json_file.stat().st_size
            print(f"   📄 {json_file.name} ({size} bytes)")
        
        print("\n📚 Próximos pasos:")
        print("   1. Lee BENCHMARKING_GUIDE.md para integración en tu código")
        print("   2. Agrega decoradores @benchmark a tus funciones críticas")
        print("   3. Ejecuta y analiza los resultados")
        print("   4. ¡Sin modificar tu código original!")
        
    except Exception as e:
        logger.error(f"Error durante demostración: {e}", exc_info=True)
        sys.exit(1)


if __name__ == '__main__':
    main()
