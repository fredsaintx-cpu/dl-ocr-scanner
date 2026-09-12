#!/bin/bash
# MINDEE DL SCANNER - Mac/Linux Launcher
# Required: pip3 install mindee rich pillow
# Optional: pip3 install opencv-python pytesseract pyzbar numpy

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SCRIPT="$SCRIPT_DIR/run10.py"

if command -v python3 &>/dev/null; then PY=python3
elif command -v python &>/dev/null; then PY=python
else echo "ERROR: Python 3 not found."; exit 1; fi

$PY -c "import mindee" 2>/dev/null || $PY -m pip install mindee
$PY -c "import rich" 2>/dev/null || $PY -m pip install rich
$PY -c "import PIL" 2>/dev/null || $PY -m pip install pillow

if [ -n "$1" ]; then "$PY" "$SCRIPT" "$1"
else "$PY" "$SCRIPT"; fi
