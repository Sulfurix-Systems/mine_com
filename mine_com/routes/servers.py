"""Server list, status, actions, metrics, RCON and create-server routes."""
import os
import re
import shutil
import subprocess
import zipfile

from flask import Blueprint, jsonify, redirect, render_template, request, session, url_for
from mcipc.rcon.je import Client as RconClient

import state
from config import MINECRAFT_SERVERS_DIR
from routes.auth import login_required
from services.server_manager import (
    get_action_log,
    get_rcon_params,
    get_servers_with_status,
    is_server_running,
    patch_bluemap_configs,
    run_server_script,
)
from services.system_monitor import get_system_resources

bp = Blueprint('servers', __name__)


# ---------------------------------------------------------------------------
# Index / status
# ---------------------------------------------------------------------------

@bp.route('/')
def list_servers():
    if not session.get('logged_in'):
        return redirect(url_for('auth.login'))
    servers = get_servers_with_status()
    resources = get_system_resources()
    return render_template('index.html', servers=servers, resources=resources)


@bp.route('/resources')
@login_required
def resources():
    return jsonify(get_system_resources())


@bp.route('/server_status')
@login_required
def server_status():
    """Return server list with active/busy + backup status merged in one call."""
    servers = get_servers_with_status()
    for s in servers:
        s['backup_status'] = state.backup_status.get(s['name'], 'idle')
        bk = state.backup_result.get(s['name'], {})
        s['backup_filename'] = bk.get('filename')
        s['backup_success'] = bk.get('success')
    return jsonify({'servers': servers})


# ---------------------------------------------------------------------------
# Start / stop
# ---------------------------------------------------------------------------

@bp.route('/server/<server_name>/<action>', methods=['POST'])
@login_required
def server_action(server_name, action):
    if action not in ('start', 'stop'):
        return jsonify({'success': False, 'error': 'Unknown action'}), 400
    script_file = 'start.sh' if action == 'start' else 'stop.sh'
    if action == 'start':
        patch_bluemap_configs(server_name)
    ok, msg, pid = run_server_script(server_name, script_file)
    if ok and pid:
        state.busy_pids[server_name] = pid
    return jsonify({'success': ok, 'message': msg})


# ---------------------------------------------------------------------------
# Logs
# ---------------------------------------------------------------------------

@bp.route('/server/<server_name>/<action>/log')
@login_required
def server_action_log(server_name, action):
    if action not in ("start", "stop"):
        return jsonify({"log": "Неверное действие"}), 400
    log = get_action_log(server_name, action)
    if log is None:
        return jsonify({"log": "Нет лога"}), 404
    return jsonify({"log": log})


@bp.route('/server/<server_name>/docker_log')
@login_required
def server_docker_log(server_name):
    if not is_server_running(server_name):
        return jsonify({'error': 'Контейнер не запущен'}), 400
    try:
        log = subprocess.check_output(
            ["docker", "logs", f"{server_name}-server", "--tail", "100"],
            stderr=subprocess.STDOUT,
            encoding="utf-8",
            errors="replace",
        )
        return jsonify({'log': log})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

@bp.route('/server/<server_name>/metrics')
@login_required
def server_metrics(server_name):
    ramdisk_path = os.path.join('/mnt/ramdisk', f"{server_name}_world")
    raid_world_path = os.path.join('/mnt/raid/minecraft', server_name, 'world')

    cpu_percent = 0
    mem_percent = 0
    mem_used = 0
    mem_total = 0

    try:
        cname = f"{server_name}-server"
        lines = subprocess.check_output(
            ["docker", "stats", "--no-stream", "--format",
             "{{.Name}} {{.CPUPerc}} {{.MemUsage}}"]
        ).decode().splitlines()
        for line in lines:
            if line.startswith(cname + " "):
                parts = line.split()
                cpu_percent = float(parts[1].replace('%', '').replace(',', '.'))

                def _parse_mem(m: str) -> float:
                    m = m.replace(",", ".")
                    if m.endswith("GiB"):
                        return float(m[:-3])
                    if m.endswith("MiB"):
                        return float(m[:-3]) / 1024
                    if m.endswith("KiB"):
                        return float(m[:-3]) / (1024 * 1024)
                    return float(m)

                mu = _parse_mem(parts[2])
                mt = _parse_mem(parts[4])
                mem_percent = int(round(mu / mt * 100)) if mt else 0
                mem_used = round(mu, 2)
                mem_total = round(mt, 2)
                break
    except Exception:
        pass

    def _du(path: str) -> int:
        try:
            return int(subprocess.check_output(['du', '-sb', path]).decode().split()[0])
        except Exception:
            return 0

    ramdisk_size = _du(ramdisk_path) if os.path.isdir(ramdisk_path) else 0
    raid_size = _du(raid_world_path) if os.path.isdir(raid_world_path) else 0

    ramdisk_percent = None
    try:
        if os.path.isdir(ramdisk_path):
            total = shutil.disk_usage(ramdisk_path).total
            ramdisk_percent = round(ramdisk_size / total * 100, 2) if total else 0
    except Exception:
        pass

    root_usage_percent = 0
    try:
        root_used = shutil.disk_usage('/').used
        if root_used:
            root_usage_percent = round(ramdisk_size / root_used * 100, 2)
    except Exception:
        pass

    raid_usage_percent = 0
    try:
        raid_used = shutil.disk_usage('/mnt/raid').used
        if raid_used:
            raid_usage_percent = round(raid_size / raid_used * 100, 2)
    except Exception:
        pass

    return jsonify({
        "cpu": int(round(cpu_percent)),
        "memory": {"percent": mem_percent, "used": mem_used, "total": mem_total},
        "root_usage_percent": root_usage_percent,
        "raid_usage_percent": raid_usage_percent,
        "ramdisk_percent": ramdisk_percent,
    })


# ---------------------------------------------------------------------------
# RCON
# ---------------------------------------------------------------------------

@bp.route('/server/<server_name>/rcon', methods=['POST'])
@login_required
def rcon_command(server_name):
    data = request.get_json() or {}
    command = data.get('command', '').strip()
    if not command:
        return jsonify({'success': False, 'error': 'Команда не указана'}), 400
    try:
        host, port, password = get_rcon_params(server_name)
        with RconClient(host, port, passwd=password, timeout=5) as client:
            response = client.run(command)
        return jsonify({'success': True, 'response': response})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ---------------------------------------------------------------------------
# Create server
# ---------------------------------------------------------------------------

@bp.route('/create_server', methods=['POST'])
@login_required
def create_server():
    server_name = request.form.get('server_name', '').strip()
    neoforge_version = request.form.get('neoforge_version', '').strip()
    zip_file = request.files.get('zip_file')

    if not server_name or not re.match(r'^[A-Za-z0-9_-]+$', server_name):
        return jsonify({'success': False, 'error': 'Некорректное имя сервера'}), 400
    if not neoforge_version:
        return jsonify({'success': False, 'error': 'Не указана версия NEOFORGE'}), 400

    src = os.path.join(MINECRAFT_SERVERS_DIR, 'precreated_server_prefab')
    dst = os.path.join(MINECRAFT_SERVERS_DIR, server_name)

    if not os.path.isdir(src):
        return jsonify({'success': False, 'error': 'Шаблон не найден'}), 500
    if os.path.exists(dst):
        return jsonify({'success': False, 'error': 'Сервер с таким именем уже существует'}), 400

    try:
        shutil.copytree(src, dst)

        # Patch NeoForge version in startserver.sh
        start_sh = os.path.join(dst, "neoforge-server", "startserver.sh")
        if os.path.isfile(start_sh):
            with open(start_sh, "r", encoding="utf-8") as f:
                lines = f.readlines()
            with open(start_sh, "w", encoding="utf-8") as f:
                for line in lines:
                    if line.strip().startswith("NEOFORGE_VERSION="):
                        f.write(f'NEOFORGE_VERSION={neoforge_version}\n')
                    else:
                        f.write(line)

        # Extract optional zip (mods / configs pack)
        if zip_file and zip_file.filename.endswith('.zip'):
            extract_to = os.path.join(dst, 'neoforge-server')
            os.makedirs(extract_to, exist_ok=True)
            with zipfile.ZipFile(zip_file.stream) as zf:
                for member in zf.infolist():
                    if member.is_dir():
                        continue
                    relpath = os.path.normpath(member.filename)
                    # Prevent path traversal
                    if any(part in ('..', '') for part in relpath.split(os.sep)):
                        continue
                    target_path = os.path.join(extract_to, relpath)
                    if os.path.exists(target_path):
                        continue
                    os.makedirs(os.path.dirname(target_path), exist_ok=True)
                    with zf.open(member) as source, open(target_path, 'wb') as target:
                        shutil.copyfileobj(source, target)

        return jsonify({'success': True, 'message': f'Сервер {server_name} создан!'})
    except Exception as e:
        import traceback
        traceback.print_exc()
        if os.path.exists(dst):
            shutil.rmtree(dst, ignore_errors=True)
        return jsonify({'success': False, 'error': str(e)}), 500


# ---------------------------------------------------------------------------
# Version
# ---------------------------------------------------------------------------

@bp.route('/get_version')
def get_version():
    try:
        log = subprocess.check_output(
            ["git", "log", "--pretty=format:%s"],
            encoding="utf-8",
        ).splitlines()
        global_indices = [i for i, m in enumerate(log) if "global" in m.lower()]
        big_indices_all = [i for i, m in enumerate(log) if "big" in m.lower()]
        if global_indices:
            major = len(global_indices)
            after = log[:global_indices[0]]
            big_after = [i for i, m in enumerate(after) if "big" in m.lower()]
            minor = len(big_after)
            patch = big_after[0] if big_after else len(after)
        else:
            major = 0
            minor = len(big_indices_all)
            patch = big_indices_all[0] if big_indices_all else len(log)
        return jsonify({"version": f"{major}.{minor}.{patch}"})
    except Exception as ex:
        return jsonify({"error": str(ex)}), 500
