"""Backup creation, restore, and scheduled auto-backup."""
import datetime
import os
import re
import subprocess
import tarfile
import threading
import time

import zstandard as zstd

import state
from config import BACKUP_BASE, BACKUP_KEEP, BACKUP_THREADS, MINECRAFT_SERVERS_DIR, RAMDISK_PATH
from services.server_manager import (
    ensure_server_runtime_scripts,
    get_all_server_names,
    get_compose_command,
    is_server_running,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def cleanup_old_backups(backup_dir: str, keep: int = BACKUP_KEEP) -> None:
    """Delete oldest backups beyond *keep* count; silently skips if dir is missing."""
    if not os.path.isdir(backup_dir):
        return
    pattern = r'^world_\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}\.tar\.zst$'
    files = [
        os.path.join(backup_dir, f)
        for f in os.listdir(backup_dir)
        if re.match(pattern, f)
    ]
    files.sort(key=os.path.getmtime, reverse=True)
    for f in files[keep:]:
        try:
            os.remove(f)
            print(f"[Backup] Deleted old backup: {f}")
        except Exception as ex:
            print(f"[Backup] Failed to delete {f}: {ex}")


# ---------------------------------------------------------------------------
# Async backup
# ---------------------------------------------------------------------------

def start_backup_async(server_name: str, backup_and_stop: bool = False) -> bool:
    """
    Start a backup in a background thread.
    Returns False immediately if a backup is already running.
    Bug-fix: cleanup_old_backups is called AFTER makedirs.
    """
    if state.backup_status.get(server_name) == "in_progress":
        return False
    state.backup_status[server_name] = "in_progress"

    def run_backup() -> None:
        try:
            world_ramdisk = os.path.join(RAMDISK_PATH, f"{server_name}_world")
            backup_dir = os.path.join(BACKUP_BASE, server_name, 'backups')
            compose_path = os.path.join(
                MINECRAFT_SERVERS_DIR, server_name, "ramdisk-minecraft", "docker-compose.yml"
            )

            # --- Step 1 (backup_and_stop): stop the container FIRST so Minecraft
            #     flushes the world to ramdisk before we archive it.
            if backup_and_stop:
                state.backup_status[server_name] = "stopping"
                compose_cmd = get_compose_command()
                stop_result = subprocess.run(
                    compose_cmd + ["-f", compose_path, "down"],
                    capture_output=True,
                )
                if stop_result.returncode != 0:
                    state.backup_status[server_name] = "error"
                    state.backup_result[server_name] = {
                        'filename': None, 'success': False,
                        'error': (
                            f"docker compose down failed (code {stop_result.returncode})\n"
                            f"stderr: {stop_result.stderr.decode(errors='ignore')}"
                        ),
                    }
                    return
                state.backup_status[server_name] = "in_progress"

            # --- Step 2: backup from ramdisk (world is now fully saved)
            os.makedirs(backup_dir, exist_ok=True)
            cleanup_old_backups(backup_dir)

            if not os.path.isdir(world_ramdisk):
                state.backup_status[server_name] = "error"
                state.backup_result[server_name] = {
                    'filename': None, 'success': False,
                    'error': 'RAM world dir not found',
                }
                return

            timestamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d_%H-%M-%S")
            backup_name = f"world_{timestamp}.tar.zst"
            backup_path = os.path.join(backup_dir, backup_name)

            cmd = (
                f'tar -cf - -C "{world_ramdisk}" . '
                f'| zstd -T{BACKUP_THREADS} -1 -o "{backup_path}"'
            )
            ret = subprocess.run(cmd, shell=True, capture_output=True)
            if ret.returncode != 0:
                state.backup_status[server_name] = "error"
                state.backup_result[server_name] = {
                    'filename': None, 'success': False,
                    'error': (
                        f'Archive error, code {ret.returncode}\n'
                        f'stdout: {ret.stdout.decode(errors="ignore")}\n'
                        f'stderr: {ret.stderr.decode(errors="ignore")}'
                    ),
                }
                return

            # --- Step 3 (backup_and_stop): unmount ramdisk after backup is done
            if backup_and_stop:
                state.backup_status[server_name] = "unmounting"
                umount_result = subprocess.run(
                    ["sudo", "umount", world_ramdisk],
                    capture_output=True,
                )
                if umount_result.returncode != 0:
                    # Non-fatal: backup succeeded; log the warning but don't fail
                    print(
                        f"[Backup] Warning: umount failed for {world_ramdisk}: "
                        f"{umount_result.stderr.decode(errors='ignore')}"
                    )

            state.backup_status[server_name] = "idle"
            state.backup_result[server_name] = {
                'filename': backup_name, 'success': True, 'error': None,
            }
        except Exception as e:
            state.backup_status[server_name] = "error"
            state.backup_result[server_name] = {
                'filename': None, 'success': False, 'error': str(e),
            }

    threading.Thread(target=run_backup, daemon=True).start()
    return True


# ---------------------------------------------------------------------------
# Restore (extract .tar.zst then start)
# ---------------------------------------------------------------------------

def extract_zst_tar_with_progress_and_start(
    server_name: str, backup_path: str, world_path: str
) -> None:
    """Extract a .tar.zst backup with progress tracking, then run start.sh."""
    try:
        total_size = os.path.getsize(backup_path)
        state.restore_progress[server_name] = {
            "status": "extracting",
            "backup": os.path.basename(backup_path),
            "progress": 0,
            "total": total_size,
        }
        extracted_size = 0
        with open(backup_path, 'rb') as compressed:
            dctx = zstd.ZstdDecompressor()
            with dctx.stream_reader(compressed) as reader:
                with tarfile.open(fileobj=reader, mode='r|') as tar:
                    for member in tar:
                        tar.extract(member, world_path)
                        if getattr(member, "size", None):
                            extracted_size += member.size
                            prog = min(int(extracted_size / total_size * 100), 100)
                            state.restore_progress[server_name]["progress"] = prog

        state.restore_progress[server_name] = {
            "status": "done",
            "backup": os.path.basename(backup_path),
            "progress": 100,
            "total": total_size,
        }
        script_path = os.path.join(
            MINECRAFT_SERVERS_DIR, server_name, "ramdisk-minecraft", "start.sh"
        )
        ensure_server_runtime_scripts(server_name)
        subprocess.Popen(['bash', script_path])
    except Exception as e:
        state.restore_progress[server_name] = {
            "status": "error",
            "backup": os.path.basename(backup_path),
            "progress": 0,
            "error": str(e),
        }


# ---------------------------------------------------------------------------
# Scheduled auto-backup
# ---------------------------------------------------------------------------

def _wait_until_next_half_hour() -> None:
    now = datetime.datetime.now(datetime.timezone.utc)
    if now.minute < 30:
        next_time = now.replace(minute=30, second=0, microsecond=0)
    else:
        next_time = (now + datetime.timedelta(hours=1)).replace(
            minute=0, second=0, microsecond=0
        )
    delta = (next_time - now).total_seconds()
    if delta > 0:
        time.sleep(delta)


def autobackup_loop() -> None:
    print("[Autobackup] Thread started – waiting 60 s before first run")
    time.sleep(60)

    for server in get_all_server_names():
        if is_server_running(server) and state.backup_status.get(server) != "in_progress":
            print(f"[Autobackup] First run: backing up {server}")
            start_backup_async(server, backup_and_stop=False)

    while True:
        _wait_until_next_half_hour()
        print(f"[Autobackup] {datetime.datetime.now(datetime.timezone.utc)} – cycle start")
        for server in get_all_server_names():
            if is_server_running(server) and state.backup_status.get(server) != "in_progress":
                print(f"[Autobackup] Backing up {server}")
                start_backup_async(server, backup_and_stop=False)


def start_autobackup_thread() -> None:
    threading.Thread(target=autobackup_loop, daemon=True).start()
