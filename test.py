import cfg
log = cfg.set_logger()
from utils import RequestClient

cli = RequestClient(cfg.API_URL, mac_wifi=cfg.get_mac(), timeout=(5, 15), verbose=True, logger=log)

rc, resp = cli.get("/campaigns")
log.info(f"rc={rc} resp={resp}")

log.info(f"dict json={resp.json()}")