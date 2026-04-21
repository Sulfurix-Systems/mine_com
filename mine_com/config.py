import os

MINECRAFT_SERVERS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
RAMDISK_PATH = '/mnt/ramdisk'
LOGS_DIR = os.path.join(MINECRAFT_SERVERS_DIR, "logs")
BACKUP_BASE = '/mnt/raid/minecraft'
BACKUP_KEEP = 10
BACKUP_THREADS = 28

IGNORED_DIRS = frozenset({"mine_com", "logs", ".git", "precreated_server_prefab"})

USERNAME = 'admin'
PASSWORD = 'password123'
SECRET_KEY = 'supersecretkey123'
MAX_UPLOAD_SIZE = 4 * 1024 * 1024 * 1024  # 4 GB

os.makedirs(LOGS_DIR, exist_ok=True)
