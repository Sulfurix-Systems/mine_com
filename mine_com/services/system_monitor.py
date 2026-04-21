"""Background CPU monitor + system resource snapshot (non-blocking)."""
import shutil
import threading
import time

import psutil

_cpu_lock = threading.Lock()
_cpu_value: float = 0.0

_net_lock = threading.Lock()
_net_recv_mbps: float = 0.0
_net_sent_mbps: float = 0.0


def _cpu_updater() -> None:
    global _cpu_value, _net_recv_mbps, _net_sent_mbps
    prev_net = psutil.net_io_counters()
    prev_t = time.monotonic()
    while True:
        val = psutil.cpu_percent(interval=2)
        with _cpu_lock:
            _cpu_value = val
        # Network delta over the same ~2 s interval
        curr_net = psutil.net_io_counters()
        curr_t = time.monotonic()
        dt = curr_t - prev_t
        if dt > 0:
            recv = (curr_net.bytes_recv - prev_net.bytes_recv) / dt / (1024 * 1024)
            sent = (curr_net.bytes_sent - prev_net.bytes_sent) / dt / (1024 * 1024)
            with _net_lock:
                _net_recv_mbps = round(max(recv, 0), 2)
                _net_sent_mbps = round(max(sent, 0), 2)
        prev_net = curr_net
        prev_t = curr_t


def start_cpu_monitor() -> None:
    """Start background thread that refreshes CPU % and net stats every 2 s."""
    t = threading.Thread(target=_cpu_updater, daemon=True)
    t.start()


def get_cpu_percent() -> float:
    with _cpu_lock:
        return _cpu_value


def get_net_stats() -> dict:
    with _net_lock:
        return {'recv_mbps': _net_recv_mbps, 'sent_mbps': _net_sent_mbps}


def get_system_resources() -> dict:
    disk_root = shutil.disk_usage('/')
    try:
        disk_raid = shutil.disk_usage('/mnt/raid')
        disk_raid_info = {
            'total': round(disk_raid.total / (1024 ** 3), 1),
            'used': round(disk_raid.used / (1024 ** 3), 1),
            'free': round(disk_raid.free / (1024 ** 3), 1),
        }
    except FileNotFoundError:
        disk_raid_info = {'total': 'N/A', 'used': 'N/A', 'free': 'N/A'}

    try:
        disk_ramdisk = shutil.disk_usage('/mnt/ramdisk')
        disk_ramdisk_info = {
            'total': round(disk_ramdisk.total / (1024 ** 3), 1),
            'used': round(disk_ramdisk.used / (1024 ** 3), 1),
            'free': round(disk_ramdisk.free / (1024 ** 3), 1),
            'percent': round(disk_ramdisk.used / disk_ramdisk.total * 100, 1) if disk_ramdisk.total else 0,
        }
    except (FileNotFoundError, ZeroDivisionError):
        disk_ramdisk_info = None

    memory = psutil.virtual_memory()
    swap = psutil.swap_memory()
    net = get_net_stats()

    return {
        'disk_root': {
            'total': round(disk_root.total / (1024 ** 3), 1),
            'used': round(disk_root.used / (1024 ** 3), 1),
            'free': round(disk_root.free / (1024 ** 3), 1),
        },
        'disk_raid': disk_raid_info,
        'disk_ramdisk': disk_ramdisk_info,
        'cpu_usage': get_cpu_percent(),
        'memory': {
            'total': round(memory.total / (1024 ** 3), 1),
            'used': round(memory.used / (1024 ** 3), 1),
            'free': round(memory.free / (1024 ** 3), 1),
        },
        'swap': {
            'total': round(swap.total / (1024 ** 3), 1),
            'used': round(swap.used / (1024 ** 3), 1),
            'percent': round(swap.percent, 1),
        },
        'net': net,
    }
