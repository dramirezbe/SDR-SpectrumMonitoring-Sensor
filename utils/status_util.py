# utils/status_util.py
"""
Módulo de Estado del Dispositivo (Status Module).

Este módulo se encarga de recopilar métricas críticas del hardware, incluyendo:
- Uso de CPU (por núcleo), RAM y Swap.
- Ocupación de disco y temperatura del procesador.
- Latencia de red (Ping) y extracción de logs recientes.
- Metadatos de temporización (NTP, Calibración).

El módulo implementa mecanismos de reintento para garantizar la integridad de los 
datos ante posibles bloqueos de E/S del sistema operativo.
"""

from __future__ import annotations
from dataclasses import dataclass, field, asdict
import time
import os
import subprocess
import logging
from pathlib import Path
from typing import Dict, List, Any
from datetime import datetime

@dataclass
class StatusPost:
    """
    Estructura de datos para el reporte de estado del sensor.

    Esta clase actúa como un DTO (Data Transfer Object) que valida y formatea 
    las métricas para su envío a la API central. Maneja la conversión dinámica 
    de listas de carga de CPU a claves individuales indexadas.

    Attributes:
        mac (str): Dirección MAC del dispositivo.
        ram_mb (int): Memoria RAM en uso (MB).
        swap_mb (int): Memoria Swap en uso (MB).
        disk_mb (int): Espacio de disco en uso (MB).
        temp_c (float): Temperatura actual en grados Celsius.
        total_ram_mb (int): RAM total disponible.
        total_swap_mb (int): Swap total disponible.
        total_disk_mb (int): Capacidad total del disco.
        delta_t_ms (int): Latencia de procesamiento interna.
        ping_ms (float): Latencia de red hacia el servidor.
        timestamp_ms (int): Tiempo Unix del reporte en milisegundos.
        last_kal_ms (int): Timestamp de la última calibración.
        last_ntp_ms (int): Timestamp de la última sincronización horaria.
        logs (str): Fragmento de texto con los logs más recientes.
        cpu_loads (List[float]): Lista interna de cargas por núcleo.
    """
    mac: str
    ram_mb: int
    swap_mb: int
    disk_mb: int
    temp_c: float
    total_ram_mb: int
    total_swap_mb: int
    total_disk_mb: int
    delta_t_ms: int
    ping_ms: float
    timestamp_ms: int
    last_kal_ms: int
    last_ntp_ms: int
    logs: str
    cpu_loads: List[float] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]):
        """
        Constructor personalizado para manejar claves de CPU dinámicas (cpu_0, cpu_1...).

        Args:
            data (Dict[str, Any]): Diccionario plano con métricas y claves cpu_n.

        Returns:
            StatusPost: Instancia de la clase con la lista cpu_loads poblada.
        """
        known_fields = {
            "mac", "ram_mb", "swap_mb", "disk_mb", "temp_c",
            "total_ram_mb", "total_swap_mb", "total_disk_mb",
            "delta_t_ms", "ping_ms", "timestamp_ms",
            "last_kal_ms", "last_ntp_ms", "logs"
        }
        
        init_args = {k: v for k, v in data.items() if k in known_fields}
        obj = cls(**init_args)
        
        # Filtrar y ordenar las claves de CPU dinámicamente.
        cpu_keys = [k for k in data.keys() if k.startswith("cpu_") and k[4:].isdigit()]
        cpu_keys.sort(key=lambda x: int(x.split('_')[1]))
        
        obj.cpu_loads = [data[k] for k in cpu_keys]
        return obj

    def to_dict(self) -> Dict[str, Any]:
        """
        Convierte la instancia a un diccionario plano para serialización JSON.

        Aplana la lista `cpu_loads` de nuevo a claves individuales (cpu_0, cpu_1, etc.)
        para cumplir con el contrato de la API.

        Returns:
            Dict[str, Any]: Diccionario listo para ser enviado por HTTP.
        """
        data = asdict(self)
        
        if "cpu_loads" in data:
            del data["cpu_loads"]
            
        for idx, usage in enumerate(self.cpu_loads):
            data[f"cpu_{idx}"] = usage
            
        return data

class StatusDevice:
    """
    Controlador para la consulta de métricas del sistema operativo.

    Utiliza los sistemas de archivos virtuales /proc y /sys para obtener 
    información del hardware sin necesidad de herramientas externas pesadas.
    """
    def __init__(self, disk_path: Path = Path('/'),
                 logs_dir: Path = (Path.cwd() / "Logs"),
                 logger=logging.getLogger(__name__)):
        """
        Args:
            disk_path (Path): Punto de montaje del disco a monitorear.
            logs_dir (Path): Carpeta donde se almacenan los archivos .log.
            logger (logging.Logger): Instancia para registro de errores.
        """
        self._log = logger
        self.disk_path = disk_path
        self.disk_path_str = str(disk_path)
        self.logs_dir = logs_dir

    def get_status_snapshot(self, 
                            delta_t_ms: int,
                            last_kal_ms: int,
                            last_ntp_ms: int,
                            timestamp_ms: int,
                            mac: str = "",
                            ping_ip: str = "8.8.8.8") -> Dict[str, Any]:
        """
        Genera una captura completa del estado actual del sistema.

        Coordina todas las subrutinas de recolección (CPU, RAM, Disco, Red, Logs) 
        y empaqueta el resultado en el formato estricto definido por StatusPost.

        Args:
            delta_t_ms (int): Latencia de procesamiento actual.
            last_kal_ms (int): Tiempo desde la última calibración.
            last_ntp_ms (int): Tiempo desde la última sincronía NTP.
            timestamp_ms (int): Tiempo actual del sistema.
            mac (str): Dirección MAC del sensor.
            ping_ip (str): IP de destino para medir latencia de red.

        Returns:
            Dict[str, Any]: Snapshot completo serializado como diccionario.
        """
        snapshot = {}

        # 1. Metadatos estáticos y temporales.
        snapshot["mac"] = mac
        snapshot["timestamp_ms"] = timestamp_ms
        snapshot["delta_t_ms"] = delta_t_ms
        snapshot["last_kal_ms"] = last_kal_ms
        snapshot["last_ntp_ms"] = last_ntp_ms

        # 2. CPU: Recolección y aplanamiento.
        cpu_data = self.get_cpu_percent()
        cpu_list = cpu_data.get("cpu", [])[:4] 
        for idx, usage in enumerate(cpu_list):
            snapshot[f"cpu_{idx}"] = usage

        # 3. Métricas de memoria dinámica.
        snapshot.update(self.get_ram_swap_mb())
        snapshot.update(self.get_disk())
        snapshot.update(self.get_temp_c())

        # 4. Capacidades totales de hardware.
        totals_mem = self.get_total_ram_swap_mb()
        snapshot["total_ram_mb"] = totals_mem.get("ram_mb") or 0
        snapshot["total_swap_mb"] = totals_mem.get("swap_mb") or 0
        snapshot["total_disk_mb"] = self.get_total_disk().get("disk_mb") or 0

        # 5. Red y Logs.
        snapshot.update(self.get_ping_latency(ping_ip))
        _, _, logs_text = self.get_logs()
        snapshot["logs"] = logs_text

        return StatusPost.from_dict(snapshot).to_dict()

    def get_cpu_percent(self) -> Dict[str, List[float]]:
        """
        Calcula el uso de CPU leyendo /proc/stat.

        Realiza dos lecturas de los contadores acumulativos (jiffies) con un 
        intervalo de espera para calcular el diferencial de carga.

        

        Returns:
            Dict[str, List[float]]: Diccionario con la clave 'cpu' y lista de porcentajes.
        """
        def read_cpu_lines():
            """Lee contadores acumulativos por núcleo desde el kernel."""
            try:
                with open("/proc/stat", "r") as f:
                    lines = [l for l in f.readlines() if l.startswith("cpu")]
            except Exception:
                return []
                
            parsed = []
            for l in lines[1:]:  # Ignorar la línea agregada inicial.
                parts = l.split()
                if len(parts) < 5:
                    continue
                vals = [int(x) for x in parts[1:]]
                total = sum(vals)
                idle = vals[3] + (vals[4] if len(vals) > 4 else 0) # idle + iowait.
                parsed.append((total, idle))
            return parsed

        try:
            prev = read_cpu_lines()
            if not prev: return {"cpu": []}

            max_tries = 5
            sleep_s = 1.0

            for _ in range(max_tries):
                time.sleep(sleep_s)
                cur = read_cpu_lines()

                if not cur or len(cur) != len(prev):
                    sleep_s = min(sleep_s * 2.0, 1.5)
                    continue

                usage = []
                any_progress = False
                for (t1, i1), (t2, i2) in zip(prev, cur):
                    total_delta = t2 - t1
                    idle_delta = i2 - i1

                    if total_delta > 0:
                        any_progress = True
                        pct = (1.0 - (idle_delta / total_delta)) * 100.0
                        usage.append(round(pct, 3))
                    else:
                        usage.append(0.0)
                
                if any_progress: return {"cpu": usage}
                sleep_s = min(sleep_s * 2.0, 1.5)
            return {"cpu": []}
        except Exception:
            return {"cpu": []}

    def get_ram_swap_mb(self) -> Dict[str, int]:
        """
        Obtiene el uso actual de RAM y Swap desde /proc/meminfo.

        Returns:
            Dict[str, int]: Megabytes en uso para RAM y Swap.
        """
        mem_total = mem_available = swap_total = swap_free = None
        for _ in range(3):
            try:
                with open("/proc/meminfo", "r") as f:
                    for line in f:
                        if line.startswith("MemTotal:"): mem_total = int(line.split()[1])
                        elif line.startswith("MemAvailable:"): mem_available = int(line.split()[1])
                        elif line.startswith("SwapTotal:"): swap_total = int(line.split()[1])
                        elif line.startswith("SwapFree:"): swap_free = int(line.split()[1])
                    if mem_total is not None: break
            except Exception:
                time.sleep(0.05)

        ram_mb = (mem_total - mem_available) // 1024 if mem_total and mem_available else 0
        swap_mb = (swap_total - swap_free) // 1024 if swap_total and swap_free else 0
        return {"ram_mb": ram_mb, "swap_mb": swap_mb}

    def get_total_ram_swap_mb(self) -> Dict[str, int]:
        """Obtiene las capacidades máximas de RAM y Swap."""
        mem_total = swap_total = None
        for _ in range(3):
            try:
                with open("/proc/meminfo", "r") as f:
                    for line in f:
                        if line.startswith("MemTotal:"): mem_total = int(line.split()[1])
                        elif line.startswith("SwapTotal:"): swap_total = int(line.split()[1])
                if mem_total is not None: break
            except Exception:
                time.sleep(0.05)

        return {
            "ram_mb": mem_total // 1024 if mem_total else 0,
            "swap_mb": swap_total // 1024 if swap_total else 0,
        }

    def get_disk(self) -> dict:
        """Calcula el espacio ocupado en disco mediante statvfs."""
        try:
            st = os.statvfs(self.disk_path_str)
            used_mb = ((st.f_blocks - st.f_bfree) * st.f_frsize) // (1024 * 1024)
            return {"disk_mb": used_mb}
        except Exception:
             return {"disk_mb": 0}

    def get_total_disk(self) -> dict:
        """Calcula el tamaño total de la partición de disco."""
        try:
            st = os.statvfs(self.disk_path_str)
            total_mb = (st.f_blocks * st.f_frsize) // (1024 * 1024)
            return {"disk_mb": total_mb}
        except Exception:
            return {"disk_mb": 0}

    def get_temp_c(self) -> Dict[str, float]:
        """
        Lee la temperatura del procesador desde thermal_zone.

        Returns:
            Dict[str, float]: Temperatura en grados Celsius. -1.0 si falla.
        """
        path = "/sys/class/thermal/thermal_zone0/temp"
        for _ in range(3):
            try:
                with open(path, "r") as f:
                    content = f.read().strip()
                    if content:
                        return {"temp_c": int(content) / 1000.0}
            except Exception:
                time.sleep(0.05)
        return {"temp_c": -1.0}

    def get_ping_latency(self, ip: str) -> Dict[str, float]:
        """
        Mide la latencia de red hacia un host remoto.

        Args:
            ip (str): Dirección IP o dominio a pinguear.

        Returns:
            Dict[str, float]: Tiempo de respuesta en milisegundos.
        """
        cmd = ["ping", "-c", "1", "-W", "1", ip]
        try:
            output = subprocess.check_output(cmd, stderr=subprocess.STDOUT, text=True)
            for line in output.splitlines():
                if "time=" in line:
                    latency = float(line.split("time=")[1].split()[0])
                    return {"ping_ms": latency}
        except Exception:
            pass
        return {"ping_ms": -1.0}

    def get_logs(self):
        """
        Extrae las últimas 10 líneas de log relevantes basándose en el nombre del archivo.
        Formato esperado: DD-MM-YYYY_HH:MM:SS_tipo.log
        """
        result_logs = "Sistema operando normalmente"
        max_lines = 10
        collected_lines: List[str] = []

        if not self.logs_dir.exists():
            return None, None, result_logs

        log_files = []
        for p in self.logs_dir.glob("*.log"):
            try:
                # Separamos el nombre: ['27-12-2025', '09:32:10', 'tipo']
                parts = p.stem.split('_')
                if len(parts) >= 2:
                    # Unimos fecha y hora para crear el objeto datetime
                    ts_str = f"{parts[0]}_{parts[1]}"
                    # El formato es Día-Mes-Año_Hora:Minuto:Segundo
                    dt = datetime.strptime(ts_str, "%d-%m-%Y_%H:%M:%S")
                    log_files.append((dt, p))
            except (ValueError, IndexError):
                # Ignora archivos que no cumplan el formato de fecha exacto
                continue
        
        # Ordenamos por el objeto datetime (del más nuevo al más viejo)
        log_files.sort(key=lambda x: x[0], reverse=True)

        for _, p in log_files:
            try:
                # Leemos las líneas y filtramos
                lines = p.read_text(encoding="utf-8", errors="ignore").splitlines()
                valid_lines = [line for line in lines if "[[OK]]" not in line]
                
                if not valid_lines:
                    continue

                # Agregamos las líneas nuevas AL PRINCIPIO de nuestra lista 
                # para que las más recientes queden al final del texto resultante
                collected_lines = valid_lines + collected_lines
                
                if len(collected_lines) >= max_lines:
                    collected_lines = collected_lines[-max_lines:]
                    break
            except Exception as e:
                self._log.error(f"Error al leer log {p}: {e}")
                continue

        if collected_lines:
            result_logs = "\n".join(collected_lines)
            
        return None, None, result_logs