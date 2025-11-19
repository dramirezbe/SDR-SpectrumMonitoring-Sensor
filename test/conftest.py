# test/conftest.py
import sys
from pathlib import Path
import importlib
import types
import traceback

ROOT_DIR = Path(__file__).resolve().parents[1]
ROOT_STR = str(ROOT_DIR)
if ROOT_STR not in sys.path:
    sys.path.insert(0, ROOT_STR)

def _alias_module(pkg_name: str, alias: str):
    if alias in sys.modules:
        return
    try:
        mod = importlib.import_module(pkg_name)
    except Exception:
        # failed import: leave it to caller to decide what to do
        return False
    sys.modules[alias] = mod
    return True

# Alias app modules if they import cleanly
_alias_module("app.cfg", "cfg")
_alias_module("app.utils", "utils")
_alias_module("app.libs", "libs")

# Try to alias app.status_device; if it fails, register a minimal stub so tests can run.
if not _alias_module("app.status_device", "status_device"):
    # create a very small StatusDevice stub with the minimal API used by acquire_runner
    stub = types.ModuleType("status_device")
    class StatusDeviceStub:
        def get_disk(self):
            return {"disk_mb": 0}
        def get_total_disk(self):
            return {"disk_mb": 1}
    stub.StatusDevice = StatusDeviceStub
    sys.modules["status_device"] = stub
    # Optional: print traceback for debugging why import failed (remove in CI)
    try:
        importlib.import_module("app.status_device")
    except Exception:
        print("conftest: app.status_device import failed; using StatusDevice stub.")
        traceback.print_exc()
