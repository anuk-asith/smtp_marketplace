#!/usr/bin/env bash
# run.sh — start the bot
set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

if [ ! -f ".venv/bin/python3" ]; then
    echo "ERROR: .venv not found. Run ./setup.sh first."
    exit 1
fi

if [ ! -f ".env" ]; then
    echo "ERROR: .env not found."
    echo "Run: cp .env.example .env  then fill in your values."
    exit 1
fi

echo "Starting SMTP Marketplace bot..."
exec .venv/bin/python3 -m app.main
