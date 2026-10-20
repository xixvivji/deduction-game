#!/bin/sh
set -eu
cd "$(dirname "$0")"
exec .venv/bin/python -m uvicorn game.server:app --host "${GAME_HOST:-127.0.0.1}" --port "${GAME_PORT:-8200}"
