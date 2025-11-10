"""
@file libs/lte_handler.py
@brief Python wrapper for lte_driver.so with print-based logging and pathlib usage.
"""

from __future__ import annotations
import ctypes
from ctypes import c_int, c_char_p
from pathlib import Path
from typing import Optional, Union
import contextlib
import os
import sys


# ---- Exceptions ----
class LTELibError(Exception):
    """Base exception for LTE wrapper errors."""
    pass


class LTELibClosedError(LTELibError):
    """Raised when operations are attempted on a closed wrapper."""
    pass


class LTELibValidationError(LTELibError):
    """Raised for invalid arguments (type/range/etc)."""
    pass


# Acquire a handle to libc (for fflush). If not available, set to None and behave gracefully.
try:
    _libc = ctypes.CDLL(None)
    # Try to get fflush symbol (some embedded platforms may not have it)
    if not hasattr(_libc, "fflush"):
        _libc = None
except Exception:
    _libc = None


# ---- Main Wrapper ----
class LteHandler:
    VALID_ANT_RANGE = range(0, 4)

    def __init__(self, lib_path: Union[str, Path], verbose: bool = False) -> None:
        # Normalize to Path early
        path = Path(lib_path) if not isinstance(lib_path, Path) else lib_path

        self.verbose = verbose

        try:
            # resolve but allow non-strict (file may be created later in tests)
            path = path.resolve(strict=False)
        except Exception:
            # resolve() can raise on some broken symlinks; keep the Path object anyway
            path = Path(str(path))

        # --- Validate path ---
        if lib_path is None:
            print("[LTE][ERROR] lib_path must be provided and non-empty.")
            raise LTELibValidationError("lib_path must be provided and non-empty")

        # After normalization, ensure we have a usable string-like path
        if not isinstance(path, Path) or str(path).strip() == "":
            print("[LTE][ERROR] lib_path must be a non-empty string or Path-like.")
            raise LTELibValidationError("lib_path must be a non-empty string or Path-like")

        # Check existence & type
        if not path.exists():
            print(f"[LTE][ERROR] Shared library not found: {path}")
            raise LTELibError(f"Shared library not found: {path}")

        if not path.is_file():
            print(f"[LTE][ERROR] Path exists but is not a file: {path}")
            raise LTELibError(f"Shared library path is not a file: {path}")

        # Check readability by attempting to open for read — more portable than os.access
        try:
            with path.open("rb"):
                pass
        except Exception as e:
            print(f"[LTE][ERROR] Shared library not readable: {path} ({e})")
            raise LTELibError(f"Shared library not readable: {path}") from e

        self._lib_path = path
        self._closed = False
        self._last_antenna: Optional[int] = 0
        if self.verbose:
            print(f"[LTE] Loading LTE shared library: {self._lib_path}")

        # --- Load library ---
        try:
            # ctypes accepts a str path; cast explicitly to str
            self._lib = ctypes.CDLL(str(self._lib_path))
        except OSError as e:
            print(f"[LTE][EXCEPTION] Failed to load shared library: {e}")
            raise LTELibError(f"Failed to load shared library: {e}") from e

        # --- Validate symbols ---
        missing = []
        for func, restype, argtypes in [
            ("LTE_on", None, None),
            ("LTE_off", None, None),
            ("switch_antenna", c_int, [c_int]),
            ("get_gps", c_char_p, None),
        ]:
            if not hasattr(self._lib, func):
                missing.append(func)
                continue
            func_ref = getattr(self._lib, func)
            func_ref.restype = restype
            if argtypes:
                func_ref.argtypes = argtypes

        if missing:
            print(f"[LTE][ERROR] Missing symbols: {', '.join(missing)}")
            raise LTELibError(f"Shared library missing symbols: {', '.join(missing)}")
        if self.verbose:
            print(f"[LTE] Library loaded successfully from {self._lib_path}")

    # ---- Lifecycle ----
    def close(self) -> None:
        if self._closed and self.verbose:
            print("[LTE] close() called but already closed.")
            return
        try:
            # Remove reference to library object — ctypes will unload when refcount hits 0
            del self._lib
            if self.verbose:
                print("[LTE] Released library reference.")
        except Exception as e:
            print(f"[LTE][EXCEPTION] Error releasing library: {e}")
        self._closed = True
        if self.verbose:
            print("[LTE] Handler closed.")

    def _ensure_open(self) -> None:
        if self._closed:
            print("[LTE][ERROR] Operation attempted on closed handler.")
            raise LTELibClosedError("LteHandler is closed")

    # ---- Helpers to suppress C stdout/stderr ----
    @contextlib.contextmanager
    def _suppress_c_output(self):
        """
        Context manager that redirects native process stdout/stderr file descriptors
        to /dev/null if verbose == False. Calls fflush(NULL) before/after redirection
        to avoid buffered C output leaking outside the redirect.
        """
        if self.verbose:
            # no suppression requested
            yield
            return

        # Flush Python-level buffers
        try:
            sys.stdout.flush()
        except Exception:
            pass
        try:
            sys.stderr.flush()
        except Exception:
            pass

        # Flush C stdio buffers (fflush(NULL)) if available
        try:
            if _libc is not None:
                _libc.fflush(None)
        except Exception:
            # non-fatal; continue with fd redirection
            pass

        # Save original fds and redirect
        try:
            # open /dev/null read/write: O_RDWR is more robust for both stdout/stderr
            devnull_fd = os.open(os.devnull, os.O_RDWR)
        except Exception:
            # fallback: don't suppress if we can't open /dev/null
            yield
            return

        saved_stdout_fd = os.dup(1)
        saved_stderr_fd = os.dup(2)

        # Redirect both stdout and stderr to devnull
        os.dup2(devnull_fd, 1)
        os.dup2(devnull_fd, 2)
        # devnull_fd can be closed after dup2
        os.close(devnull_fd)

        try:
            yield
        finally:
            # Flush Python buffers again
            try:
                sys.stdout.flush()
            except Exception:
                pass
            try:
                sys.stderr.flush()
            except Exception:
                pass

            # Flush C buffers again to be safe
            try:
                if _libc is not None:
                    _libc.fflush(None)
            except Exception:
                pass

            # Restore original fds
            try:
                os.dup2(saved_stdout_fd, 1)
                os.dup2(saved_stderr_fd, 2)
            except Exception as e:
                # If restore fails we try best-effort to close the saved fds
                print(f"[LTE][EXCEPTION] Failed to restore fds: {e}")
            finally:
                try:
                    os.close(saved_stdout_fd)
                except Exception:
                    pass
                try:
                    os.close(saved_stderr_fd)
                except Exception:
                    pass

    # ---- API ----
    def LTE_on(self) -> None:
        self._ensure_open()
        if self.verbose:
            print("[LTE] Calling LTE_on()...")
        try:
            with self._suppress_c_output():
                self._lib.LTE_on()
            if self.verbose:
                print("[LTE] LTE_on() done.")
        except Exception as e:
            print(f"[LTE][EXCEPTION] LTE_on() failed: {e}")
            raise LTELibError(f"LTE_on failed: {e}") from e

    def LTE_off(self) -> None:
        self._ensure_open()
        if self.verbose:
            print("[LTE] Calling LTE_off()...")
        try:
            with self._suppress_c_output():
                self._lib.LTE_off()
            if self.verbose:
                print("[LTE] LTE_off() done.")
        except Exception as e:
            print(f"[LTE][EXCEPTION] LTE_off() failed: {e}")
            raise LTELibError(f"LTE_off failed: {e}") from e

    def switch_antenna(self, ant_num: Optional[int] = None) -> bool:
        self._ensure_open()

        if ant_num is None:
            current = self._last_antenna if isinstance(self._last_antenna, int) else 0
            target = (current + 1) % 4
            if self.verbose:
                print(f"[LTE] Cycling antenna {current} → {target}")
        else:
            if not isinstance(ant_num, int):
                print(f"[LTE][ERROR] ant_num type invalid: {type(ant_num)}")
                raise LTELibValidationError("ant_num must be int in range 0..3 or None")
            if ant_num not in self.VALID_ANT_RANGE:
                print(f"[LTE][ERROR] ant_num out of range: {ant_num}")
                raise LTELibValidationError(f"ant_num must be in {list(self.VALID_ANT_RANGE)}")
            target = ant_num

            if self.verbose:
                print(f"[LTE] Switching to antenna {target}")

        try:
            with self._suppress_c_output():
                # pass plain int — ctypes will convert according to argtypes
                ret = int(self._lib.switch_antenna(c_int(target)))
        except Exception as e:
            print(f"[LTE][EXCEPTION] switch_antenna({target}) failed: {e}")
            raise LTELibError(f"switch_antenna failed: {e}") from e

        if ret == 0:
            # The C library may print its own message; we've suppressed it when verbose==False.
            self._last_antenna = target
            return True
        else:
            print(f"[LTE][WARN] switch_antenna({target}) returned {ret}")
            return False

    def get_gps(self) -> str:
        self._ensure_open()
        if self.verbose:
            print("[LTE] Calling get_gps()...")
        try:
            with self._suppress_c_output():
                raw = self._lib.get_gps()
        except Exception as e:
            print(f"[LTE][EXCEPTION] get_gps() failed: {e}")
            raise LTELibError(f"get_gps invocation failed: {e}") from e

        if not raw:
            print("[LTE][ERROR] get_gps returned NULL.")
            raise LTELibError("get_gps returned NULL")

        try:
            s = (
                raw.decode("utf-8", errors="ignore").strip()
                if isinstance(raw, (bytes, bytearray))
                else str(raw).strip()
            )
            if self.verbose:
                print(f"[LTE] GPS: {s}")
            return s
        except Exception as e:
            print(f"[LTE][EXCEPTION] Failed to decode GPS result: {e}")
            raise LTELibError(f"Failed to decode GPS result: {e}") from e

    # ---- Context manager support ----
    def __enter__(self) -> "LteHandler":
        self._ensure_open()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()


# ---- Convenience factory ----
def init_lte(lib_path: Optional[Union[str, Path]] = "./liblte_dummy.so", verbose: bool = False) -> LteHandler:
    return LteHandler(lib_path, verbose=verbose)