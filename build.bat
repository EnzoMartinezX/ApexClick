@echo off
echo Installing dependencies...
pip install -r requirements.txt
if %ERRORLEVEL% NEQ 0 (
    echo Error installing dependencies. Please ensure Python is installed and added to PATH.
    pause
    exit /b
)

echo.
echo Building executable...
pyinstaller --noconsole --onefile --name "Autoclicker" autoclicker.py
if %ERRORLEVEL% NEQ 0 (
    echo Error building executable.
    pause
    exit /b
)

echo.
echo Build complete!
echo You can find the executable in the "dist" folder.
pause
