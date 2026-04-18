#!/usr/bin/env python3
"""
Módulo de Benchmarking Limpio para SDR Sensor.

Proporciona decoradores y context managers para medir:
  - Tiempo de procesamiento (ms)
  - CPU por núcleo (%)
  - Uso de memoria (MB)
  - Escrituras en disco (bytes)
  - Temperatura del sistema (°C)
  - Parámetros de entrada de la función

Sin modificar el código original.
"""

import functools
import time
import os
import json
import psutil
import threading
import logging
from contextlib import contextmanager
from dataclasses import dataclass, asdict, field
from typing import Any, Dict, Optional, Callable, List
from datetime import datetime
from pathlib import Path
import inspect

# ============================================================================
# TIPOS DE DATOS
# ============================================================================

@dataclass
class SystemMetrics:
    """Métricas de sistema capturadas durante benchmarking."""
    timestamp: str
    function_name: str
    parameters: Dict[str, Any]
    execution_time_ms: float
    cpu_percent: float
    cpu_count: int
    memory_usage_mb: float
    memory_percent: float
    disk_writes_bytes: int
    disk_reads_bytes: int
    temperature_celsius: Optional[float] = None
    thread_count: int = 0
    context_switches: int = 0
    
    def to_dict(self) -> Dict[str, Any]:
        """Convierte a diccionario para serialización."""
        return asdict(self)


# ============================================================================
# UTILIDADES DE MONITOREO DE SISTEMA
# ============================================================================

class SystemMonitor:
    """Monitor de métricas del sistema en tiempo real."""
    
    def __init__(self, process: Optional[psutil.Process] = None):
        """
        Inicializa el monitor.
        
        Args:
            process: Proceso a monitorear (defecto: proceso actual)
        """
        self.process = process or psutil.Process()
        self.start_time = None
        self.start_metrics = None
        self.end_metrics = None
        
    def start(self) -> None:
        """Captura métricas iniciales."""
        self.start_time = time.perf_counter()
        self.start_metrics = self._capture_metrics()
        
    def stop(self) -> None:
        """Captura métricas finales."""
        self.end_metrics = self._capture_metrics()
        
    def _capture_metrics(self) -> Dict[str, Any]:
        """Captura snapshot de métricas del proceso."""
        try:
            io_counters = self.process.io_counters()
        except (psutil.AccessDenied, psutil.NoSuchProcess):
            io_counters = None
            
        return {
            'cpu_percent': self.process.cpu_percent(interval=0.01),
            'memory_info': self.process.memory_info(),
            'io_counters': io_counters,
            'num_threads': self.process.num_threads(),
            'num_ctx_switches': self.process.num_ctx_switches(),
        }
    
    def get_elapsed_time_ms(self) -> float:
        """Retorna tiempo transcurrido en milisegundos."""
        if self.start_time is None:
            return 0.0
        end = time.perf_counter()
        return (end - self.start_time) * 1000.0
    
    def get_metrics_delta(self) -> Dict[str, Any]:
        """Calcula deltas entre start y stop."""
        if not self.start_metrics or not self.end_metrics:
            return {}
        
        delta = {}
        
        # CPU
        delta['cpu_percent'] = self.end_metrics['cpu_percent']
        
        # Memoria
        start_mem = self.start_metrics['memory_info'].rss
        end_mem = self.end_metrics['memory_info'].rss
        delta['memory_mb'] = (end_mem - start_mem) / (1024 ** 2)
        delta['memory_percent'] = self.process.memory_percent()
        
        # Disk I/O
        delta['disk_reads'] = 0
        delta['disk_writes'] = 0
        if self.start_metrics['io_counters'] and self.end_metrics['io_counters']:
            delta['disk_reads'] = (
                self.end_metrics['io_counters'].read_bytes -
                self.start_metrics['io_counters'].read_bytes
            )
            delta['disk_writes'] = (
                self.end_metrics['io_counters'].write_bytes -
                self.start_metrics['io_counters'].write_bytes
            )
        
        # Threads y context switches
        delta['thread_count'] = self.end_metrics['num_threads']
        cs_start = self.start_metrics['num_ctx_switches']
        cs_end = self.end_metrics['num_ctx_switches']
        delta['ctx_switches'] = (cs_end.voluntary - cs_start.voluntary +
                                 cs_end.involuntary - cs_start.involuntary)
        
        return delta


def get_system_temperature() -> Optional[float]:
    """
    Obtiene la temperatura del sistema en °C.
    
    Returns:
        Temperatura en °C o None si no está disponible.
    """
    try:
        temps = psutil.sensors_temperatures()
        if not temps:
            return None
        
        # Intenta obtener la temperatura del CPU principal
        for name, entries in temps.items():
            if entries:
                return entries[0].current
    except (AttributeError, OSError):
        pass
    
    return None


def get_cpu_per_core() -> List[float]:
    """
    Obtiene uso de CPU por núcleo (%).
    
    Returns:
        Lista con porcentaje de CPU por núcleo.
    """
    return psutil.cpu_percent(interval=0.01, percpu=True)


def extract_function_parameters(func: Callable, args: tuple, kwargs: dict) -> Dict[str, Any]:
    """
    Extrae los parámetros de una función llamada.
    
    Args:
        func: Función llamada
        args: Argumentos posicionales
        kwargs: Argumentos nombrados
    
    Returns:
        Diccionario con nombres y valores de parámetros
    """
    try:
        sig = inspect.signature(func)
        bound = sig.bind_partial(*args, **kwargs)
        bound.apply_defaults()
        
        # Serializa los parámetros
        params = {}
        for name, value in bound.arguments.items():
            if name == 'self' or name == 'cls':
                continue
            
            # Intenta serializar el valor
            try:
                json.dumps(value)
                params[name] = value
            except (TypeError, ValueError):
                params[name] = str(value)
        
        return params
    except Exception:
        return {}


# ============================================================================
# DECORADOR PRINCIPAL
# ============================================================================

class BenchmarkDecorator:
    """Decorador para benchmarking de funciones."""
    
    def __init__(
        self,
        output_file: Optional[str] = None,
        log_params: bool = True,
        verbose: bool = False,
        logger: Optional[logging.Logger] = None,
    ):
        """
        Inicializa el decorador.
        
        Args:
            output_file: Archivo JSON/CSV para guardar resultados
            log_params: Si True, registra parámetros de entrada
            verbose: Si True, imprime información en tiempo real
            logger: Logger personalizado (usa logging.getLogger() por defecto)
        """
        self.output_file = output_file
        self.log_params = log_params
        self.verbose = verbose
        self.logger = logger or logging.getLogger(__name__)
        self.results: List[SystemMetrics] = []
        
    def __call__(self, func: Callable) -> Callable:
        """Envuelve la función con benchmarking."""
        
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            monitor = SystemMonitor()
            monitor.start()
            
            try:
                # Ejecuta la función
                result = func(*args, **kwargs)
            finally:
                monitor.stop()
            
            # Captura parámetros
            params = {}
            if self.log_params:
                params = extract_function_parameters(func, args, kwargs)
            
            # Obtiene métricas
            metrics_delta = monitor.get_metrics_delta()
            
            metrics = SystemMetrics(
                timestamp=datetime.now().isoformat(),
                function_name=func.__name__,
                parameters=params,
                execution_time_ms=monitor.get_elapsed_time_ms(),
                cpu_percent=metrics_delta.get('cpu_percent', 0.0),
                cpu_count=psutil.cpu_count(),
                memory_usage_mb=metrics_delta.get('memory_mb', 0.0),
                memory_percent=metrics_delta.get('memory_percent', 0.0),
                disk_writes_bytes=metrics_delta.get('disk_writes', 0),
                disk_reads_bytes=metrics_delta.get('disk_reads', 0),
                temperature_celsius=get_system_temperature(),
                thread_count=metrics_delta.get('thread_count', 0),
                context_switches=metrics_delta.get('ctx_switches', 0),
            )
            
            self._record_metrics(metrics)
            
            if self.verbose:
                self._print_metrics(metrics)
            
            return result
        
        return wrapper
    
    def _record_metrics(self, metrics: SystemMetrics) -> None:
        """Registra las métricas."""
        self.results.append(metrics)
        
        if self.output_file:
            self._save_to_file(metrics)
    
    def _save_to_file(self, metrics: SystemMetrics) -> None:
        """Guarda las métricas a archivo."""
        output_path = Path(self.output_file)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        if self.output_file.endswith('.json'):
            self._save_json(metrics, output_path)
        else:
            self._save_csv(metrics, output_path)
    
    def _save_json(self, metrics: SystemMetrics, path: Path) -> None:
        """Guarda en JSON (append)."""
        try:
            if path.exists():
                with open(path, 'r') as f:
                    data = json.load(f)
            else:
                data = []
            
            data.append(metrics.to_dict())
            
            with open(path, 'w') as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            self.logger.error(f"Error saving JSON metrics: {e}")
    
    def _save_csv(self, metrics: SystemMetrics, path: Path) -> None:
        """Guarda en CSV (append con headers)."""
        try:
            import csv
            
            file_exists = path.exists()
            with open(path, 'a', newline='') as f:
                writer = csv.DictWriter(f, fieldnames=metrics.to_dict().keys())
                
                if not file_exists:
                    writer.writeheader()
                
                # Simplifica parameters a JSON string
                row = metrics.to_dict()
                row['parameters'] = json.dumps(row['parameters'])
                writer.writerow(row)
        except Exception as e:
            self.logger.error(f"Error saving CSV metrics: {e}")
    
    def _print_metrics(self, metrics: SystemMetrics) -> None:
        """Imprime las métricas en formato legible."""
        output = (
            f"\n{'='*70}\n"
            f"Benchmarking: {metrics.function_name}\n"
            f"{'='*70}\n"
            f"  ⏱️  Tiempo:          {metrics.execution_time_ms:.2f} ms\n"
            f"  💾 Memoria:        {metrics.memory_usage_mb:+.2f} MB ({metrics.memory_percent:.2f}%)\n"
            f"  🔴 CPU:            {metrics.cpu_percent:.2f}% ({metrics.cpu_count} cores)\n"
            f"  💿 Escrituras:     {metrics.disk_writes_bytes} bytes\n"
            f"  📖 Lecturas:       {metrics.disk_reads_bytes} bytes\n"
            f"  🌡️  Temperatura:    {metrics.temperature_celsius}°C\n"
            f"  🧵 Threads:        {metrics.thread_count}\n"
            f"  🔄 Context Sw:     {metrics.context_switches}\n"
            f"{'='*70}\n"
        )
        self.logger.info(output)


# ============================================================================
# CONTEXT MANAGER
# ============================================================================

@contextmanager
def benchmark_context(
    name: str = "code_block",
    output_file: Optional[str] = None,
    logger: Optional[logging.Logger] = None,
):
    """
    Context manager para benchmarking de bloques de código.
    
    Uso:
        with benchmark_context("mi_operacion", "metrics.json"):
            # código a benchmarkear
    
    Args:
        name: Nombre del bloque de código
        output_file: Archivo para guardar resultados
        logger: Logger personalizado
    """
    monitor = SystemMonitor()
    monitor.start()
    
    try:
        yield monitor
    finally:
        monitor.stop()
        
        params_dict = {}
        metrics_delta = monitor.get_metrics_delta()
        
        metrics = SystemMetrics(
            timestamp=datetime.now().isoformat(),
            function_name=name,
            parameters=params_dict,
            execution_time_ms=monitor.get_elapsed_time_ms(),
            cpu_percent=metrics_delta.get('cpu_percent', 0.0),
            cpu_count=psutil.cpu_count(),
            memory_usage_mb=metrics_delta.get('memory_mb', 0.0),
            memory_percent=metrics_delta.get('memory_percent', 0.0),
            disk_writes_bytes=metrics_delta.get('disk_writes', 0),
            disk_reads_bytes=metrics_delta.get('disk_reads', 0),
            temperature_celsius=get_system_temperature(),
            thread_count=metrics_delta.get('thread_count', 0),
            context_switches=metrics_delta.get('ctx_switches', 0),
        )
        
        if logger:
            logger.info(f"Benchmark [{name}] - {metrics.execution_time_ms:.2f} ms")
        
        if output_file:
            _save_metrics_to_file(metrics, output_file, logger)


def _save_metrics_to_file(
    metrics: SystemMetrics,
    output_file: str,
    logger: Optional[logging.Logger] = None
) -> None:
    """Helper para guardar métricas a archivo."""
    output_path = Path(output_file)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    if output_file.endswith('.json'):
        try:
            if output_path.exists():
                with open(output_path, 'r') as f:
                    data = json.load(f)
            else:
                data = []
            
            data.append(metrics.to_dict())
            
            with open(output_path, 'w') as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            if logger:
                logger.error(f"Error saving JSON metrics: {e}")


# ============================================================================
# FUNCIONES DE ANÁLISIS
# ============================================================================

def analyze_benchmarks(results_file: str) -> Dict[str, Any]:
    """
    Analiza resultados de benchmarking guardados.
    
    Args:
        results_file: Archivo JSON con resultados
    
    Returns:
        Diccionario con análisis estadísticos
    """
    import statistics
    
    try:
        with open(results_file, 'r') as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    
    # Agrupa por función
    by_function = {}
    for record in data:
        func_name = record.get('function_name', 'unknown')
        if func_name not in by_function:
            by_function[func_name] = []
        by_function[func_name].append(record)
    
    # Calcula estadísticas
    analysis = {}
    for func_name, records in by_function.items():
        times = [r['execution_time_ms'] for r in records]
        analysis[func_name] = {
            'count': len(records),
            'time_ms': {
                'mean': statistics.mean(times),
                'median': statistics.median(times),
                'min': min(times),
                'max': max(times),
                'stdev': statistics.stdev(times) if len(times) > 1 else 0,
            },
            'memory_mb': {
                'mean': statistics.mean([r['memory_usage_mb'] for r in records]),
                'max': max([r['memory_usage_mb'] for r in records]),
            },
            'cpu_percent': {
                'mean': statistics.mean([r['cpu_percent'] for r in records]),
                'max': max([r['cpu_percent'] for r in records]),
            },
        }
    
    return analysis


# ============================================================================
# FACTORÍA
# ============================================================================

def create_benchmark_decorator(
    output_file: str = "benchmarks/results.json",
    verbose: bool = False,
    logger: Optional[logging.Logger] = None,
) -> BenchmarkDecorator:
    """
    Factory para crear decoradores de benchmarking.
    
    Args:
        output_file: Archivo para guardar resultados
        verbose: Si True, imprime información detallada
        logger: Logger personalizado
    
    Returns:
        Decorador configurado
    """
    return BenchmarkDecorator(
        output_file=output_file,
        log_params=True,
        verbose=verbose,
        logger=logger,
    )


if __name__ == '__main__':
    # Ejemplo de uso
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger(__name__)
    
    benchmark = create_benchmark_decorator(
        output_file='/tmp/benchmark_test.json',
        verbose=True,
        logger=logger,
    )
    
    @benchmark
    def example_function(n: int = 1000000):
        """Función de ejemplo para benchmarking."""
        total = 0
        for i in range(n):
            total += i ** 2
        return total
    
    # Ejecuta ejemplo
    result = example_function(1000000)
    print(f"Result: {result}")
