# Shared mutable state – module-level singletons shared across all routes and services.

busy_pids: dict = {}          # server_name -> pid
restore_progress: dict = {}   # server_name -> {status, progress, ...}
backup_status: dict = {}      # server_name -> "idle"|"in_progress"|"stopping"|"error"
backup_result: dict = {}      # server_name -> {filename, success, error}
