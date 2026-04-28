#!/bin/bash

SCRIPT_PATH="$(realpath "$0")"
APP_DIR="$(dirname "$SCRIPT_PATH")"   # mine_com/ — каталог с app.py и venv
REPO_DIR="$(dirname "$APP_DIR")"      # git-корень (родитель APP_DIR, там лежит .git)
LOGS_DIR="$REPO_DIR/logs"             # совпадает с LOGS_DIR из config.py

# Разрешаем git работать в репозитории (актуально при запуске через sudo)
git config --global --add safe.directory "$REPO_DIR" || true

# ---------------------------------------------------------------------------
# Режим запуска: без --child — запускаем себя как фоновый демон
# ---------------------------------------------------------------------------
if [[ "$1" != "--child" ]]; then
  sudo nohup bash "$SCRIPT_PATH" --child >> "$APP_DIR/auto_update.log" 2>&1 &
  echo "Монитор запущен (PID $!), лог: $APP_DIR/auto_update.log"
  exit 0
fi

# ---------------------------------------------------------------------------
# Дочерний процесс: управление приложением и авто-обновление
# ---------------------------------------------------------------------------
mkdir -p "$LOGS_DIR"

GIT_BRANCH="main"
APP_PID_FILE="$APP_DIR/flask.pid"
LOG_FILE="$APP_DIR/server.log"

log() {
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"
}

recreate_venv() {
  log "Пересоздаю виртуальное окружение..."
  rm -rf "$APP_DIR/venv"
  python3 -m venv "$APP_DIR/venv"
}

ensure_venv() {
  if [ ! -d "$APP_DIR/venv" ]; then
    log "Виртуальное окружение не найдено, создаю..."
    python3 -m venv "$APP_DIR/venv"
  fi

  if ! "$APP_DIR/venv/bin/python3" -m pip --version >/dev/null 2>&1; then
    log "ПРЕДУПРЕЖДЕНИЕ: pip внутри venv поврежден, пытаюсь восстановить через ensurepip..."
    if ! "$APP_DIR/venv/bin/python3" -m ensurepip --upgrade >/dev/null 2>&1; then
      log "ПРЕДУПРЕЖДЕНИЕ: ensurepip не помог, пересоздаю venv."
      recreate_venv
    fi
  fi

  if ! "$APP_DIR/venv/bin/python3" -m pip --version >/dev/null 2>&1; then
    log "ПРЕДУПРЕЖДЕНИЕ: pip всё ещё недоступен после восстановления, пересоздаю venv."
    recreate_venv
  fi
}

install_deps() {
  local req="$APP_DIR/requirements.txt"
  local venv_python="$APP_DIR/venv/bin/python3"
  if [ ! -f "$req" ]; then
    log "ПРЕДУПРЕЖДЕНИЕ: requirements.txt не найден в $req"
    return
  fi
  log "Проверка и установка зависимостей из requirements.txt..."
  "$venv_python" -m ensurepip --upgrade >/dev/null 2>&1 || true
  "$venv_python" -m pip install --quiet --upgrade pip
  local pip_exit_code=$?
  if [ $pip_exit_code -ne 0 ]; then
    log "ПРЕДУПРЕЖДЕНИЕ: обновление pip завершилось с кодом $pip_exit_code. Пересоздаю venv и повторяю."
    recreate_venv
    venv_python="$APP_DIR/venv/bin/python3"
    "$venv_python" -m ensurepip --upgrade >/dev/null 2>&1 || true
    "$venv_python" -m pip install --quiet --upgrade pip
  fi
  "$venv_python" -m pip install --quiet -r "$req"
  local exit_code=$?
  if [ $exit_code -ne 0 ]; then
    log "ОШИБКА: pip install завершился с кодом $exit_code. Проверьте requirements.txt."
    exit 1
  fi
  log "Зависимости установлены."
  # Гарантируем права на исполнение скриптов после git pull
  chmod +x "$SCRIPT_PATH"
  find "$APP_DIR" -maxdepth 1 -name "*.sh" -exec chmod +x {} \;
}

start_app() {
  cd "$APP_DIR"
  nohup "$APP_DIR/venv/bin/python3" app.py >> "$LOG_FILE" 2>&1 &
  echo $! > "$APP_PID_FILE"
  log "Приложение запущено (PID $(cat "$APP_PID_FILE"))"
}

stop_app() {
  if [ -f "$APP_PID_FILE" ]; then
    PID="$(cat "$APP_PID_FILE")"
    if kill "$PID" 2>/dev/null; then
      log "Приложение остановлено (PID $PID)"
    fi
    rm -f "$APP_PID_FILE"
  fi
}

ensure_venv
install_deps
start_app

while true; do
  git config --global --add safe.directory "$REPO_DIR" || true
  cd "$REPO_DIR"
  git fetch origin "$GIT_BRANCH" --quiet

  LOCAL=$(git rev-parse "$GIT_BRANCH")
  REMOTE=$(git rev-parse "origin/$GIT_BRANCH")

  if [ "$LOCAL" != "$REMOTE" ]; then
    log "Обнаружено обновление (${LOCAL:0:7} → ${REMOTE:0:7}). Перезапуск..."
    stop_app
    git pull origin "$GIT_BRANCH"
    install_deps
    start_app
  fi

  sleep 60
done