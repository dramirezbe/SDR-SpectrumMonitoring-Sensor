"""
@file utils/__init__.py
@brief Expose main SDR utilities at package level.
"""

from .sdr_util import AcquireFrame
from .io_util import atomic_write_bytes, run_and_capture, get_tmp_var, modify_tmp, CronHandler
from .request_util import RequestClient

__all__ = ["AcquireFrame", "atomic_write_bytes", "RequestClient", "run_and_capture", "get_tmp_var", "modify_tmp", "CronHandler"]


"""
Example usage:
from utils import AcquireFrame

sdr = AcquireFrame(100e6, 110e6, 1e6, 1)
sdr.create_IQ("Samples")
iq = sdr.get_IQ("Samples")
psd = sdr.get_psd("Samples")

"""