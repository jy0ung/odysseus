#!/usr/bin/env bash
set -Eeuo pipefail

MODE="auto"
BIND_HOST="127.0.0.1"
PORT="7000"

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
    -h|--help)
      cat <<'EOF'
Start Odysseus inside WSL.

Usage:
  bash scripts/start-wsl.sh [--mode auto|native|docker] [--bind 127.0.0.1] [--port 7000]

Modes:
  auto    Use an existing Docker Compose deployment when present; otherwise use venv.
  native  Start python -m uvicorn from venv inside WSL.
  docker  Start docker compose in detached mode.
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

  info "Installing/updating Python dependencies"
  venv/bin/python -m pip install --upgrade pip --quiet
  venv/bin/python -m pip install -r requirements.txt

  info "Running first-time setup"
  venv/bin/python setup.py
}

start_native() {
  if [[ ! -x venv/bin/python ]]; then
    ensure_native_env
  elif [[ ! -f data/app.db || ! -f .env ]]; then
    info "Completing first-time setup"
    venv/bin/python setup.py
  fi

  info "Starting Odysseus at http://localhost:${PORT}"
  echo "Press Ctrl+C in this WSL window to stop."
  exec venv/bin/python -m uvicorn app:app --host "$BIND_HOST" --port "$PORT"
}

if [[ "$MODE" == "docker" ]]; then
  start_docker
elif [[ "$MODE" == "native" ]]; then
  start_native
else
  if has_docker_compose && docker_is_running && compose_has_existing_app; then
    start_docker
  else
    start_native
  fi
fi
