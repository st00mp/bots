#!/usr/bin/env bash
# v0x — lancement du bot Telegram
set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Chargement .env si présent
if [ -f ".env" ]; then
    set -a
    source .env
    set +a
fi

exec venv/bin/python3 bot.py "$@"
