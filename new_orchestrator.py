import cfg
log = cfg.set_logger()
from utils import ZmqPub, ZmqSub, RequestClient

topic_data = "data"
topic_sub = "acquire"
pub = ZmqPub(addr=cfg.IPC_CMD_ADDR)
sub = ZmqSub(addr=cfg.IPC_DATA_ADDR, topic=topic_data)
client = RequestClient(cfg.API_URL, timeout=(5, 15), verbose=True, logger=log)
