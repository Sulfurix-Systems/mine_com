#!/bin/bash

SCRIPT_PATH="$(realpath "$0")"
APP_DIR="$(dirname "$SCRIPT_PATH")"   # mine_com/ — каталог с app.py и venv
REPO_DIR="$(dirname "$APP_DIR")"      # git-корень (родитель APP_DIR, там лежит .git)
LOGS_DIR="$REPO_DIR/logs"             # совпадает с LOGS_DIR из config.py

# Разрешаем git работать в репозитории (актуально при запуске через sudo)
git config --global --add safe.directory "$REPO_DIR"

# ---------------------------------------------------------------------------
# Режим запуска: без --child — запускаем себя как фоновый демон
# ---------------------------------------------------------------------------
if [[ "$1" != "--child" ]]; then
  mkdir -p "$LOGS_DIR"
  sudo nohup "$SCRIPT_PATH" --child >> "$LOGS_DIR/auto_update.log" 2>&1 &
  echo "Монитор запущен (PID $!), лог: $LOGS_DIR/auto_update.log"
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

ensure_venv() {
  if [ ! -d "$APP_DIR/venv" ]; then
    log "Виртуальное окружение не найдено, создаю..."
    python3 -m venv "$APP_DIR/venv"
  fi
}

install_deps() {
  local req="$APP_DIR/requirements.txt"
  if [ ! -f "$req" ]; then
    log "ПРЕДУПРЕЖДЕНИЕ: requirements.txt не найден в $req"
    return
  fi
  log "Проверка и установка зависимостей из requirements.txt..."
  source "$APP_DIR/venv/bin/activate"
  pip install --quiet --upgrade pip
  pip install --quiet -r "$req"
  local exit_code=$?
  deactivate
  if [ $exit_code -ne 0 ]; then
    log "ОШИБКА: pip install завершился с кодом $exit_code. Проверьте requirements.txt."
    exit 1
  fi
  log "Зависимости установлены."
}

start_app() {
  source "$APP_DIR/venv/bin/activate"
  cd "$APP_DIR"
  nohup python3 app.py >> "$LOG_FILE" 2>&1 &
  echo $! > "$APP_PID_FILE"
  deactivate
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
  git config --global --add safe.directory "$REPO_DIR"
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