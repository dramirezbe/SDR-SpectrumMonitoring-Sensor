"""
@file utils/__init__.py
@brief Expose main SDR utilities at package level.
"""

from .io_util import atomic_write_bytes, get_persist_var, modify_persist, CronHandler
from .request_util import RequestClient
from .welch_util import WelchEstimator, CampaignHackRF

__all__ = ["atomic_write_bytes", "RequestClient", "get_persist_var", "modify_persist", "CronHandler", "WelchEstimator", "CampaignHackRF"]


"""
Example usage:
from utils import AcquireFrame

sdr = AcquireFrame(100e6, 110e6, 1e6, 1)
sdr.create_IQ("Samples")
iq = sdr.get_IQ("Samples")
psd = sdr.get_psd("Samples")

"""