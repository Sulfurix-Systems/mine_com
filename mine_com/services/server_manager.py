"""Docker / subprocess operations for Minecraft servers."""
import datetime
import glob
import os
import shutil
import stat
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


def get_compose_command() -> list:
    """Prefer Docker Compose v2 plugin, fallback to legacy docker-compose binary."""
    docker_binary = shutil.which("docker")
    if docker_binary:
        try:
            result = subprocess.run(
                [docker_binary, "compose", "version"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            if result.returncode == 0:
                return [docker_binary, "compose"]
        except OSError:
            pass

    docker_compose_binary = shutil.which("docker-compose")
    if docker_compose_binary:
        return [docker_compose_binary]

    raise RuntimeError("Docker Compose is not installed")


def ensure_server_runtime_scripts(server_name: str) -> None:
    """Patch existing per-server scripts to use a compatible Compose command."""
    scripts_dir = os.path.join(MINECRAFT_SERVERS_DIR, server_name, "ramdisk-minecraft")
    shim = (
        "# MC compose shim: prefer Docker Compose v2 plugin, fallback to v1 binary.\n"
        "resolve_compose_cmd() {\n"
        "  if docker compose version >/dev/null 2>&1; then\n"
        "    COMPOSE_CMD=(docker compose)\n"
        "  elif command -v docker-compose >/dev/null 2>&1; then\n"
        "    COMPOSE_CMD=(docker-compose)\n"
        "  else\n"
        "    echo \"Docker Compose is not installed\" >&2\n"
        "    exit 127\n"
        "  fi\n"
        "}\n"
        "resolve_compose_cmd\n"
    )

    for script_name in ("start.sh", "stop.sh"):
        script_path = os.path.join(scripts_dir, script_name)
        if not os.path.isfile(script_path):
            continue

        with open(script_path, "r", encoding="utf-8", errors="replace", newline="") as f:
            content = f.read()

        updated = content
        if "docker-compose -f" in updated:
            if "# MC compose shim:" not in updated:
                if "set -e\n" in updated:
                    updated = updated.replace("set -e\n", f"set -e\n\n{shim}\n", 1)
                else:
                    lines = updated.splitlines(keepends=True)
                    insert_at = 1 if lines and lines[0].startswith("#!") else 0
                    lines.insert(insert_at, shim + "\n")
                    updated = "".join(lines)
            updated = updated.replace("docker-compose -f", '"${COMPOSE_CMD[@]}" -f')

        if updated != content:
            with open(script_path, "w", encoding="utf-8", newline="\n") as f:
                f.write(updated)

        current_mode = os.stat(script_path).st_mode
        exec_mode = current_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
        if exec_mode != current_mode:
            os.chmod(script_path, exec_mode)


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
    if not os.path.isfile(script_path):
        return False, "Файл не найден.", None

    try:
        ensure_server_runtime_scripts(server_name)
    except Exception as e:
        return False, f"Ошибка подготовки скрипта: {e}", None

    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d_%H%M%S")
    action = script_name.replace('.sh', '')
    log_file = os.path.join(LOGS_DIR, f"{server_name}_{action}_{ts}.log")
    try:
        with open(log_file, "w") as f:
            proc = subprocess.Popen(["bash", script_path], stdout=f, stderr=subprocess.STDOUT)
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
