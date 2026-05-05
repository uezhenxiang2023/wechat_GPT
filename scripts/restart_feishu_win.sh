#!/usr/bin/env bash
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"
source .venv/Scripts/activate
mkdir -p logs
echo "Starting Feishu webhook service on port 7777..."
waitress-serve --port=7777 app:application