#!/usr/bin/env bash
set -Eeuo pipefail

MODE="auto"
BIND_HOST="127.0.0.1"
PORT="7000"
DETACH="0"
LOG_FILE="logs/odysseus-wsl.log"
PID_FILE="logs/odysseus-wsl.pid"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --mode)
      MODE="${2:-auto}"
      shift 2
      ;;
    --bind)
      BIND_HOST="${2:-127.0.0.1}"
      shift 2
      ;;
    --port)
      PORT="${2:-7000}"
      shift 2
      ;;
    --detach)
      DETACH="1"
      shift
      ;;
    --foreground)
      DETACH="0"
      shift
      ;;
    -h|--help)
      cat <<'EOF'
Start Odysseus inside WSL.

Usage:
  bash scripts/start-wsl.sh [--mode auto|native|docker] [--bind 127.0.0.1] [--port 7000] [--detach|--foreground]

Modes:
  auto    Use an existing Docker Compose deployment when present; otherwise use venv.
  native  Start python -m uvicorn from venv inside WSL.
  docker  Start docker compose in detached mode.

Options:
  --detach      Keep native uvicorn running after the launcher exits.
  --foreground  Run native uvicorn in the current shell.
EOF
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

case "$MODE" in
  auto|native|docker) ;;
  *)
    echo "Invalid --mode: $MODE" >&2
    exit 2
    ;;
esac

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

info() {
  printf '\n==> %s\n' "$*"
}

is_native_running() {
  [[ -f "$PID_FILE" ]] || return 1
  local pid
  pid="$(cat "$PID_FILE" 2>/dev/null || true)"
  [[ "$pid" =~ ^[0-9]+$ ]] || return 1
  kill -0 "$pid" >/dev/null 2>&1
}

has_docker_compose() {
  command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1
}

docker_is_running() {
  docker info >/dev/null 2>&1
}

compose_has_existing_app() {
  [[ -f docker-compose.yml ]] || return 1
  docker compose ps -q odysseus 2>/dev/null | grep -q .
}

start_docker() {
  if ! has_docker_compose; then
    echo "Docker Compose is not available in WSL." >&2
    exit 1
  fi
  if ! docker_is_running; then
    echo "Docker is not running. Start Docker Desktop or your WSL Docker daemon, then retry." >&2
    exit 1
  fi

  info "Starting Docker Compose deployment"
  APP_BIND="$BIND_HOST" APP_PORT="$PORT" docker compose up -d
  info "Odysseus is starting at http://localhost:${PORT}"
  echo "View logs with: docker compose logs -f odysseus"
}

find_python() {
  if [[ -x venv/bin/python ]]; then
    printf '%s\n' "venv/bin/python"
    return 0
  fi
  if command -v python3 >/dev/null 2>&1; then
    command -v python3
    return 0
  fi
  if command -v python >/dev/null 2>&1; then
    command -v python
    return 0
  fi
  return 1
}

ensure_native_env() {
  local python_cmd
  python_cmd="$(find_python)" || {
    echo "Python 3.11+ was not found inside WSL." >&2
    exit 1
  }

  if [[ ! -x venv/bin/python ]]; then
    info "Creating WSL virtual environment"
    "$python_cmd" -m venv venv
  fi

  if ! venv/bin/python -m pip --version >/dev/null 2>&1; then
    info "Bootstrapping pip in the WSL virtual environment"
    if ! venv/bin/python -m ensurepip --upgrade; then
      echo "Could not bootstrap pip. Install the WSL venv package, then retry:" >&2
      echo "  sudo apt update && sudo apt install -y python3-venv python3-pip" >&2
      exit 1
    fi
  fi

  if ! venv/bin/python -c "import fastapi, uvicorn" >/dev/null 2>&1; then
    info "Installing Python dependencies"
    venv/bin/python -m pip install --upgrade pip --quiet
    venv/bin/python -m pip install -r requirements.txt
  fi

  if [[ ! -f data/app.db || ! -f .env ]]; then
    info "Running first-time setup"
    venv/bin/python setup.py
  fi
}

start_native() {
  ensure_native_env

  info "Starting Odysseus at http://localhost:${PORT}"
  echo "Press Ctrl+C in this WSL window to stop."
  exec venv/bin/python -m uvicorn app:app --host "$BIND_HOST" --port "$PORT"
}

start_native_detached() {
  mkdir -p logs

  if is_native_running; then
    info "Odysseus is already running from PID $(cat "$PID_FILE")"
    echo "URL: http://localhost:${PORT}"
    echo "Logs: $LOG_FILE"
    return 0
  fi

  ensure_native_env

  info "Starting Odysseus in the background at http://localhost:${PORT}"
  {
    printf '\n[%s] Starting Odysseus on %s:%s\n' "$(date -Is)" "$BIND_HOST" "$PORT"
  } >>"$LOG_FILE"
  nohup venv/bin/python -m uvicorn app:app --host "$BIND_HOST" --port "$PORT" >>"$LOG_FILE" 2>&1 &
  local pid=$!
  printf '%s\n' "$pid" >"$PID_FILE"
  disown "$pid" 2>/dev/null || true

  sleep 3
  if is_native_running; then
    info "Odysseus is running from PID $pid"
    echo "URL: http://localhost:${PORT}"
    echo "Logs: $LOG_FILE"
    return 0
  fi

  echo "Odysseus did not stay running. Last log lines:" >&2
  tail -n 80 "$LOG_FILE" >&2 || true
  rm -f "$PID_FILE"
  exit 1
}

if [[ "$MODE" == "docker" ]]; then
  start_docker
elif [[ "$MODE" == "native" ]]; then
  if [[ "$DETACH" == "1" ]]; then
    start_native_detached
  else
    start_native
  fi
else
  if has_docker_compose && docker_is_running && compose_has_existing_app; then
    start_docker
  else
    if [[ "$DETACH" == "1" ]]; then
      start_native_detached
    else
      start_native
    fi
  fi
fi
