#!/usr/bin/env bash
# setup.sh — sets up the bot using Python 3.11
set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

echo "==> Finding Python 3.11..."
if [ -x "/opt/homebrew/bin/python3.11" ]; then
    PY="/opt/homebrew/bin/python3.11"
elif command -v python3.11 &>/dev/null; then
    PY="$(command -v python3.11)"
else
    echo "ERROR: python3.11 not found."
    exit 1
fi

echo "    Using: $($PY --version)"

echo "==> Creating clean virtual environment..."
rm -rf .venv
$PY -m venv .venv

echo "==> Installing dependencies..."
.venv/bin/pip install --upgrade pip --quiet
.venv/bin/pip install -r requirements.txt

echo ""
echo "✅ Setup complete!"
echo ""
