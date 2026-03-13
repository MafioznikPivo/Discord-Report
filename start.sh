#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

PYTHON_BIN="${PYTHON_BIN:-python3}"
VENV_DIR="${VENV_DIR:-$ROOT_DIR/.venv}"
INSTALL_DEPS="${INSTALL_DEPS:-1}"
LOG_TO_FILE="${LOG_TO_FILE:-1}"
LOG_DIR="${LOG_DIR:-$ROOT_DIR/logs}"
LOG_FILE="${LOG_FILE:-$LOG_DIR/bot.log}"

log() {
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"
}

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "Error: '$PYTHON_BIN' is not installed."
  echo "Install Python 3.11+ and try again."
  exit 1
fi

if [[ ! -f ".env" ]]; then
  echo "Error: .env file was not found in $ROOT_DIR"
  echo "Create it from .env.example before starting the bot."
  exit 1
fi

if [[ ! -f "requirements.txt" ]]; then
  echo "Error: requirements.txt not found in $ROOT_DIR"
  exit 1
fi

PYTHON_VERSION="$($PYTHON_BIN -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
PYTHON_MAJOR="${PYTHON_VERSION%%.*}"
PYTHON_MINOR="${PYTHON_VERSION##*.}"
if (( PYTHON_MAJOR < 3 || (PYTHON_MAJOR == 3 && PYTHON_MINOR < 11) )); then
  echo "Error: Python $PYTHON_VERSION detected, but 3.11+ is required."
  exit 1
fi

log "Using Python: $PYTHON_BIN ($PYTHON_VERSION)"
log "Project dir: $ROOT_DIR"

if [[ ! -d "$VENV_DIR" ]]; then
  log "Creating virtual environment at $VENV_DIR"
  "$PYTHON_BIN" -m venv "$VENV_DIR"
fi

source "$VENV_DIR/bin/activate"
log "Virtual environment activated: $VENV_DIR"

if [[ "$INSTALL_DEPS" == "1" ]]; then
  log "Installing dependencies from requirements.txt"
  python -m pip install --upgrade pip
  python -m pip install -r requirements.txt
else
  log "Skipping dependency installation (INSTALL_DEPS=$INSTALL_DEPS)"
fi

if [[ "$LOG_TO_FILE" == "1" ]]; then
  mkdir -p "$LOG_DIR"
  touch "$LOG_FILE"
  log "Logging enabled: $LOG_FILE"
  exec > >(tee -a "$LOG_FILE") 2>&1
fi

log "Starting bot..."
exec python bot.py