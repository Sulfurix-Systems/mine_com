"""File management routes: server.properties, JVM args, mods, configs."""
import os
import shutil
import zipfile

from flask import Blueprint, jsonify, request
from werkzeug.utils import secure_filename

from config import MINECRAFT_SERVERS_DIR
from routes.auth import login_required

bp = Blueprint('files', __name__)


def _safe_path(base: str, rel: str):
    """Return absolute path only if it resolves within *base*, else None."""
    base = os.path.abspath(base)
    abs_path = os.path.normpath(os.path.join(base, rel))
    if not abs_path.startswith(base + os.sep) and abs_path != base:
        return None
    return abs_path


# ---------------------------------------------------------------------------
# server.properties
# ---------------------------------------------------------------------------

@bp.route('/server/<server_name>/properties', methods=['GET'])
@login_required
def get_properties(server_name):
    prop_path = os.path.join(
        MINECRAFT_SERVERS_DIR, server_name, "ramdisk-minecraft", "server.properties"
    )
    if not os.path.isfile(prop_path):
        return jsonify({"error": "Файл не найден"}), 404
    with open(prop_path, "r", encoding="utf-8", errors="replace") as f:
        text = f.read()
    return jsonify({"text": text})


@bp.route('/server/<server_name>/properties', methods=['POST'])
@login_required
def save_properties(server_name):
    data = request.get_json() or {}
    text = data.get("text", "")
    prop_path = os.path.join(
        MINECRAFT_SERVERS_DIR, server_name, "ramdisk-minecraft", "server.properties"
    )
    try:
        with open(prop_path, "w", encoding="utf-8") as f:
            f.write(text)
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ---------------------------------------------------------------------------
# user_jvm_args.txt
# ---------------------------------------------------------------------------

@bp.route('/server/<server_name>/jvmargs', methods=['GET'])
@login_required
def get_jvmargs(server_name):
    jvm_path = os.path.join(
        MINECRAFT_SERVERS_DIR, server_name, "ramdisk-minecraft", "user_jvm_args.txt"
    )
    if not os.path.isfile(jvm_path):
        return jsonify({"error": "Файл не найден"}), 404
    with open(jvm_path, "r", encoding="utf-8", errors="replace") as f:
        text = f.read()
    return jsonify({"text": text})


@bp.route('/server/<server_name>/jvmargs', methods=['POST'])
@login_required
def save_jvmargs(server_name):
    data = request.get_json() or {}
    text = data.get("text", "")
    jvm_path = os.path.join(
        MINECRAFT_SERVERS_DIR, server_name, "ramdisk-minecraft", "user_jvm_args.txt"
    )
    try:
        with open(jvm_path, "w", encoding="utf-8") as f:
            f.write(text)
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ---------------------------------------------------------------------------
# Mods
# ---------------------------------------------------------------------------

@bp.route('/server/<server_name>/add_mod', methods=['POST'])
@login_required
def add_mod(server_name):
    mod_file = request.files.get('mod_file')
    if not mod_file:
        return jsonify({'success': False, 'error': 'Файл не выбран'}), 400
    filename = secure_filename(mod_file.filename)
    if not filename.endswith('.jar'):
        return jsonify({'success': False, 'error': 'Неверный .jar файл'}), 400
    mods_dir = os.path.join(MINECRAFT_SERVERS_DIR, server_name, "neoforge-server", "mods")
    os.makedirs(mods_dir, exist_ok=True)
    target_path = os.path.join(mods_dir, filename)
    if os.path.exists(target_path):
        return jsonify({'success': False, 'error': 'Файл уже существует'}), 400
    try:
        mod_file.save(target_path)
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ---------------------------------------------------------------------------
# Config files
# ---------------------------------------------------------------------------

@bp.route('/server/<server_name>/add_config', methods=['POST'])
@login_required
def add_config(server_name):
    config_file = request.files.get('config_file')
    if not config_file:
        return jsonify({'success': False, 'error': 'Файл не выбран'}), 400
    allowed_ext = ('.zip', '.toml', '.json', '.cfh', '.json5')
    filename = secure_filename(config_file.filename)
    if not any(filename.endswith(ext) for ext in allowed_ext):
        return jsonify({'success': False, 'error': 'Недопустимый тип файла'}), 400
    configs_dir = os.path.join(MINECRAFT_SERVERS_DIR, server_name, "neoforge-server", "config")
    os.makedirs(configs_dir, exist_ok=True)
    try:
        if filename.endswith('.zip'):
            with zipfile.ZipFile(config_file.stream) as zf:
                for member in zf.infolist():
                    if member.is_dir():
                        continue
                    relpath = os.path.normpath(member.filename)
                    if any(part in ('..', '') for part in relpath.split(os.sep)):
                        continue
                    target_path = os.path.join(configs_dir, relpath)
                    if os.path.exists(target_path):
                        continue
                    os.makedirs(os.path.dirname(target_path), exist_ok=True)
                    with zf.open(member) as source, open(target_path, 'wb') as target:
                        shutil.copyfileobj(source, target)
        else:
            target_path = os.path.join(configs_dir, filename)
            if os.path.exists(target_path):
                return jsonify({'success': False, 'error': 'Файл уже существует'}), 400
            config_file.save(target_path)
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/server/<server_name>/config/list', methods=['GET'])
@login_required
def list_config_files(server_name):
    rel_path = request.args.get('path', '')
    config_root = os.path.abspath(
        os.path.join(MINECRAFT_SERVERS_DIR, server_name, 'neoforge-server', 'config')
    )
    abs_path = _safe_path(config_root, rel_path)
    if abs_path is None:
        return jsonify({'error': 'Недопустимый путь'}), 400
    if not os.path.isdir(abs_path):
        return jsonify({'error': 'Папка не найдена'}), 404

    items = []
    for name in sorted(os.listdir(abs_path)):
        full = os.path.join(abs_path, name)
        items.append({'name': name, 'type': 'dir' if os.path.isdir(full) else 'file'})

    parent = None
    if abs_path != config_root:
        rel_parent = os.path.relpath(os.path.dirname(abs_path), config_root)
        parent = '' if rel_parent == '.' else rel_parent

    return jsonify({
        'items': items,
        'parent': parent,
        'current': os.path.relpath(abs_path, config_root),
    })


@bp.route('/server/<server_name>/config/file', methods=['GET', 'POST'])
@login_required
def config_file(server_name):
    rel_path = request.args.get('path', '')
    config_root = os.path.abspath(
        os.path.join(MINECRAFT_SERVERS_DIR, server_name, 'neoforge-server', 'config')
    )
    abs_path = _safe_path(config_root, rel_path)
    if abs_path is None:
        return jsonify({'error': 'Недопустимый путь'}), 400

    if request.method == 'GET':
        if not os.path.isfile(abs_path):
            return jsonify({'error': 'Файл не найден'}), 404
        with open(abs_path, 'r', encoding='utf-8', errors='replace') as f:
            text = f.read()
        return jsonify({'text': text, 'filename': os.path.basename(abs_path)})
    else:
        data = request.get_json() or {}
        text = data.get('text', '')
        try:
            with open(abs_path, 'w', encoding='utf-8') as f:
                f.write(text)
            return jsonify({'success': True})
        except Exception as e:
            return jsonify({'success': False, 'error': str(e)}), 500
