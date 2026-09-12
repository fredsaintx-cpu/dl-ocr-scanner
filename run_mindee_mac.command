#!/bin/bash
# run_mindee_mac.command
# Double-clickable on Mac - just put it next to run10.py
cd "$(dirname "$0")"

# Find Python
if command -v python3 &>/dev/null; then PY=python3
elif command -v python &>/dev/null; then PY=python
else
    osascript -e 'display alert "Python 3 not found" message "Install Python from https://python.org then try again." as critical'
    exit 1
fi

# Auto-install required deps
$PY -c "import mindee" 2>/dev/null || { echo "Installing mindee..."; $PY -m pip install mindee; }
$PY -c "import rich" 2>/dev/null || { echo "Installing rich..."; $PY -m pip install rich; }
$PY -c "import PIL" 2>/dev/null || { echo "Installing Pillow..."; $PY -m pip install pillow; }

echo ""
echo "Starting Mindee DL Scanner..."
echo ""
$PY "$(dirname "$0")/run10.py"
echo ""
echo "Press Enter to close..."
read
