@echo off
title DL Tools - Dependency Installer
echo ============================================================
echo  DL Tools - Installing Dependencies
echo ============================================================
echo.

echo [1/3] Installing Python packages...
pip install requests pillow opencv-python pytesseract pyzbar mindee rich numpy
if %errorlevel% neq 0 (
    echo ERROR: pip install failed. Make sure Python is installed and in PATH.
    pause
    exit /b 1
)

echo.
echo [2/3] Checking Tesseract OCR...
if exist "C:\Program Files\Tesseract-OCR\tesseract.exe" (
    echo Tesseract already installed at C:\Program Files\Tesseract-OCR\
) else (
    echo Tesseract NOT found. Downloading installer...
    curl -L -o "%TEMP%\tesseract-installer.exe" "https://github.com/UB-Mannheim/tesseract/releases/download/v5.3.3.20231005/tesseract-ocr-w64-setup-5.3.3.20231005.exe"
    echo Running Tesseract installer (install to default location: C:\Program Files\Tesseract-OCR\)...
    start /wait "" "%TEMP%\tesseract-installer.exe"
)

echo.
echo [3/3] Downloading face detection model files...
python -c "
import os, urllib.request
model = os.path.expanduser('~') + '/face_deploy.prototxt'
weights = os.path.expanduser('~') + '/face_res10.caffemodel'
if not os.path.exists(model):
    print('Downloading face_deploy.prototxt...')
    urllib.request.urlretrieve('https://raw.githubusercontent.com/opencv/opencv/master/samples/dnn/face_detector/deploy.prototxt', model)
    print('Done.')
else:
    print('face_deploy.prototxt already exists.')
if not os.path.exists(weights):
    print('Downloading face_res10.caffemodel...')
    urllib.request.urlretrieve('https://raw.githubusercontent.com/opencv/opencv_3rdparty/dnn_samples_face_detector_20170830/res10_300x300_ssd_iter_140000.caffemodel', weights)
    print('Done.')
else:
    print('face_res10.caffemodel already exists.')
print('All model files ready.')
"

echo.
echo ============================================================
echo  All dependencies installed successfully!
echo  You can now run:
echo    RUN_CLIPDROP_EXPAND.bat  - Background expander
echo    run_mindee_dl.bat        - DL OCR auto-renamer
echo    run_kling_ui.bat         - Kling UI
echo ============================================================
pause
