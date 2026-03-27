#!/bin/bash
# Dafodil — launch wrapper
# Activates the venv and starts main.py
# Run with:  bash run.sh

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="/home/dafodil/venv"

if [ ! -f "$VENV_DIR/bin/python3" ]; then
    echo "ERROR: venv not found at $VENV_DIR"
    echo "Run setup first:  bash $SCRIPT_DIR/setup.sh"
    exit 1
fi

echo "Starting Dafodil..."
exec "$VENV_DIR/bin/python3" "$SCRIPT_DIR/main.py" "$@"
