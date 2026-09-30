"""Free-RAM checks. On a 16 GB laptop with shared GPU memory, the model, the embedder,
Electron and OCR all compete for the same RAM, so heavy work backs off when it runs low."""
import logging

from app.config import settings

log = logging.getLogger(__name__)

try:
    import psutil
except ImportError:  # optional: without it the checks always pass
    psutil = None


def status() -> dict:
    """{"free_mb", "total_mb", "low"}; free/total are None when psutil isn't installed."""
    if psutil is None:
        return {"free_mb": None, "total_mb": None, "low": False}
    vm = psutil.virtual_memory()
    free = vm.available // (1024 * 1024)
    return {"free_mb": free, "total_mb": vm.total // (1024 * 1024), "low": free < settings.min_free_ram_mb}


def is_low() -> bool:
    return status()["low"]
