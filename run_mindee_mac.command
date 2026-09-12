#!/bin/bash
# MINDEE DL SCANNER - Mac/Linux Launcher
# Required: pip3 install mindee rich pillow
# Optional: pip3 install opencv-python pytesseract pyzbar numpy

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SCRIPT="$SCRIPT_DIR/run10.py"

if command -v python3 &>/dev/null; then PY=python3
elif command -v python &>/dev/null; then PY=python
else echo "ERROR: Python 3 not found."; exit 1; fi

# Fix Mac SSL certificate issue
$PY -c "import ssl; ssl.create_default_context()" 2>/dev/null || {
    echo "Fixing SSL certificates..."
    $PY -m pip install --upgrade certifi 2>/dev/null
    CERT=$($PY -c "import certifi; print(certifi.where())" 2>/dev/null)
    if [ -n "$CERT" ]; then
        export SSL_CERT_FILE="$CERT"
        export REQUESTS_CA_BUNDLE="$CERT"
    fi
}

# Also set cert env vars proactively on Mac
if [[ "$OSTYPE" == "darwin"* ]]; then
    CERT=$($PY -c "import certifi; print(certifi.where())" 2>/dev/null)
    if [ -n "$CERT" ]; then
        export SSL_CERT_FILE="$CERT"
        export REQUESTS_CA_BUNDLE="$CERT"
    fi
fi

$PY -c "import mindee" 2>/dev/null || $PY -m pip install mindee
$PY -c "import rich" 2>/dev/null || $PY -m pip install rich
$PY -c "import PIL" 2>/dev/null || $PY -m pip install pillow
$PY -m pip install certifi -q

echo ""
echo "Starting Mindee DL Scanner..."
echo ""

if [ -n "$1" ]; then "$PY" "$SCRIPT" "$1"
else "$PY" "$SCRIPT"; fi
