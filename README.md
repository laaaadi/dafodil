# SoundSight

A Raspberry Pi 3 fullscreen app that listens through a microphone, transcribes speech, classifies non-speech sounds, detects faces, and displays everything as animated white text on a black screen.

Runs headless — no desktop, no X server. Uses the KMS/DRM framebuffer directly.

---

## How it works

- **Speech → text**: Vosk (offline, real-time). Words appear immediately as you speak, sized by volume.
- **Sound classification**: YAMNet (521 AudioSet classes). Non-speech sounds appear in grey.
- **Face tracking**: OpenCV DNN. Speech text is placed on detected faces. If no face is visible, text appears at a random position.
- **Camera feed toggle**: Press `C` to show/hide the live camera behind the text.

All text fades out after 1.5 seconds.

---

## Hardware

| Part | Details |
|---|---|
| Board | Raspberry Pi 3 |
| OS | Raspbian Lite (no desktop) |
| Display | 800×480 HDMI |
| Camera | Raspberry Pi Camera Rev 1.3 (CSI ribbon) |
| Microphone | INMP441 I2S MEMS mic |

### INMP441 wiring

| INMP441 pin | RPi GPIO | RPi pin |
|---|---|---|
| VDD | 3.3V | Pin 1 |
| GND | GND | Pin 6 |
| SD | GPIO 20 | Pin 38 |
| WS | GPIO 19 | Pin 35 |
| BCK | GPIO 18 | Pin 12 |
| L/R | GND | Pin 6 (mono left channel) |

---

## Installation

### 1. Flash Raspbian Lite

Use **Raspberry Pi Imager** to flash **Raspberry Pi OS Lite (64-bit)** to an SD card.

Before flashing, click the gear icon and set:
- Hostname: `dafodil`
- Username: `dafodil` / Password: `dafodil`
- Enable SSH (password authentication)
- Wi-Fi SSID and password

Boot the Pi and find its IP with `hostname -I` or check your router.

### 2. Enable the camera

```bash
ssh dafodil@192.168.x.x
sudo raspi-config
```

Go to **Interface Options → Camera → Enable**, then finish (do not reboot yet).

### 3. Copy the project to the Pi

From your Windows machine (PowerShell or Git Bash):

```bash
scp -r D:\ide\impact\prototypes\dafodil_rpi3\dafodil dafodil@192.168.x.x:/home/dafodil/
```

Password: `ilikedafodil`

### 4. Run setup

SSH into the Pi, then:

```bash
ssh dafodil@192.168.x.x

cd ~/dafodil
bash setup.sh
```

This will:
- Install all system and Python packages
- Download the Vosk speech model (~40 MB)
- Download the YAMNet TFLite model (~3 MB)
- Download OpenCV face detection model (~5 MB)
- Download the AudioSet class list (521 sound classes)
- Add the I2S mic overlay to `/boot/config.txt`
- Write `~/.asoundrc` for the INMP441

Takes about **5–10 minutes** depending on your internet speed.

### 5. Reboot

The I2S microphone overlay needs a reboot to activate:

```bash
sudo reboot
```

### 6. Test manually (optional)

After reboot, SSH back in and run manually to see errors in the terminal:

```bash
ssh dafodil@192.168.x.x
cd ~/dafodil
python3 main.py
```

---

## Auto-start on boot

### Install the systemd service

```bash
# Copy the service file
sudo cp /home/dafodil/dafodil/soundsight.service /etc/systemd/system/

# Allow the service to be restarted without a password
echo "dafodil ALL=(ALL) NOPASSWD: /bin/systemctl restart soundsight, /bin/systemctl start soundsight, /bin/systemctl stop soundsight" \
  | sudo tee /etc/sudoers.d/soundsight

# Enable and start the service
sudo systemctl daemon-reload
sudo systemctl enable soundsight
sudo systemctl start soundsight
```

### Check status

```bash
sudo systemctl status soundsight
```

### Watch live logs

```bash
journalctl -u soundsight -f
```

### Start / stop manually

```bash
sudo systemctl start soundsight
sudo systemctl stop soundsight
sudo systemctl restart soundsight
```

The service will now start automatically every time the Pi boots.

---

## Controls

| Key | Action |
|---|---|
| `Space` or `Enter` | Start / pause |
| `C` | Toggle camera feed on/off |
| `Q` or `Esc` | Quit |

---

## File structure

```
dafodil/
  main.py               — Entry point, starts all 4 processes
  audio_process.py      — Process 1: mic capture + Vosk speech recognition
  yamnet_process.py     — Process 2: YAMNet sound classification
  camera_process.py     — Process 3: picamera2 + face detection
  renderer.py           — Process 4: Pygame renderer (runs in main process)
  setup.sh              — Setup script (run once on the Pi)
  requirements.txt      — Python package versions
  soundsight.service    — systemd service file
  models/               — Downloaded by setup.sh (not in git)
```

---

## Architecture

Four Python processes, one per CPU core:

```
Process 1  Audio → Vosk
           sounddevice captures 16kHz mono from INMP441
           Sends partial and final words → Renderer queue
           Sends raw audio chunks → YAMNet queue

Process 2  Audio → YAMNet
           Accumulates ~1 second of audio (15600 samples)
           Runs TFLite inference ~once per second
           Sends class name + confidence → Renderer queue

Process 3  Camera → Face detection
           picamera2 at 320×240, OpenCV DNN every ~150ms
           Sends face bounding box (x%, y%) → Renderer queue
           Shares raw frames via shared_memory for display

Process 4  Renderer (main process)
           Reads all queues every frame
           Pygame + KMS/DRM framebuffer, 15–20 fps
           Handles text fading, camera overlay, key input
```

---

## Development — updating code on the Pi

After making changes on Windows, sync with:

```bash
scp -r D:\ide\impact\prototypes\dafodil_rpi3\dafodil dafodil@192.168.x.x:/home/dafodil/
```

Then restart the service:

```bash
ssh dafodil@192.168.x.x "sudo systemctl restart soundsight"
```

Or watch logs immediately after restarting:

```bash
ssh dafodil@192.168.x.x "sudo systemctl restart soundsight && journalctl -u soundsight -f"
```

---

## Troubleshooting

**Service keeps restarting / model files missing**
```bash
# Models are not in git — run setup.sh first
bash ~/dafodil/setup.sh
```

**No audio / mic not found**
```bash
# Check ALSA sees the mic (should show card 1 or similar)
arecord -l

# Test mic capture (5-second recording)
arecord -D hw:1,0 -f S32_LE -r 16000 -c 1 -d 5 test.wav
aplay test.wav
```

**Black screen / Pygame SDL error**
```bash
# Check the SDL video driver
journalctl -u soundsight | grep SDL

# Try fbcon fallback if kmsdrm fails
sudo SDL_VIDEODRIVER=fbcon python3 ~/dafodil/main.py
```

**Camera not found**
```bash
# Check camera is detected
libcamera-hello --list-cameras

# If nothing listed, check the ribbon cable and re-enable in raspi-config
sudo raspi-config
```

**tflite-runtime not installed (YAMNet disabled)**

YAMNet requires `tflite-runtime`. If setup.sh couldn't install it:
```bash
# Check your Python version
python3 --version

# Try installing manually for Python 3.11 on armv7l:
pip3 install --break-system-packages \
  "https://github.com/google-coral/pycoral/releases/download/v2.0.0/tflite_runtime-2.5.0.post1-cp311-cp311-linux_armv7l.whl"
```
Replace `cp311` with your version (e.g. `cp310` for Python 3.10).
