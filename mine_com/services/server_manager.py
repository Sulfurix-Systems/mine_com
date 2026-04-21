"""Docker / subprocess operations for Minecraft servers."""
import datetime
import glob
import os
import subprocess

import psutil

import state
from config import IGNORED_DIRS, LOGS_DIR, MINECRAFT_SERVERS_DIR


# ---------------------------------------------------------------------------
# Process helpers
# ---------------------------------------------------------------------------

def is_pid_running(pid) -> bool:
    if not pid:
        return False
    try:
        proc = psutil.Process(pid)
        return proc.is_running() and proc.status() != psutil.STATUS_ZOMBIE
    except Exception:
        return False


def is_server_running(server_name: str) -> bool:
    """Return True if the server's Docker container is currently running."""
    try:
        output = subprocess.check_output(
            ["docker", "ps", "--filter", f"name=^{server_name}-server$", "--format", "{{.ID}}"],
            stderr=subprocess.DEVNULL,
        ).decode().strip()
        return bool(output)
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Server enumeration
# ---------------------------------------------------------------------------

def get_all_server_names() -> list:
    return [
        d for d in os.listdir(MINECRAFT_SERVERS_DIR)
        if os.path.isdir(os.path.join(MINECRAFT_SERVERS_DIR, d))
        and d not in IGNORED_DIRS
    ]


def get_servers_with_status() -> list:
    servers = []
    for name in get_all_server_names():
        active = is_server_running(name)
        pid = state.busy_pids.get(name)
        busy = is_pid_running(pid)
        servers.append({'name': name, 'active': active, 'busy': busy})
    return servers


# ---------------------------------------------------------------------------
# Script execution
# ---------------------------------------------------------------------------

def run_server_script(server_name: str, script_name: str):
    """
    Run start.sh / stop.sh and stream output to a timestamped log file.
    Returns (success, message, pid|None).
    """
    script_path = os.path.join(
        MINECRAFT_SERVERS_DIR, server_name, "ramdisk-minecraft", script_name
    )
    if not os.path.isfile(script_path) or not os.access(script_path, os.X_OK):
        return False, "Файл не найден или не исполняемый.", None

    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d_%H%M%S")
    action = script_name.replace('.sh', '')
    log_file = os.path.join(LOGS_DIR, f"{server_name}_{action}_{ts}.log")
    try:
        with open(log_file, "w") as f:
            proc = subprocess.Popen([script_path], stdout=f, stderr=subprocess.STDOUT)
        state.busy_pids[server_name] = proc.pid
        return True, f"Скрипт запущен (pid {proc.pid}). Лог пишется.", proc.pid
    except Exception as e:
        return False, f"Ошибка запуска: {e}", None


def get_action_log(server_name: str, action: str):
    """Return the content of the most recent start/stop log, or None."""
    log_mask = os.path.join(LOGS_DIR, f"{server_name}_{action}_*.log")
    log_files = sorted(glob.glob(log_mask), reverse=True)
    if not log_files:
        return None
    with open(log_files[0], "rb") as f:
        return f.read().decode("utf-8", errors="replace")


# ---------------------------------------------------------------------------
# RCON
# ---------------------------------------------------------------------------

def get_rcon_params(server_name: str):
    """Read RCON host/port/password from server.properties."""
    prop_path = os.path.join(
        MINECRAFT_SERVERS_DIR, server_name, "ramdisk-minecraft", "server.properties"
    )
    rcon_port = 25575
    rcon_password = None
    if not os.path.isfile(prop_path):
        raise RuntimeError("server.properties not found")
    with open(prop_path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if line.startswith("rcon.port="):
                try:
                    rcon_port = int(line.split("=", 1)[1])
                except Exception:
                    pass
            elif line.startswith("rcon.password="):
                rcon_password = line.split("=", 1)[1]
    if not rcon_password:
        raise RuntimeError("rcon.password not set in server.properties")
    return "127.0.0.1", rcon_port, rcon_password


# ---------------------------------------------------------------------------
# BlueMap config patching
# ---------------------------------------------------------------------------

def patch_bluemap_configs(server_name: str) -> None:
    config_dir = os.path.join(
        MINECRAFT_SERVERS_DIR, server_name, "neoforge-server", "config", "bluemap"
    )
    patch_list = [
        {
            "filename": "core.conf",
            "key": "data:",
            "value": f'data: "/server/{server_name}/bluemap/"',
        },
        {
            "filename": "webapp.conf",
            "key": "webroot:",
            "value": f'webroot: "/server/{server_name}/bluemap/web"',
        },
        {
            "filename": "webserver.conf",
            "key": "webroot:",
            "value": f'webroot: "/server/{server_name}/bluemap/web"',
        },
        {
            "filename": os.path.join("storages", "file.conf"),
            "key": "root:",
            "value": f'root: "/server/{server_name}/bluemap/web/maps"',
        },
    ]
    for patch in patch_list:
        path = os.path.join(config_dir, patch["filename"])
        if not os.path.isfile(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                lines = f.readlines()
            with open(path, "w", encoding="utf-8") as f:
                for line in lines:
                    if line.strip().startswith(patch["key"]):
                        f.write(patch["value"] + "\n")
                    else:
                        f.write(line)
        except Exception as ex:
            print(f"Ошибка обновления {path}: {ex}")
