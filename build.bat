@echo off
echo Installing dependencies...
pip install -r requirements.txt
if %ERRORLEVEL% NEQ 0 (
    echo Error installing dependencies. Please ensure Python is installed and added to PATH.
    pause
    exit /b
)

echo.
echo Building ApexClick executable...
pyinstaller --noconsole --onefile --clean --workpath "%TEMP%\apexclick_build" --distpath "dist" --name "ApexClick" --icon "neon_icon.ico" --add-data "neon_icon.ico;." autoclicker.py
if %ERRORLEVEL% NEQ 0 (
    echo Error building executable.
    pause
    exit /b
)

echo.
echo Build complete!
echo You can find ApexClick.exe in the "dist" folder.
pause
