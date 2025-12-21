#!/usr/bin/env python3
# retry_queue.py

"""
Módulo de Gestión de Cola de Reintentos.

Este script se encarga de procesar archivos JSON almacenados localmente que no pudieron
ser enviados previamente. Gestiona la lógica de reintentos, validación de archivos
y limpieza de la cola de envío hacia la API central.
"""

from __future__ import annotations

import json
import time
import sys
from pathlib import Path

import cfg
from utils import RequestClient

log = cfg.set_logger()

# Configuración de reintentos
RETRY_SECONDS = 5
RETRIES_PER_FILE = 2

# Códigos de retorno
RC_OK = 0
RC_NETWORK = 1      # Problemas de red o de POST (transitorios)
RC_IO = 2           # Errores de entrada/salida o de sistema de archivos
RC_JSON = 3         # JSON inválido o error de validación (4xx)
RC_UNEXPECTED = 4   # Otros fallos inesperados


def attempt_send(cli: RequestClient, payload: dict, url: str) -> int:
    """
    Intenta enviar un payload a la API una sola vez.

    Realiza una petición POST y analiza la respuesta para determinar si el error
    es transitorio o permanente.

    Args:
        cli (RequestClient): Instancia del cliente de peticiones HTTP.
        payload (dict): Diccionario con los datos a enviar.
        url (str): URL del endpoint de la API.

    Returns:
        int: Código de retorno basado en el resultado:
            * RC_OK: Éxito (2xx).
            * RC_NETWORK: Error transitorio de red o servidor (5xx).
            * RC_JSON: Error de validación del cliente (4xx).
            * RC_UNEXPECTED: Error inesperado o respuesta malformada.
    """
    try:
        rc, resp = cli.post_json(url, payload)
    except Exception as e:
        log.exception("Exception while posting JSON: %s", e)
        return RC_NETWORK

    # client returned non-zero rc -> inspect status if available
    if rc != 0:
        status = getattr(resp, "status_code", None)
        if status is not None:
            try:
                status_int = int(status)
            except Exception:
                status_int = None
            if status_int is not None and 400 <= status_int < 500:
                log.error("POST returned client error %s for url=%s.", status_int, url)
                return RC_JSON
            if status_int is not None and 500 <= status_int < 600:
                log.warning("POST returned server error %s for url=%s.", status_int, url)
                return RC_NETWORK
        log.warning("post_json returned rc=%s for url=%s without usable HTTP status; treating as network error.", rc, url)
        return RC_NETWORK

    # rc == 0 -> must have resp
    if resp is None:
        log.error("post_json returned rc=0 but response object is None for url=%s", url)
        return RC_UNEXPECTED

    status = getattr(resp, "status_code", None)
    if status is None:
        log.error("Response missing status_code attribute; treating as unexpected failure.")
        return RC_UNEXPECTED

    try:
        status_int = int(status)
    except Exception:
        log.error("Invalid status_code type: %r", status)
        return RC_UNEXPECTED

    if 200 <= status_int < 300:
        try:
            preview = resp.text[:200] + ("..." if len(resp.text) > 200 else "")
            log.info("POST success code=%s preview=%s", status_int, preview)
        except Exception:
            pass
        return RC_OK

    # map non-2xx -> 4xx permanent-ish, 5xx transient
    if 400 <= status_int < 500:
        log.error("POST returned client error %s for url=%s.", status_int, url)
        return RC_JSON
    if 500 <= status_int < 600:
        log.warning("POST returned server error %s for url=%s.", status_int, url)
        return RC_NETWORK

    log.warning("POST returned unexpected status %s for url=%s; treating as transient.", status_int, url)
    return RC_NETWORK


def retry_queue(cli: RequestClient) -> int:
    """
    Procesa los archivos en el directorio de cola intentando reenviarlos.

    Sigue un orden de antigüedad (los más viejos primero). Los archivos corruptos
    o enviados con éxito se eliminan. Si un archivo agota sus reintentos por
    error de red, el proceso se detiene inmediatamente para preservar el orden.

    Args:
        cli (RequestClient): Cliente para realizar los reintentos de envío.

    Returns:
        int: Siempre retorna RC_OK tras finalizar o detener el procesamiento.
        RC_IO: Si hay errores persistentes al listar el directorio.
    """
    qdir = Path(cfg.QUEUE_DIR)
    if not qdir.exists():
        log.info("Queue directory %s does not exist; nothing to do.", qdir)
        return RC_OK

    try:
        files = [p for p in qdir.iterdir() if p.is_file() and p.suffix == ".json"]
    except Exception as e:
        log.exception("Failed to list queue directory %s: %s", qdir, e)
        return RC_IO

    files.sort(key=lambda p: p.stat().st_mtime)  # oldest first
    log.info("Found %d files in retry queue at %s", len(files), qdir)

    # Process oldest-first
    while files:
        file_path: Path = files.pop(0)
        log.info("Processing queued file: %s", file_path)

        # Load JSON payload
        try:
            with file_path.open("r", encoding="utf-8") as fh:
                payload = json.load(fh)
        except json.JSONDecodeError:
            log.error("Invalid JSON in queued file %s; removing corrupt file.", file_path)
            try:
                file_path.unlink()
                log.info("Deleted corrupt queued file: %s", file_path)
            except Exception as e:
                log.exception("Failed to delete corrupt file %s: %s", file_path, e)
            # continue to next file
            continue
        except Exception as e:
            log.exception("Failed reading queued file %s: %s", file_path, e)
            # leave file in queue for later; continue with next file
            continue

        # Validate payload is a mapping
        if not isinstance(payload, dict):
            log.error("Queued file %s does not contain a JSON object (expected dict). Deleting file.", file_path)
            try:
                file_path.unlink()
                log.info("Deleted invalid queued file: %s", file_path)
            except Exception as e:
                log.exception("Failed to delete invalid file %s: %s", file_path, e)
            continue

        # Attempt to send with retry loop
        attempt = 0
        sent = False
        last_rc = RC_NETWORK
        while attempt < RETRIES_PER_FILE and not sent:
            attempt += 1
            log.info("Attempt %d/%d for %s", attempt, RETRIES_PER_FILE, file_path.name)
            rc_send = attempt_send(cli, payload, cfg.DATA_URL)
            last_rc = rc_send

            if rc_send == RC_OK:
                sent = True
                break

            # transient or client error -> retry up to RETRIES_PER_FILE
            if attempt < RETRIES_PER_FILE:
                log.warning("Attempt %d failed for %s (rc=%s). Retrying in %s seconds.", attempt, file_path.name, rc_send, RETRY_SECONDS)
                time.sleep(RETRY_SECONDS)
            else:
                log.error("Attempt %d failed for %s (rc=%s). Will stop processing and leave file in queue.", attempt, file_path.name, rc_send)

        # Post-attempt handling
        if sent:
            # Delete the file on successful send
            try:
                file_path.unlink()
                log.info("Successfully sent and deleted queued file: %s", file_path)
            except Exception as e:
                log.exception("Sent but failed to delete queued file %s: %s", file_path, e)
            # continue processing next file
            continue
        else:
            # Not sent after retries -> stop processing immediately, leave file in place
            log.info("Stopping retry run due to failure on %s (last_rc=%s). Leaving file in queue.", file_path, last_rc)
            return RC_OK

    log.info("Retry queue processing complete.")
    return RC_OK


def main() -> int:
    """
    Punto de entrada principal para el ejecutor de la cola de reintentos.

    Inicializa el cliente HTTP y lanza el procesamiento de la cola.

    Returns:
        int: RC_OK si el proceso termina correctamente, RC_NETWORK si falla la inicialización.
    """
    try:
        cli = RequestClient(cfg.API_URL, mac_wifi=cfg.get_mac(), timeout=(RETRY_SECONDS, 15), verbose=cfg.VERBOSE, logger=log)
    except Exception as e:
        log.exception("Failed to construct RequestClient: %s", e)
        return RC_NETWORK

    return retry_queue(cli)


if __name__ == "__main__":
    rc = cfg.run_and_capture(main, cfg.LOG_FILES_NUM)
    sys.exit(rc)