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
from dataclasses import asdict
import time
import subprocess

WEBRTC_CMD = f"{cfg.PYTHON_ENV_STR} -u server_webrtc.py 2>&1 | systemd-cat -t WEBRTC_SERVER"
KAL_SYNC_CMD = f"{cfg.PYTHON_ENV_STR} -u kal_sync.py 2>&1 | systemd-cat -t KAL_SYNC"
#log.info(f"WEBRTC_CMD: {WEBRTC_CMD}")

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
                ppm_error=0,
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
    GlobalSys.set(SysState.KALIBRATING)
    
    try:
        # Uso de asyncio para no bloquear el loop
        process = await asyncio.create_subprocess_shell(KAL_SYNC_CMD)
        return_code = await process.wait()

        if return_code == 0:
            log.info("✅ Calibración exitosa.")
        else:
            log.warning(f"⚠️ Calibración falló (RC {return_code}). Continuando...")
    except Exception as e:
        log.error(f"❌ Error en secuencia de calibración: {e}")
    
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
    if not GlobalSys.is_idle(): return 1
    
    # 1. Initial Probe
    next_config, _, delta_t_ms = fetch_realtime_config(client)
    if not next_config:
        return 0
    
    # 2. Lock State
    GlobalSys.set(SysState.REALTIME)
    webrtc_proc = None
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
            
            while True:
                if timer_force_rotation.time_elapsed():
                    log.info("[REALTIME] Periodic rotation triggered.")
                    break

                #StateMachine Realtime
                is_demod = bool(next_config.get("demodulation", False))

                if is_demod:
                    if webrtc_proc is None or webrtc_proc.poll() is not None:
                        log.info("[REALTIME] Starting WebRTC Server...")
                        webrtc_proc = subprocess.Popen(WEBRTC_CMD, shell=True)
                    DEMOD_CFG_SENT = True
                    dsp_payload = await acquirer.get_corrected_data(next_config)     
                else:
                    if webrtc_proc is not None:
                        log.info("[REALTIME] Stopping WebRTC Server...")
                        webrtc_proc.terminate()
                        webrtc_proc.wait() # Ensure it's fully closed
                        webrtc_proc = None # Reset the handle
                    dsp_payload = await acquirer.get_corrected_data(next_config) 
                    if DEMOD_CFG_SENT:
                        RESET_DEMOD_CFG = True
                        DEMOD_CFG_SENT = False

                if RESET_DEMOD_CFG:
                    await zmq_ctrl.send_command({}) #Stop the audio demodulation in rf_engine just if demodulation changed
                    RESET_DEMOD_CFG = False

                
                
                if dsp_payload:
                    final_payload = format_data_for_upload(dsp_payload, log)

                    #debug
                    if final_payload.get("excursion_hz", False):
                        log.info(f"Excursion: {final_payload['excursion_hz']} Hz")
                    if final_payload.get("depth", False):
                        log.info(f"Depth: {final_payload['depth']} %")

                    rc, _ = client.post_json(cfg.DATA_URL, final_payload)
                    if rc != 0:
                        log.warning(f"[REALTIME] Upload failed (RC {rc}).")
                else:
                    log.warning("[REALTIME] Acquisition timeout or DSP error.")

                # --- STEP D: Heartbeat / Config Update ---
                new_conf, _, dt = fetch_realtime_config(client)
                if not new_conf:
                    log.info("[REALTIME] Stop command received. Breaking.")
                    break 
                
                next_config = new_conf
                store.add_to_persistent("delta_t_ms", dt)

                await asyncio.sleep(0.05)

    except Exception as e:
        if webrtc_proc:
            webrtc_proc.terminate()
        log.error(f"[REALTIME] Critical loop error: {e}")
    finally:
        log.info("[REALTIME] Reverting to IDLE.")
        if webrtc_proc and webrtc_proc.poll() is None:
            webrtc_proc.terminate()
            try:
                webrtc_proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                webrtc_proc.kill() # Force kill if it won't stop
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
            #await _perform_calibration_sequence()
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