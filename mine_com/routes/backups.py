"""Backup creation, restore, and progress endpoints."""
import os
import shutil
import threading

from flask import Blueprint, jsonify, request

import state
from config import BACKUP_BASE, MINECRAFT_SERVERS_DIR
from routes.auth import login_required
from services.backup_service import (
    extract_zst_tar_with_progress_and_start,
    start_backup_async,
)

bp = Blueprint('backups', __name__)


@bp.route('/server/<server_name>/backup', methods=['POST'])
@login_required
def backup_only(server_name):
    if not start_backup_async(server_name, backup_and_stop=False):
        return jsonify({'success': False, 'error': 'Бекап уже выполняется!'}), 409
    return jsonify({'success': True, 'message': 'Бекап запущен.'})


@bp.route('/server/<server_name>/backup_and_stop', methods=['POST'])
@login_required
def backup_and_stop(server_name):
    if not start_backup_async(server_name, backup_and_stop=True):
        return jsonify({'success': False, 'error': 'Бэкап уже выполняется!'}), 409
    return jsonify({'success': True, 'message': 'Бекап и остановка запущены.'})


@bp.route('/server/<server_name>/backup_status')
@login_required
def backup_status_endpoint(server_name):
    status = state.backup_status.get(server_name, "idle")
    result = state.backup_result.get(server_name, {})
    return jsonify({'status': status, **result})


@bp.route('/server/<server_name>/backups', methods=['GET'])
@login_required
def list_backups(server_name):
    backup_dir = os.path.join(BACKUP_BASE, server_name, 'backups')
    if not os.path.isdir(backup_dir):
        return jsonify({'backups': []})
    files = sorted(
        [f for f in os.listdir(backup_dir) if f.endswith('.tar.zst')],
        reverse=True,
    )
    return jsonify({'backups': files})


@bp.route('/server/<server_name>/restore_and_start', methods=['POST'])
@login_required
def restore_and_start(server_name):
    data = request.get_json() or {}
    backup_file = data.get('backup', '').strip()
    if not backup_file:
        return jsonify({'success': False, 'error': 'Бекап не указан'}), 400

    backup_dir = os.path.abspath(os.path.join(BACKUP_BASE, server_name, 'backups'))
    backup_path = os.path.abspath(os.path.join(backup_dir, backup_file))

    # Prevent path traversal
    if not backup_path.startswith(backup_dir + os.sep):
        return jsonify({'success': False, 'error': 'Недопустимый путь к бекапу'}), 400
    if not os.path.isfile(backup_path):
        return jsonify({'success': False, 'error': 'Бекап не найден'}), 404

    world_path = os.path.join(BACKUP_BASE, server_name, 'world')
    if os.path.exists(world_path):
        shutil.rmtree(world_path)
    os.makedirs(world_path, exist_ok=True)

    threading.Thread(
        target=extract_zst_tar_with_progress_and_start,
        args=(server_name, backup_path, world_path),
        daemon=True,
    ).start()
    return jsonify({'success': True})


@bp.route('/server/<server_name>/restore_progress')
@login_required
def get_restore_progress(server_name):
    return jsonify(state.restore_progress.get(server_name, {"status": "idle", "progress": 0}))
