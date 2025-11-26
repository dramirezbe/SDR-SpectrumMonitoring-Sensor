from utils import CampaignHackRF
import cfg

log = cfg.set_logger()

START = int(88e6)
END = int(108e6)

LNA = 0
VGA = 0
ANT_AMP = True
SPAN = int(20e6)
RBW = int(10e3)
FS = int(20e6)
WINDOW = "hamming"
OVERLAP = 0.5
SCALE = "dbm"
VERBOSE = True
REMOVE = True


hack = CampaignHackRF(start_freq_hz=START, end_freq_hz=END,
    sample_rate_hz=FS, resolution_hz=RBW,
    lna_gain=LNA, vga_gain=VGA, antenna_amp=ANT_AMP,
    window=WINDOW, overlap=OVERLAP, scale=SCALE,
    verbose=VERBOSE, remove_dc_spike=REMOVE, r_ant=50.0,
    log=log)

sample = hack.acquire_hackrf()

