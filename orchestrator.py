#!/usr/bin/env python3
# orchestrator.py

"""
Módulo Orquestador Principal.

Este módulo actúa como el motor central del sistema, coordinando la transición entre 
diferentes estados (IDLE, REALTIME, CAMPAIGN, KALIBRATING). Gestiona la descarga de 
configuraciones desde la API, la lógica de adquisición de datos en tiempo real y 
la programación de campañas mediante tareas cron.
"""

import cfg
log = cfg.set_logger()
from utils import (
    RequestClient, ZmqPairController, ServerRealtimeConfig, 
    FilterConfig, ShmStore, ElapsedTimer
)
from functions import (
    format_data_for_upload, CronSchedulerCampaign, GlobalSys, 
    SysState, AcquireDual
)

import sys
import asyncio
from dataclasses import asdict, dataclass
import time
import subprocess
import logging
import os
import shlex
import signal
import threading
import time 
from typing import Dict, List, Optional, Tuple


# -----------------------------------------------------------------------------
# Process helpers
# -----------------------------------------------------------------------------

@dataclass
class ManagedProc:
    name: str
    argv: List[str]
    env: Dict[str, str]
    proc: subprocess.Popen
    log_thread: threading.Thread


def _parse_exec_env(exec_str: str) -> Tuple[List[str], Dict[str, str]]:
    """
    Soporta valores como:
      "/opt/venv/bin/python3"
      "PYTHONUNBUFFERED=1 /opt/venv/bin/python3"
    """
    tokens = shlex.split(exec_str)
    if not tokens:
        raise ValueError("cfg.PYTHON_ENV_STR is empty")

    env = os.environ.copy()
    argv: List[str] = []

    for tok in tokens:
        if "=" in tok and not argv:
            k, v = tok.split("=", 1)
            if k and all(ch not in k for ch in " /\\"):
                env[k] = v
                continue
        argv.append(tok)

    if not argv:
        raise ValueError(f"Could not parse executable from: {exec_str!r}")

    return argv, env


def _build_python_cmd(exec_str: str, script_name: str) -> Tuple[List[str], Dict[str, str]]:
    base_argv, env = _parse_exec_env(exec_str)
    argv = [*base_argv, "-u", script_name]
    return argv, env



def _read_proc_cmdline(pid: int) -> str:
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            raw = f.read()
        return raw.replace(b"\x00", b" ").decode("utf-8", errors="ignore").strip()
    except Exception:
        return ""


def _read_proc_state(pid: int) -> str:
    try:
        with open(f"/proc/{pid}/stat", "r", encoding="utf-8", errors="ignore") as f:
            parts = f.read().split()
        return parts[2] if len(parts) > 2 else ""
    except Exception:
        return ""


def _iter_matching_pids(match_terms: List[str]) -> List[int]:
    found: List[int] = []
    self_pid = os.getpid()

    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue

        pid = int(entry)
        if pid == self_pid:
            continue

        if _read_proc_state(pid) == "Z":
            continue

        cmd = _read_proc_cmdline(pid)
        if not cmd:
            continue

        if all(term in cmd for term in match_terms):
            found.append(pid)

    return found


def _kill_pid_or_group(pid: int, sig: int, log: logging.Logger) -> None:
    try:
        pgid = os.getpgid(pid)
    except ProcessLookupError:
        return
    except Exception as e:
        log.warning(f"[PROC] getpgid({pid}) failed: {e}")
        pgid = None

    try:
        current_pgid = os.getpgrp()
    except Exception:
        current_pgid = None

    try:
        if pgid and pgid != current_pgid:
            os.killpg(pgid, sig)
        else:
            os.kill(pid, sig)
    except ProcessLookupError:
        return
    except Exception as e:
        log.warning(f"[PROC] signal {sig} failed for pid={pid}, pgid={pgid}: {e}")


def cleanup_stale_processes(match_terms: List[str], log: logging.Logger, term_timeout: float = 2.0) -> None:
    pids = _iter_matching_pids(match_terms)
    if not pids:
        return

    log.warning(f"[PROC] Cleaning stale processes for {match_terms}: {pids}")

    for pid in pids:
        _kill_pid_or_group(pid, signal.SIGTERM, log)

    end = time.monotonic() + term_timeout
    while time.monotonic() < end:
        alive = [pid for pid in pids if os.path.exists(f"/proc/{pid}")]
        if not alive:
            return
        time.sleep(0.1)

    alive = [pid for pid in pids if os.path.exists(f"/proc/{pid}")]
    if alive:
        log.warning(f"[PROC] Force killing stale processes: {alive}")
        for pid in alive:
            _kill_pid_or_group(pid, signal.SIGKILL, log)


def _pump_process_output(proc: subprocess.Popen, name: str, log: logging.Logger) -> None:
    try:
        assert proc.stdout is not None
        for line in iter(proc.stdout.readline, ""):
            if not line:
                break
            log.info(f"[{name}] {line.rstrip()}")
    except Exception as e:
        log.warning(f"[{name}] log pump stopped: {e}")
    finally:
        try:
            if proc.stdout:
                proc.stdout.close()
        except Exception:
            pass


def start_managed_process(
    *,
    name: str,
    argv: List[str],
    env: Dict[str, str],
    log: logging.Logger,
    stale_match_terms: Optional[List[str]] = None,
) -> ManagedProc:
    if stale_match_terms:
        cleanup_stale_processes(stale_match_terms, log)

    log.info(f"[PROC] Starting {name}: {' '.join(shlex.quote(x) for x in argv)}")

    proc = subprocess.Popen(
        argv,
        shell=False,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        universal_newlines=True,
        close_fds=True,
        preexec_fn=os.setsid,   # new session + new process group
        env=env,
    )

    t = threading.Thread(
        target=_pump_process_output,
        args=(proc, name, log),
        name=f"{name}_log_pump",
        daemon=True,
    )
    t.start()

    return ManagedProc(
        name=name,
        argv=argv,
        env=env,
        proc=proc,
        log_thread=t,
    )


def stop_managed_process(mp: Optional[ManagedProc], log: logging.Logger, timeout: float = 2.0) -> None:
    if mp is None:
        return

    proc = mp.proc

    if proc.poll() is not None:
        try:
            proc.wait(timeout=0)
        except Exception:
            pass
        return

    try:
        pgid = os.getpgid(proc.pid)
    except ProcessLookupError:
        pgid = None
    except Exception as e:
        log.warning(f"[PROC] {mp.name}: getpgid failed: {e}")
        pgid = None

    log.info(f"[PROC] Stopping {mp.name} pid={proc.pid} pgid={pgid}")

    try:
        if pgid:
            os.killpg(pgid, signal.SIGTERM)
        else:
            proc.terminate()
    except ProcessLookupError:
        pass
    except Exception as e:
        log.warning(f"[PROC] {mp.name}: SIGTERM failed: {e}")

    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        log.warning(f"[PROC] {mp.name}: TERM timeout, sending SIGKILL")
        try:
            if pgid:
                os.killpg(pgid, signal.SIGKILL)
            else:
                proc.kill()
        except ProcessLookupError:
            pass
        except Exception as e:
            log.warning(f"[PROC] {mp.name}: SIGKILL failed: {e}")
        finally:
            try:
                proc.wait(timeout=1.0)
            except Exception:
                pass
    except Exception as e:
        log.warning(f"[PROC] {mp.name}: wait() failed: {e}")

    script_name = mp.argv[-1] if mp.argv else mp.name
    cleanup_stale_processes([script_name], log)


WEBRTC_ARGV, WEBRTC_ENV = _build_python_cmd(cfg.PYTHON_ENV_STR, "server_webrtc.py")


# --- CONFIG FETCHING ---
def fetch_realtime_config(client):
    """
    Descarga y valida la configuración de tiempo real desde el servidor.

    Realiza una petición GET al endpoint de configuración, calcula la latencia 
    de red (delta_t) y mapea la respuesta JSON a objetos de configuración 
    especializados para filtrado y demodulación.

    Args:
        client (RequestClient): Cliente HTTP para realizar la consulta.

    Returns:
        tuple: Un conjunto de tres elementos:
            * dict: Configuración mapeada como diccionario (asdict). Vacío si falla.
            * Response: Objeto de respuesta HTTP completo.
            * int: Latencia de la petición en milisegundos (delta_t_ms).
    """
    delta_t_ms = 0 
    try:
        start_delta_t = time.perf_counter()
        _, resp = client.get(cfg.REALTIME_URL)
        end_delta_t = time.perf_counter()
        delta_t_ms = int((end_delta_t - start_delta_t) * 1000)
        
        if resp is None or resp.status_code != 200:
            return {}, resp, delta_t_ms 
        
        #DEBUG
        log.debug(f"---REALTIME--- :{resp.json()}")
        
        try:
            json_payload = resp.json()
        except Exception:
            return {}, resp, delta_t_ms 
        
        if not json_payload:
            return {}, resp, delta_t_ms

        if json_payload.get("center_freq_hz") == 0:
            return {}, resp, delta_t_ms
        
        ppm_err_shm = None
        # DISABLED!!!!!! WARNNING!!!! DISABLED!!!! (FOR DEBUGGING)
        ppm_err_shm = ShmStore().consult_persistent("ppm_error")

        config_obj = ServerRealtimeConfig(
                method_psd="pfb",
                center_freq_hz=int(json_payload.get("center_freq_hz")), 
                sample_rate_hz=int(json_payload.get("sample_rate_hz")),
                rbw_hz=int(json_payload.get("rbw_hz")),
                window=json_payload.get("window"),
                overlap=float(json_payload.get("overlap")),
                lna_gain=int(json_payload.get("lna_gain")),
                vga_gain=int(json_payload.get("vga_gain")),
                antenna_amp=bool(json_payload.get("antenna_amp")),
                antenna_port=int(json_payload.get("antenna_port")), 
                ppm_error=float(ppm_err_shm) if ppm_err_shm else 0.0,
                cooldown_request=float(json_payload.get("cooldown_request", 2.0))
            )
        
        if json_payload.get("demodulation") in ["fm","am"]:
            config_obj.demodulation = json_payload.get("demodulation")
        else:
            config_obj.demodulation = None


        if json_payload.get("filter") is not None:
            config_obj.filter = FilterConfig(
                start_freq_hz=int(json_payload.get("filter").get("start_freq_hz")),
                end_freq_hz=int(json_payload.get("filter").get("end_freq_hz")),
            )

        else:
            config_obj.filter = None

        try:
            return asdict(config_obj), resp, delta_t_ms

        except (ValueError, TypeError) as val_err:
            log.error(f"SKIPPING REALTIME: {val_err}")
            return {}, resp, delta_t_ms 

    except Exception as e:
        log.error(f"Error fetching config: {e}")
        return {}, None, 0

# --- HELPER: CALIBRATION ---
async def _perform_calibration_sequence():
    log.info("--------------------------------")
    log.info("🛠️ INICIANDO CALIBRACIÓN PRE-CAMPAÑA")
    previous_state = GlobalSys.current
    GlobalSys.set(SysState.KALIBRATING)
    return_code = 1 #fallback
    
    try:
        controller = ZmqPairController(addr=cfg.IPC_ADDR, is_server=True, verbose=False)
        async with controller as zmq_ctrl:
            log.info("Enviando comando de calibración...")
            await zmq_ctrl.send_command({"calibrate": True})
            
            log.info("Esperando respuesta del motor...")
            response = await zmq_ctrl.wait_for_data()
            if response is None:
                log.warning("✗ Timeout: No se recibió respuesta del motor en 15 segundos")
            else:
                log.info(f"✓ Respuesta recibida: {response}")
                ppm_engine = response.get("ppm_error", None)
                if ppm_engine is None or ppm_engine == 0.0 or ppm_engine == 0:
                    log.warning(f"⚠️ Advertencia: valor inválido de PPM valor: {ppm_engine}. No se actualizará el error de frecuencia.")
                    return_code = 1
                else:
                    return_code = 0

        

        if return_code == 0:
            log.info("✅ Calibración exitosa.")
        else:
            log.warning(f"⚠️ Calibración falló (RC {return_code}). Continuando...")
    except Exception as e:
        log.error(f"❌ Error en secuencia de calibración: {e}")
    finally:
        if GlobalSys.current == SysState.KALIBRATING:
            GlobalSys.set(previous_state if previous_state != SysState.KALIBRATING else SysState.IDLE)

    log.info("--------------------------------")

# --- 1. REALTIME LOGIC (WITH OFFSET & CROP) ---
async def run_realtime_logic(client: RequestClient, store: ShmStore) -> int:
    """
    Gestiona el bucle de ejecución para el modo de tiempo real.

    Establece una conexión ZMQ con el backend de procesamiento (DSP), adquiere
    espectros (con o sin demodulación/offset) y los sube a la API. El bucle
    se mantiene activo hasta que el servidor deja de enviar una configuración válida
    o se alcanza el tiempo de rotación.

    Args:
        client (RequestClient): Cliente para comunicación con la API.
        store (ShmStore): Almacén para guardar el delta de tiempo de red.

    Returns:
        int: Código de estado (0 para fin de ciclo, 1 si el sistema está ocupado).
    """
    log.info("[REALTIME] Entering Sticky Mode (Offset & Crop enabled)...")
    if not GlobalSys.is_idle():
        return 1

    next_config, _, delta_t_ms = fetch_realtime_config(client)
    if not next_config:
        return 0

    GlobalSys.set(SysState.REALTIME)
    webrtc_proc: Optional[ManagedProc] = None
    DEMOD_CFG_SENT = False
    RESET_DEMOD_CFG = False
    store.add_to_persistent("delta_t_ms", delta_t_ms)

    timer_force_rotation = ElapsedTimer()
    timer_force_rotation.init_count(300)

    controller = ZmqPairController(addr=cfg.IPC_ADDR, is_server=True, verbose=False)

    try:
        async with controller as zmq_ctrl:
            acquirer = AcquireDual(controller=zmq_ctrl, log=log)

            log.info("[REALTIME] Connection established. Processing stream...")

            # limpieza preventiva de instancias viejas
            cleanup_stale_processes(["server_webrtc.py"], log)
            cleanup_stale_processes(["systemd-cat", "WEBRTC_SERVER"], log)

            while True:
                if timer_force_rotation.time_elapsed():
                    log.info("[REALTIME] Periodic rotation triggered.")
                    break

                is_demod = bool(next_config.get("demodulation", False))

                # si murió inesperadamente, limpiar handle
                if webrtc_proc is not None and webrtc_proc.proc.poll() is not None:
                    rc = webrtc_proc.proc.returncode
                    log.warning(f"[REALTIME] WebRTC exited unexpectedly rc={rc}")
                    stop_managed_process(webrtc_proc, log, timeout=0.5)
                    webrtc_proc = None

                if is_demod:
                    if webrtc_proc is None:
                        log.info("[REALTIME] Starting WebRTC Server...")
                        webrtc_proc = start_managed_process(
                            name="WEBRTC_SERVER",
                            argv=WEBRTC_ARGV,
                            env=WEBRTC_ENV,
                            log=log,
                            stale_match_terms=["server_webrtc.py"],
                        )

                    DEMOD_CFG_SENT = True
                    dsp_payload = await acquirer.get_corrected_data(next_config)

                else:
                    if webrtc_proc is not None:
                        log.info("[REALTIME] Stopping WebRTC Server...")
                        stop_managed_process(webrtc_proc, log)
                        webrtc_proc = None

                    dsp_payload = await acquirer.get_corrected_data(next_config)

                    if DEMOD_CFG_SENT:
                        RESET_DEMOD_CFG = True
                        DEMOD_CFG_SENT = False

                if RESET_DEMOD_CFG:
                    await zmq_ctrl.send_command({})
                    RESET_DEMOD_CFG = False

                if dsp_payload:
                    final_payload = format_data_for_upload(dsp_payload, log)

                    if final_payload.get("excursion_hz", False):
                        log.info(f"Excursion: {final_payload['excursion_hz']} Hz")
                    if final_payload.get("depth", False):
                        log.info(f"Depth: {final_payload['depth']} %")

                    rc, _ = client.post_json(cfg.DATA_URL, final_payload)
                    if rc != 0:
                        log.warning(f"[REALTIME] Upload failed (RC {rc}).")
                else:
                    log.warning("[REALTIME] Acquisition timeout or DSP error.")

                new_conf, _, dt = fetch_realtime_config(client)
                if not new_conf:
                    log.info("[REALTIME] Stop command received. Breaking.")
                    break

                next_config = new_conf
                store.add_to_persistent("delta_t_ms", dt)

                await asyncio.sleep(0.05)

    except Exception as e:
        log.exception(f"[REALTIME] Critical loop error: {e}")
    finally:
        log.info("[REALTIME] Reverting to IDLE.")

        try:
            stop_managed_process(webrtc_proc, log)
        finally:
            webrtc_proc = None

        cleanup_stale_processes(["server_webrtc.py"], log)
        cleanup_stale_processes(["systemd-cat", "WEBRTC_SERVER"], log)

        GlobalSys.set(SysState.IDLE)

    return 0

# --- 2. CAMPAIGN LOGIC ---
async def run_campaigns_logic(client: RequestClient, store: ShmStore, scheduler: CronSchedulerCampaign) -> int:
    """
    Sincroniza y gestiona las campañas de medición programadas.

    Consulta la lista de campañas pendientes en el servidor. Si hay campañas activas,
    ejecuta una calibración y pone al sistema en modo CAMPAIGN. Se mantiene 
    monitoreando la ventana de tiempo hasta que no queden tareas pendientes.

    Args:
        client (RequestClient): Cliente para comunicación con la API.
        store (ShmStore): Almacén de persistencia.
        scheduler (CronSchedulerCampaign): Gestor de tareas programadas en el sistema.

    Returns:
        int: Código de estado (0 éxito, 1 si el sistema no está IDLE o falla la red).
    """
    def _validate_camp_arr(resp):
        if resp is not None:
            #DEBUG
            log.debug(f"---CAMPAIGNS--- :{resp.json()}")
            camps_arr = resp.json().get("campaigns", [])
            if not camps_arr: return 1, None
            else: return 0, camps_arr
        else: return 1, None

    log.info("[CAMPAIGN] Checking for scheduled campaigns...")
    if not GlobalSys.is_idle(): return 1

    try:
        rc, resp = client.get(cfg.CAMPAIGN_URL)
        if rc != 0: return 1
        
        err, camps_arr = _validate_camp_arr(resp)
        if err or not camps_arr: return 1
            
        is_active = scheduler.sync_jobs(camps_arr, cfg.get_time_ms(), store)
        
        if is_active:
            # DISABLED!!!!!! WARNNING!!!! DISABLED!!!! (FOR DEBUGGING)
            await _perform_calibration_sequence()
            GlobalSys.set(SysState.CAMPAIGN)
            
            while True:
                await asyncio.sleep(cfg.INTERVAL_REQUEST_CAMPAIGNS_S)
                rc, resp = client.get(cfg.CAMPAIGN_URL)
                if rc != 0: break 
                
                err, camps_arr = _validate_camp_arr(resp)
                if err or not camps_arr: return 1
                
                if not scheduler.sync_jobs(camps_arr, cfg.get_time_ms(), store):
                    log.info("[CAMPAIGN] Window closed. Exiting campaign mode.")
                    break
    except Exception as e:
        log.error(f"[CAMPAIGN] Error: {e}")
    finally:
        GlobalSys.set(SysState.IDLE)
    return 0

# --- 3. MAIN LOOP ---
async def main() -> int:
    """
    Punto de entrada principal del orquestador.

    Inicializa los servicios base (Store, Client, Scheduler) y entra en un bucle 
    infinito. Utiliza temporizadores (`ElapsedTimer`) para decidir cuándo consultar 
    la API por nuevas configuraciones de tiempo real o campañas programadas.

    Returns:
        int: Código de salida del script.
    """
    try:
        time.sleep(1) 
        store = ShmStore()
        client = RequestClient(cfg.API_URL, mac_wifi=cfg.get_mac(), timeout=(5, 15), verbose=True, logger=log)
        scheduler = CronSchedulerCampaign(
            poll_interval_s=cfg.INTERVAL_REQUEST_CAMPAIGNS_S, 
            python_env=cfg.PYTHON_ENV_STR,
            cmd=str((cfg.PROJECT_ROOT / "campaign_runner.py").absolute()), 
            logger=log
        )
        
        tim_check_realtime = ElapsedTimer()
        tim_check_campaign = ElapsedTimer()
        
        tim_check_realtime.init_count(cfg.INTERVAL_REQUEST_REALTIME_S)
        tim_check_campaign.init_count(cfg.INTERVAL_REQUEST_CAMPAIGNS_S)
        
        log.info("Orchestrator online. Monitoring tasks...")

        while True:
            if GlobalSys.is_idle() and tim_check_realtime.time_elapsed():
                await run_realtime_logic(client, store)
                tim_check_realtime.init_count(cfg.INTERVAL_REQUEST_REALTIME_S)

            if GlobalSys.is_idle() and tim_check_campaign.time_elapsed():
                await run_campaigns_logic(client, store, scheduler)
                tim_check_campaign.init_count(cfg.INTERVAL_REQUEST_CAMPAIGNS_S)

            await asyncio.sleep(0.1)
    except Exception as e:
        log.error(f"Error in Orchestrator: {e}")
        return 1
    finally:
        log.info("Orchestrator offline.")
        return 0

if __name__ == "__main__":
    rc = cfg.run_and_capture(main)
    sys.exit(rc)
