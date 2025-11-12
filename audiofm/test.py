import subprocess
import shlex

def run_hackrf_fm(cent_freq: float, bw: float = 250e3):
    """
    Run a HackRF + CSDR FM demodulation pipeline.
    
    Parameters:
        cent_freq (float): Center frequency in Hz (e.g. 105.7e6 for 105.7 MHz)
        bw (float): Desired demodulated bandwidth in Hz (default 250e3)
    """
    # fixed hackrf sample rate (2 MHz), chosen so we can decimate to 250kHz
    hackrf_rate = 2_000_000
    decimation = int(hackrf_rate / bw)

    # build pipeline command
    cmd = f"""
    hackrf_transfer -r - -f {cent_freq:.0f} -s {hackrf_rate} -a 0 -l 40 -g 50 |
    csdr convert_s8_f |
    csdr fir_decimate_cc {decimation} 0.05 |
    csdr fmdemod_quadri_cf |
    csdr deemphasis_wfm_ff {bw:.0f} 5.0e-5 |
    play -t f32 -r {bw:.0f} -c 1 - rate 48k
    """

    print(f"[INFO] Running pipeline:\n{cmd}")

    # run in shell mode with streaming
    process = subprocess.Popen(
        cmd,
        shell=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        bufsize=1,
        universal_newlines=True
    )

    if process.stdout is not None:
        try:
            print("[INFO] Press Ctrl+C to terminate HackRF...")
        except KeyboardInterrupt:
            print("\n[INFO] Interrupted by user, terminating HackRF...")
            process.terminate()
        finally:
            process.wait()
            print(f"[INFO] Pipeline exited with code {process.returncode}")
    else:
        print("[INFO] No output from HackRF")
        process.wait()
        print(f"[INFO] Pipeline exited with code {process.returncode}")


if __name__ == "__main__":
    run_hackrf_fm(105.7e6)