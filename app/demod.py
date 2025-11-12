import cfg
from utils import run_and_capture

import sys
import subprocess

log = cfg.get_logger()



class DemodSystem:
    def __init__(self, center_freq_hz, bw_hz=250e3, mode="fm"):

        self.mode = mode
        self.min_freq_hz = 1e6 #Min Linear Freq of SDR (1MHz)
        self.max_freq_hz = 6e9 #Max Physical Freq of SDR (6GHz)
        self.sample_rate_hz = 2_000_000
        self.decimation_fm = int(self.sample_rate_hz / bw_hz)

        if self.min_freq_hz < center_freq_hz < self.max_freq_hz:
            self.center_freq_hz = center_freq_hz
        else:
            log.error(f"Center frequency out of range: {center_freq_hz}, using default {self.min_freq_hz}")
            self.center_freq_hz = self.min_freq_hz

        self.bw_hz = bw_hz


    def fm_to_audio(self):

        cmd_fm = f"""
        hackrf_transfer -r - -f {self.center_freq_hz} -s {self.sample_rate_hz} -a 0 -l 40 -g 50 |
        csdr convert_s8_f |
        csdr fir_decimate_cc {self.decimation_fm} 0.05 |
        csdr fmdemod_quadri_cf |
        csdr deemphasis_wfm_ff {self.bw_hz} 5.0e-5 |
        play -t f32 -r {self.bw_hz} -c 1 - rate 48k
        """

        if cfg.VERBOSE:
            log.info(f"[INFO] Running pipeline FM demod to audio:\n{cmd_fm}")

        process = subprocess.Popen(
            cmd_fm,
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=1,
            universal_newlines=True
        )
        try:
            if process.returncode == 0:
                if cfg.VERBOSE:
                    log.info("Finished Demod, exiting with rc=0")
            else:
                log.error(f"Failed to run pipeline rc={process.returncode}")
        except Exception as e:
            log.error(f"Failed to run pipeline: {e}")
        except KeyboardInterrupt:
            if cfg.VERBOSE:
                log.info("KeyboardInterrupt received, exiting with rc=0")
            process.terminate()
        finally:
            process.wait()

    def am_to_audio(self):
        pass



    def run_demod(self):
        match self.mode:
            case "fm":
                self.fm_to_audio()
            case "am":
                self.am_to_audio()

def main():
    sys = DemodSystem(105.7e6)
    sys.fm_to_audio()

if __name__ == "__main__":
    rc = run_and_capture(main, log, cfg.LOGS_DIR / "demod", cfg.get_time_ms(), cfg.LOG_FILES_NUM)
    sys.exit(rc)