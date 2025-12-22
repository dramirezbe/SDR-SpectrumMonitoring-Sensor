#!/usr/bin/env python3
import cfg
import asyncio
import json
import datetime
from pathlib import Path
from utils import ZmqPairController
from functions import AcquireCampaign

async def main():
    # Configuración del registrador de eventos
    log = cfg.set_logger()
    
    rf_params = {
        "method_psd": "pfb",
        "center_freq_hz": int(98e6),
        "sample_rate_hz": int(20e6),
        "rbw_hz": int(10e3),
        "overlap": 0.5,
        "window": "hamming",
        "lna_gain": 0,
        "vga_gain": 0,
        "antenna_amp": True,
        "antenna_port": 1,
        "ppm_error": 0,
    }

    try:
        async with ZmqPairController(addr=cfg.IPC_ADDR, is_server=True) as controller:
            log.info("Iniciando captura con corrección de DC Spike...")
            campaign = AcquireCampaign(controller, log)
            
            # Adquisición de datos procesados (Stitched/Patched)
            data_dict = await campaign.get_corrected_data(rf_params)

            if data_dict:
                # 1. Preparar carpeta de destino
                output_dir = Path("json-campaign")
                output_dir.mkdir(parents=True, exist_ok=True)

                # 2. Generar metadatos para el nombre del archivo
                human_date = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                fc = rf_params["center_freq_hz"]
                fs = rf_params["sample_rate_hz"]
                rbw = rf_params["rbw_hz"]

                filename = f"{human_date}_FC{fc}_FS{fs}_RBW{rbw}.json"
                file_path = output_dir / filename

                # 3. Guardar como JSON
                with open(file_path, "w", encoding="utf-8") as f:
                    json.dump(data_dict, f, indent=4)
                
                log.info(f"✅ Campaña guardada exitosamente: {file_path}")
            else:
                log.warning("No se recibieron datos para guardar.")
        
    except Exception as e:
        log.error(f"Error en la ejecución: {e}")

if __name__ == "__main__":
    asyncio.run(main())