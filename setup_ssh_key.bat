@echo off
:: Run this ONCE to set up passwordless SSH to the Pi.
:: After this, sync.bat will never ask for a password again.

set PI_USER=dafodil
set PI_HOST=192.168.0.101

echo === Generating SSH key (press Enter 3 times to accept defaults) ===
ssh-keygen -t ed25519 -f "%USERPROFILE%\.ssh\id_ed25519_soundsight" -C "soundsight-dev"

echo.
echo === Copying key to Pi (enter Pi password: ilikedafodil) ===
type "%USERPROFILE%\.ssh\id_ed25519_soundsight.pub" | ssh %PI_USER%@%PI_HOST% "mkdir -p ~/.ssh && cat >> ~/.ssh/authorized_keys && chmod 700 ~/.ssh && chmod 600 ~/.ssh/authorized_keys"

echo.
echo === Testing passwordless login ===
ssh -i "%USERPROFILE%\.ssh\id_ed25519_soundsight" -o BatchMode=yes %PI_USER%@%PI_HOST% "echo SUCCESS: passwordless SSH works"

echo.
echo === Done. You can now run sync.bat without a password. ===
pause
