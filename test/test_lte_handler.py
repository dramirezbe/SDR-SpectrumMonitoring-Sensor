# tests/test_lte_handler.py
import os
from pathlib import Path
import pytest

from app.libs.lte_handler import (
    LteHandler,
    LTELibError,
    LTELibValidationError,
    LTELibClosedError,
)


# --- Helpers: create fake ctypes-like C functions and library object ---
class FakeCFunc:
    def __init__(self, pyfunc):
        self._pyfunc = pyfunc
        # ctypes function attributes that the wrapper sets
        self.restype = None
        self.argtypes = None

    def __call__(self, *args, **kwargs):
        return self._pyfunc(*args, **kwargs)


class FakeLib:
    def __init__(self, on=lambda: None, off=lambda: None, switch=lambda x: 0, gps=lambda: b"0,0"):
        self.LTE_on = FakeCFunc(on)
        self.LTE_off = FakeCFunc(off)
        self.switch_antenna = FakeCFunc(switch)
        self.get_gps = FakeCFunc(gps)


# --- Tests ---
def test_init_loads_library_and_checks_symbols(tmp_path: Path, monkeypatch):
    lib_dir = tmp_path / "libs_C"
    lib_dir.mkdir()
    so_path = lib_dir / "lte_driver.so"
    so_path.write_bytes(b"\x00")  # create dummy file

    fake = FakeLib()
    # patch CDLL used in the module under test
    monkeypatch.setattr("app.libs.lte_handler.ctypes.CDLL", lambda _p: fake)

    handler = LteHandler(lib_path=so_path, verbose=True)
    assert handler._lib is fake
    handler.close()


def test_init_missing_file_raises(tmp_path: Path):
    missing = tmp_path / "libs_C" / "lte_driver.so"
    with pytest.raises(LTELibError):
        LteHandler(lib_path=missing, verbose=True)


def test_init_missing_symbols_raises(tmp_path: Path, monkeypatch):
    lib_dir = tmp_path / "libs_C"
    lib_dir.mkdir()
    so_path = lib_dir / "lte_driver.so"
    so_path.write_bytes(b"\x00")

    # return an object missing get_gps
    class IncompleteLib:
        def __init__(self):
            self.LTE_on = FakeCFunc(lambda: None)
            self.LTE_off = FakeCFunc(lambda: None)
            self.switch_antenna = FakeCFunc(lambda x: 0)
            # no get_gps

    monkeypatch.setattr("app.libs.lte_handler.ctypes.CDLL", lambda _p: IncompleteLib())

    with pytest.raises(LTELibError):
        LteHandler(lib_path=so_path, verbose=True)


def test_LTE_on_off_switch_and_get_gps(tmp_path: Path, monkeypatch):
    lib_dir = tmp_path / "libs_C"
    lib_dir.mkdir()
    so_path = lib_dir / "lte_driver.so"
    so_path.write_text("dummy")  # create file

    # build fake lib with predictable behavior
    def switch_impl(x):
        # return 0 for success
        return 0

    def gps_impl():
        return b"12.3456,78.9012"

    fake = FakeLib(on=lambda: None, off=lambda: None, switch=switch_impl, gps=gps_impl)
    monkeypatch.setattr("app.libs.lte_handler.ctypes.CDLL", lambda _p: fake)

    handler = LteHandler(lib_path=so_path, verbose=True)

    # LTE_on / LTE_off should not raise
    handler.LTE_on()
    handler.LTE_off()

    # explicit antenna
    assert handler.switch_antenna(2) is True
    assert handler._last_antenna == 2

    # cycle antenna (None)
    assert handler.switch_antenna() is True
    assert handler._last_antenna in range(4)

    # get_gps returns decoded string
    gps = handler.get_gps()
    assert isinstance(gps, str)
    assert "12.3456" in gps

    handler.close()


def test_switch_antenna_invalids(tmp_path: Path, monkeypatch):
    lib_dir = tmp_path / "libs_C"
    lib_dir.mkdir()
    so_path = lib_dir / "lte_driver.so"
    so_path.write_text("dummy")

    fake = FakeLib()
    monkeypatch.setattr("app.libs.lte_handler.ctypes.CDLL", lambda _p: fake)

    handler = LteHandler(lib_path=so_path, verbose=True)

    with pytest.raises(LTELibValidationError):
        handler.switch_antenna("not-an-int")

    with pytest.raises(LTELibValidationError):
        handler.switch_antenna(99)

    handler.close()


def test_operations_after_close_raise(tmp_path: Path, monkeypatch):
    lib_dir = tmp_path / "libs_C"
    lib_dir.mkdir()
    so_path = lib_dir / "lte_driver.so"
    so_path.write_text("dummy")

    fake = FakeLib()
    monkeypatch.setattr("app.libs.lte_handler.ctypes.CDLL", lambda _p: fake)

    handler = LteHandler(lib_path=so_path, verbose=True)
    handler.close()

    with pytest.raises(LTELibClosedError):
        handler.LTE_on()

    with pytest.raises(LTELibClosedError):
        handler.switch_antenna(1)

    with pytest.raises(LTELibClosedError):
        handler.get_gps()
