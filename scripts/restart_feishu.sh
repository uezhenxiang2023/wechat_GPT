#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="${ROOT_DIR}/.venv_314"
PORT="${PORT:-7777}"

cd "${ROOT_DIR}"

if [ ! -f "${VENV_DIR}/bin/activate" ]; then
  echo "Virtualenv not found: ${VENV_DIR}"
  exit 1
fi

source "${VENV_DIR}/bin/activate"

if command -v zsh >/dev/null 2>&1; then
  # Read TOS-related exports from the user's zsh environment without sourcing
  # zsh-specific config directly inside this bash script.
  while IFS='=' read -r key value; do
    case "${key}" in
      TOS_ACCESS_KEY|TOS_SECRET_KEY)
        export "${key}=${value}"
        ;;
    esac
  done < <(zsh -ic 'env | grep -E "^(TOS_ACCESS_KEY|TOS_SECRET_KEY)=" 2>/dev/null')
fi

if [ "${MEDIA_STORE_PROVIDER:-}" = "tos" ] || [ -n "${TOS_ACCESS_KEY:-}" ] || [ -n "${TOS_SECRET_KEY:-}" ]; then
  if [ -z "${TOS_ACCESS_KEY:-}" ] || [ -z "${TOS_SECRET_KEY:-}" ]; then
    echo "Warning: TOS mode may be enabled, but TOS_ACCESS_KEY / TOS_SECRET_KEY are not fully set."
  fi
fi

PIDS="$(lsof -tiTCP:${PORT} -sTCP:LISTEN 2>/dev/null || true)"
if [ -n "${PIDS}" ]; then
  echo "Stopping existing listeners on port ${PORT}: ${PIDS}"
  kill ${PIDS} 2>/dev/null || true
  sleep 1

  REMAINING="$(lsof -tiTCP:${PORT} -sTCP:LISTEN 2>/dev/null || true)"
  if [ -n "${REMAINING}" ]; then
    echo "Force stopping remaining listeners on port ${PORT}: ${REMAINING}"
    kill -9 ${REMAINING} 2>/dev/null || true
  fi
fi

rm -f "${HOME}/.gunicorn/gunicorn.ctl"

echo "Starting Feishu webhook service on port ${PORT}..."
exec gunicorn -w 1 -k gevent -b "0.0.0.0:${PORT}" --timeout 180 \
  --access-logfile logs/access.log \
  --error-logfile logs/error.log \
  app:application
