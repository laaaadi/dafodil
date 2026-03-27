@echo off
:: Syncs dafodil/ to the Pi and restarts the service.
:: Run setup_ssh_key.bat first for passwordless operation.

set PI_USER=dafodil
set PI_HOST=192.168.0.101
set PI_PATH=/home/dafodil/dafodil
set SSH_KEY=%USERPROFILE%\.ssh\id_ed25519_soundsight
set LOCAL_PATH=%~dp0dafodil

echo [1/2] Syncing files to %PI_USER%@%PI_HOST%:%PI_PATH% ...

:: Use scp with SSH key if it exists, otherwise fall back to password prompt
if exist "%SSH_KEY%" (
    scp -i "%SSH_KEY%" -r "%LOCAL_PATH%\." %PI_USER%@%PI_HOST%:%PI_PATH%/
) else (
    scp -r "%LOCAL_PATH%\." %PI_USER%@%PI_HOST%:%PI_PATH%/
)

if %ERRORLEVEL% neq 0 (
    echo ERROR: Transfer failed. Is the Pi on and reachable?
    pause
    exit /b 1
)

echo [2/2] Restarting soundsight service on Pi ...
if exist "%SSH_KEY%" (
    ssh -i "%SSH_KEY%" %PI_USER%@%PI_HOST% "sudo systemctl restart soundsight 2>/dev/null || echo '(service not installed yet, skipping restart)'"
) else (
    ssh %PI_USER%@%PI_HOST% "sudo systemctl restart soundsight 2>/dev/null || echo '(service not installed yet, skipping restart)'"
)

echo.
echo Done. Files are on the Pi.
