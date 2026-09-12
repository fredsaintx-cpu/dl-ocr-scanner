#!/bin/bash
# MINDEE DL SCANNER - Mac/Linux Launcher

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SCRIPT="$SCRIPT_DIR/run10.py"

if command -v python3 &>/dev/null; then PY=python3
elif command -v python &>/dev/null; then PY=python
else echo "ERROR: Python 3 not found."; exit 1; fi

# Install all required deps first (certifi MUST be first for SSL to work on Mac)
echo "Checking dependencies..."
$PY -m pip install certifi -q
$PY -m pip install mindee rich pillow -q

echo ""
echo "Starting Mindee DL Scanner..."
echo ""

if [ -n "$1" ]; then "$PY" "$SCRIPT" "$1"
else "$PY" "$SCRIPT"; fi
