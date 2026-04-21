"""Background CPU monitor + system resource snapshot (non-blocking)."""
import shutil
import threading

import psutil

_cpu_lock = threading.Lock()
_cpu_value: float = 0.0


def _cpu_updater() -> None:
    global _cpu_value
    while True:
        val = psutil.cpu_percent(interval=2)
        with _cpu_lock:
            _cpu_value = val


def start_cpu_monitor() -> None:
    """Start background thread that refreshes CPU % every 2 s."""
    t = threading.Thread(target=_cpu_updater, daemon=True)
    t.start()


def get_cpu_percent() -> float:
    with _cpu_lock:
        return _cpu_value


def get_system_resources() -> dict:
    disk_root = shutil.disk_usage('/')
    try:
        disk_raid = shutil.disk_usage('/mnt/raid')
        disk_raid_info = {
            'total': disk_raid.total // (1024 ** 3),
            'used': disk_raid.used // (1024 ** 3),
            'free': disk_raid.free // (1024 ** 3),
        }
    except FileNotFoundError:
        disk_raid_info = {'total': 'N/A', 'used': 'N/A', 'free': 'N/A'}

    memory = psutil.virtual_memory()
    return {
        'disk_root': {
            'total': disk_root.total // (1024 ** 3),
            'used': disk_root.used // (1024 ** 3),
            'free': disk_root.free // (1024 ** 3),
        },
        'disk_raid': disk_raid_info,
        'cpu_usage': get_cpu_percent(),
        'memory': {
            'total': memory.total // (1024 ** 3),
            'used': memory.used // (1024 ** 3),
            'free': memory.free // (1024 ** 3),
        },
    }
