"""Free-RAM checks. On a 16 GB laptop with shared GPU memory, the model, the embedder,
Electron and OCR all compete for the same RAM, so heavy work backs off when it runs low."""
import psutil

from app.core import personalize


def status() -> dict:
    """{"free_mb", "total_mb", "low"}."""
    vm = psutil.virtual_memory()
    free = vm.available // (1024 * 1024)
    return {"free_mb": free, "total_mb": vm.total // (1024 * 1024), "low": free < personalize.effective("min_free_ram_mb")}


def is_low() -> bool:
    return status()["low"]
