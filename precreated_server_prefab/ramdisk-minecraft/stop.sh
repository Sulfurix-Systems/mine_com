#!/bin/bash
set -e

# Prefer Docker Compose v2 plugin, fallback to legacy docker-compose binary.
resolve_compose_cmd() {
    if docker compose version >/dev/null 2>&1; then
        COMPOSE_CMD=(docker compose)
    elif command -v docker-compose >/dev/null 2>&1; then
        COMPOSE_CMD=(docker-compose)
    else
        echo "Docker Compose is not installed" >&2
        exit 127
    fi
}

resolve_compose_cmd

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
SERVER_NAME="$(basename "$(dirname "$SCRIPT_DIR")")"
RAMDISK_PATH="/mnt/ramdisk/${SERVER_NAME}_world"

echo "🛑 Остановка сервера: ${SERVER_NAME}"

# Останавливаем контейнер, если есть docker
if command -v docker &> /dev/null; then
    echo "[1/2] Остановка Docker-контейнера..."
    "${COMPOSE_CMD[@]}" -f "${SCRIPT_DIR}/docker-compose.yml" down
else
    echo "[1/2] Пропуск: Docker не установлен"
fi

# Размонтируем RAM-диск если смонтирован
echo "[2/2] Проверка и размонтирование RAM-диска..."
if mountpoint -q "$RAMDISK_PATH"; then
    sudo umount "$RAMDISK_PATH"
    echo "✅ RAM-диск успешно размонтирован"
else
    echo "ℹ️ RAM-диск не был смонтирован, ничего не делаем"
fi

echo -e "\n✅ Сервер \e[1;32m${SERVER_NAME}\e[0m полностью остановлен"