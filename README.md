# Dafodil

A Raspberry Pi 3 fullscreen app that listens through a microphone, transcribes speech in real time, classifies non-speech sounds, detects faces, and displays everything as animated white text on a black screen.

Runs headless — no desktop, no X server. Uses the KMS/DRM framebuffer directly.

---

## How it works

- **Speech → text**: Vosk (offline). Words appear immediately as you speak, sized by how loud you are.
- **Sound classification**: YAMNet (521 AudioSet classes). Non-speech sounds appear in grey.
- **Face tracking**: OpenCV DNN. Speech text is placed on detected faces. If no face is visible, text appears at a random position.
- **Camera feed toggle**: Press `C` to show or hide the live camera behind the text.

All text fades out after 1.5 seconds.

---

## Hardware

| Part | Details |
|---|---|
| Board | Raspberry Pi 3 |
| OS | Raspberry Pi OS Lite 64-bit (no desktop) |
| Display | 800×480 HDMI |
| Camera | Raspberry Pi Camera Rev 1.3 (CSI ribbon cable) |
| Microphone | INMP441 I2S MEMS mic |

### INMP441 wiring

| INMP441 pin | RPi GPIO | RPi physical pin |
|---|---|---|
| VDD | 3.3V | Pin 1 |
| GND | GND | Pin 6 |
| SD | GPIO 20 | Pin 38 |
| WS | GPIO 19 | Pin 35 |
| BCK | GPIO 18 | Pin 12 |
| L/R | GND | Pin 6 (sets mono left channel) |

---

## Installation

### Step 1 — Flash the SD card

Use **Raspberry Pi Imager** → choose **Raspberry Pi OS Lite (64-bit)**.

Click the **gear icon (⚙)** before writing and configure:

| Setting | Value |
|---|---|
| Hostname | `dafodil` |
| Username | `dafodil` |
| Password | `ilikedafodil` |
| SSH | Enable (password authentication) |
| Wi-Fi | your SSID and password |

Write the card, insert it in the Pi, power on.

Find the Pi's IP address from your router, or run on the Pi:
```bash
hostname -I
```

---

### Step 2 — Enable the camera

SSH into the Pi:
```bash
ssh dafodil@192.168.x.x
```

Then:
```bash
sudo raspi-config
```

Go to **Interface Options → Legacy Camera → Enable**, then **Finish** (do not reboot yet).

---

### Step 3 — Copy the project to the Pi

From your **Windows machine** (PowerShell or Git Bash):

```bash
scp -r D:\ide\impact\prototypes\dafodil_rpi3\dafodil dafodil@192.168.x.x:/home/dafodil/
```

Password: `ilikedafodil`

This copies the `dafodil` folder to `/home/dafodil/dafodil/` on the Pi.

> **If you have already copied it before**, the folder may be nested (e.g. `/home/dafodil/dafodil/dafodil/`).
> Clean it up first:
> ```bash
> ssh dafodil@192.168.x.x
> rm -rf ~/dafodil
> exit
> ```
> Then re-run the scp command above.

---

### Step 4 — Run setup

SSH in and run the setup script:

```bash
ssh dafodil@192.168.x.x

cd ~/dafodil
bash setup.sh
```

This will (takes about **5–10 minutes**):
- Install all system and Python packages
- Download the Vosk speech model (~40 MB)
- Download the YAMNet TFLite model (~3 MB)
- Download the OpenCV face detection model (~5 MB)
- Download the AudioSet class list (521 sound names)
- Add the I2S mic overlay to `/boot/firmware/config.txt`
- Write `~/.asoundrc` for the INMP441

---

### Step 5 — Reboot

The I2S microphone overlay needs a reboot to activate:

```bash
sudo reboot
```

---

### Step 6 — Test manually

After reboot, SSH back in and run directly to see any errors in the terminal:

```bash
ssh dafodil@192.168.x.x

cd ~/dafodil
/home/dafodil/venv/bin/python3 main.py
```

Or activate the venv first so you can just type `python3`:

```bash
source /home/dafodil/venv/bin/activate
cd ~/dafodil
python3 main.py
```

---

### Step 7 — Install the auto-start service

```bash
ssh dafodil@192.168.x.x

# Copy service file
sudo cp ~/dafodil/soundsight.service /etc/systemd/system/

# Allow passwordless service restart (for the sync script)
echo "dafodil ALL=(ALL) NOPASSWD: /bin/systemctl restart soundsight, /bin/systemctl start soundsight, /bin/systemctl stop soundsight" \
    | sudo tee /etc/sudoers.d/soundsight

# Enable and start
sudo systemctl daemon-reload
sudo systemctl enable soundsight
sudo systemctl start soundsight
```

The service will now start automatically on every boot.

---

## Controls

| Key | Action |
|---|---|
| `Space` or `Enter` | Start / pause |
| `C` | Toggle camera feed on/off |
| `Q` or `Esc` | Quit |

---

## Checking logs

```bash
# Live log output
journalctl -u soundsight -f

# Last 50 lines
journalctl -u soundsight -n 50

# Service status
sudo systemctl status soundsight
```

---

## Development — pushing changes to the Pi

Use `sync.bat` from the project root on Windows. It copies changed files and restarts the service in one step:

```bat
sync.bat
```

Or manually:

```bash
# From Windows — copy files
scp -r D:\ide\impact\prototypes\dafodil_rpi3\dafodil dafodil@192.168.x.x:/home/dafodil/

# Restart service
ssh dafodil@192.168.x.x "sudo systemctl restart soundsight"

# Restart and watch logs
ssh dafodil@192.168.x.x "sudo systemctl restart soundsight && journalctl -u soundsight -f"
```

---

## File structure

```
dafodil/
  main.py               — Entry point, starts all 4 processes
  audio_process.py      — Process 1: mic capture + Vosk speech recognition
  yamnet_process.py     — Process 2: YAMNet sound classification
  camera_process.py     — Process 3: picamera2 + face detection
  renderer.py           — Process 4: Pygame renderer (runs in main process)
  setup.sh              — One-time setup script (run on the Pi)
  requirements.txt      — Python package list
  soundsight.service    — systemd service file
  models/               — Downloaded by setup.sh — NOT in git
```

---

## Architecture

Four Python processes, one per CPU core:

```
Process 1  Audio → Vosk
           sounddevice captures 16 kHz mono from INMP441
           Sends partial and final words → Renderer queue
           Sends raw audio chunks → YAMNet queue

Process 2  Audio → YAMNet
           Accumulates ~1 second of audio (15 600 samples)
           Runs TFLite inference ~once per second
           Sends class name + confidence → Renderer queue

Process 3  Camera → Face detection
           picamera2 at 320×240, OpenCV DNN every ~150 ms
           Sends face bounding box (x%, y%) → Renderer queue
           Shares raw frames via shared_memory for display

Process 4  Renderer (main process)
           Reads all queues every frame
           Pygame + KMS/DRM framebuffer, 15–20 fps
           Handles text fading, camera overlay, key input
```

---

## Troubleshooting

**Service keeps restarting / model files missing**
```bash
# Models are not in git — run setup.sh first
cd ~/dafodil
bash setup.sh
```

**Files nested wrong (e.g. `/home/dafodil/dafodil/dafodil/`)**
```bash
# Remove and re-copy
rm -rf ~/dafodil
# Then re-run scp from Windows and setup.sh again
```

**No audio / mic not found**
```bash
# List ALSA capture devices (look for card 1 or similar)
arecord -l

# Test 5-second recording
arecord -D hw:1,0 -f S32_LE -r 16000 -c 1 -d 5 test.wav
aplay test.wav
```

**Black screen / Pygame SDL error**
```bash
# Check what SDL driver is being used
journalctl -u soundsight | grep SDL

# Try fbcon fallback manually
sudo SDL_VIDEODRIVER=fbcon python3 ~/dafodil/main.py
```

**Camera not found**
```bash
# List detected cameras
libcamera-hello --list-cameras

# If nothing shown: check the ribbon cable and re-enable in raspi-config
sudo raspi-config
```

**tflite-runtime not installed (YAMNet disabled)**
```bash
/home/dafodil/venv/bin/pip install tflite-runtime
```

**pip / packages not found**

All Python packages live in the venv at `/home/dafodil/venv`.
Always use the venv Python and pip:
```bash
/home/dafodil/venv/bin/python3 main.py
/home/dafodil/venv/bin/pip install <package>

# Or activate once per session:
source /home/dafodil/venv/bin/activate
python3 main.py
```

---

## Publishing to GitHub

### First time — create the repo

1. Go to [github.com/new](https://github.com/new)
2. Name it `dafodil`, set it to **Public** or **Private**
3. Do **not** add a README or .gitignore — the repo must be empty

### On the Pi — push the code

SSH into the Pi:

```bash
ssh dafodil@192.168.x.x
```

Configure git (one time only):
```bash
git config --global user.name "Your Name"
git config --global user.email "you@example.com"
```

Init and push:
```bash
cd ~/dafodil

git init
git add .
git commit -m "initial commit"
git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/dafodil.git
git push -u origin main
```

GitHub will ask for your username and a **Personal Access Token** (not your password).
To create a token: GitHub → Settings → Developer settings → Personal access tokens → Tokens (classic) → Generate new token → check `repo` → copy the token.

### Updating GitHub after changes

After syncing new code to the Pi and testing:
```bash
cd ~/dafodil
git add .
git commit -m "describe what you changed"
git push
```

### Or push directly from Windows

If you have git installed on Windows:
```powershell
cd D:\ide\impact\prototypes\dafodil_rpi3

git init
git add .
git commit -m "initial commit"
git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/dafodil.git
git push -u origin main
```

> The `models/` folder is in `.gitignore` and will not be uploaded — models are downloaded by `setup.sh`.
