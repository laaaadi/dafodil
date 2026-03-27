#!/bin/bash
# Dafodil — Raspberry Pi 3 setup script
# Run with:  bash setup.sh

# ── Auto-fix Windows CRLF line endings ────────────────────────────────────────
if python3 -c "
import sys
d = open(sys.argv[1], 'rb').read()
sys.exit(0 if b'\r\n' in d else 1)
" "$0" 2>/dev/null; then
    echo "Fixing Windows line endings in setup.sh..."
    python3 -c "
import sys
p = sys.argv[1]
open(p, 'wb').write(open(p, 'rb').read().replace(b'\r\n', b'\n'))
" "$0"
    exec bash "$0" "$@"
fi
# ──────────────────────────────────────────────────────────────────────────────

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
MODELS_DIR="$SCRIPT_DIR/models"
VENV_DIR="/home/dafodil/venv"
PIP="$VENV_DIR/bin/pip"

echo "=== Dafodil RPi3 Setup ==="
echo "Script dir : $SCRIPT_DIR"
echo "Models dir : $MODELS_DIR"
echo "Venv dir   : $VENV_DIR"
echo ""

# ── 1. Update package lists ───────────────────────────────────────────────────
echo "[1/9] Updating package lists..."
sudo apt-get update -q || echo "  Warning: apt-get update had errors, continuing..."

# ── 2. System packages ────────────────────────────────────────────────────────
echo "[2/9] Installing system packages..."

sudo apt-get install -y --fix-missing \
    python3-dev python3-pip python3-venv python3-numpy \
    libsdl2-dev libsdl2-ttf-dev libatlas-base-dev \
    libportaudio2 portaudio19-dev \
    libopenblas-dev \
    unzip wget \
    || echo "  Warning: some system packages failed, continuing..."

# opencv via apt — guaranteed ARM64 binary, avoids 30-min source compile from pip
# The venv uses --system-site-packages so it sees this automatically
sudo apt-get install -y --fix-missing python3-opencv 2>/dev/null \
    && echo "  python3-opencv installed via apt." \
    || echo "  Note: python3-opencv not in apt (will try pip, may be slow on ARM)"

# Camera stack — Raspberry Pi OS only; gracefully skipped on plain Debian
# The venv inherits these via --system-site-packages, no pip needed
sudo apt-get install -y --fix-missing python3-picamera2 2>/dev/null \
    && echo "  python3-picamera2 installed via apt." \
    || echo "  Note: python3-picamera2 not in apt"

sudo apt-get install -y --fix-missing python3-kms++ 2>/dev/null \
    || sudo apt-get install -y --fix-missing python3-kmsxx 2>/dev/null \
    || echo "  Note: python3-kms++ not available"

sudo apt-get install -y --fix-missing libcap-dev 2>/dev/null \
    || echo "  Note: libcap-dev not available"

# ── 3. Create Python virtual environment ─────────────────────────────────────
echo "[3/9] Setting up Python virtual environment at $VENV_DIR..."

if [ ! -d "$VENV_DIR" ]; then
    python3 -m venv "$VENV_DIR" --system-site-packages
    echo "  Venv created."
else
    echo "  Venv already exists."
fi

# Upgrade pip inside venv
"$VENV_DIR/bin/pip" install --upgrade pip --quiet \
    || { echo "ERROR: venv pip upgrade failed — is python3-venv installed?"; exit 1; }

# ── 4. Python packages (installed into venv) ─────────────────────────────────
echo "[4/9] Installing Python packages into venv..."

# Core packages — required; abort if these fail
"$VENV_DIR/bin/pip" install vosk sounddevice pygame numpy \
    || { echo "ERROR: core pip packages failed. Check internet connection."; exit 1; }
echo "  Core packages installed."

# opencv — use apt version (inherited via --system-site-packages) if available,
# only fall back to pip if apt didn't have it. Avoids a 30-min ARM64 source build.
if "$VENV_DIR/bin/python3" -c "import cv2" 2>/dev/null; then
    echo "  opencv already available in venv (from apt)."
else
    echo "  Installing opencv-python-headless via pip (may take a while on ARM)..."
    "$VENV_DIR/bin/pip" install opencv-python-headless \
        || echo "  Warning: opencv install failed — face detection will be disabled"
fi

# picamera2 — use apt version (inherited via --system-site-packages)
if "$VENV_DIR/bin/python3" -c "import picamera2" 2>/dev/null; then
    echo "  picamera2 already available in venv (from apt)."
else
    echo "  Warning: picamera2 not available — camera will be disabled"
fi

# tflite: try ai-edge-litert first (Google's new package, supports Python 3.13),
# then fall back to legacy tflite-runtime, then Coral repo
echo "  Installing TFLite runtime..."
if "$VENV_DIR/bin/pip" install ai-edge-litert 2>/dev/null; then
    echo "  ai-edge-litert installed (PyPI) — TFLite OK"
elif "$VENV_DIR/bin/pip" install tflite-runtime 2>/dev/null; then
    echo "  tflite-runtime installed (PyPI)"
elif "$VENV_DIR/bin/pip" install \
        --extra-index-url https://google-coral.github.io/py-repo/ \
        tflite-runtime 2>/dev/null; then
    echo "  tflite-runtime installed (Coral repo)"
elif sudo apt-get install -y python3-tflite-runtime 2>/dev/null; then
    echo "  tflite-runtime installed (apt)"
else
    echo "  WARNING: TFLite runtime not installed — YAMNet sound classification will be disabled"
fi

# ── 5. Vosk model ─────────────────────────────────────────────────────────────
echo "[5/9] Downloading Vosk model (~40 MB)..."
mkdir -p "$MODELS_DIR"
if [ ! -d "$MODELS_DIR/vosk-model-small-en-us-0.15" ]; then
    wget --show-progress -O /tmp/vosk-model.zip \
        https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip
    unzip -q /tmp/vosk-model.zip -d "$MODELS_DIR"
    rm /tmp/vosk-model.zip
    echo "  Vosk model downloaded."
else
    echo "  Vosk model already present, skipping."
fi

# ── 6. YAMNet TFLite model ────────────────────────────────────────────────────
echo "[6/9] Downloading YAMNet TFLite model (~3 MB)..."
if [ ! -f "$MODELS_DIR/yamnet.tflite" ]; then
    wget --show-progress -O "$MODELS_DIR/yamnet.tflite" \
        https://storage.googleapis.com/tfhub-lite-models/google/lite-model/yamnet/classification/tflite/1.tflite
    echo "  YAMNet model downloaded."
else
    echo "  YAMNet model already present, skipping."
fi

# ── 7. Face detection model ───────────────────────────────────────────────────
echo "[7/9] Downloading face detection model (~5 MB)..."
if [ ! -f "$MODELS_DIR/deploy.prototxt" ]; then
    wget --show-progress -O "$MODELS_DIR/deploy.prototxt" \
        https://raw.githubusercontent.com/opencv/opencv/master/samples/dnn/face_detector/deploy.prototxt
    echo "  deploy.prototxt downloaded."
else
    echo "  deploy.prototxt already present, skipping."
fi
if [ ! -f "$MODELS_DIR/res10_300x300_ssd_iter_140000_fp16.caffemodel" ]; then
    wget --show-progress -O "$MODELS_DIR/res10_300x300_ssd_iter_140000_fp16.caffemodel" \
        https://raw.githubusercontent.com/opencv/opencv_3rdparty/dnn_samples_face_detector_20180205_fp16/res10_300x300_ssd_iter_140000_fp16.caffemodel
    echo "  Caffe model downloaded."
else
    echo "  Caffe model already present, skipping."
fi

# ── 8. YAMNet class map ───────────────────────────────────────────────────────
echo "[8/9] Building YAMNet class list..."
if [ ! -f "$MODELS_DIR/yamnet_classes.txt" ]; then
    wget -q -O /tmp/class_map.csv \
        https://raw.githubusercontent.com/tensorflow/models/master/research/audioset/yamnet/yamnet_class_map.csv
    MODELS_DIR="$MODELS_DIR" python3 -c "
import csv, os
out = os.path.join(os.environ['MODELS_DIR'], 'yamnet_classes.txt')
with open('/tmp/class_map.csv') as f_in, open(out, 'w') as f_out:
    reader = csv.reader(f_in)
    next(reader)
    for row in reader:
        if len(row) >= 3:
            f_out.write(row[2].strip() + '\n')
count = sum(1 for _ in open(out))
print(f'  {count} classes written to yamnet_classes.txt')
"
    rm /tmp/class_map.csv
else
    echo "  yamnet_classes.txt already present, skipping."
fi

# ── 9. I2S overlay + ALSA config ─────────────────────────────────────────────
echo "[9/9] Configuring I2S mic and ALSA..."

BOOT_CONFIG="/boot/firmware/config.txt"
[ -f "$BOOT_CONFIG" ] || BOOT_CONFIG="/boot/config.txt"

if [ -f "$BOOT_CONFIG" ]; then
    if ! grep -q "dtoverlay=googlevoicehat-soundcard" "$BOOT_CONFIG"; then
        printf '\n# INMP441 I2S microphone\ndtoverlay=googlevoicehat-soundcard\n' \
            | sudo tee -a "$BOOT_CONFIG" > /dev/null
        echo "  I2S overlay added to $BOOT_CONFIG"
    else
        echo "  I2S overlay already configured."
    fi
else
    echo "  WARNING: boot config not found. Add manually: dtoverlay=googlevoicehat-soundcard"
fi

cat > "$HOME/.asoundrc" << 'ALSA'
pcm.!default {
    type asym
    capture.pcm "mic"
}
pcm.mic {
    type plug
    slave {
        pcm "hw:1,0"
        format S32_LE
        rate 16000
        channels 1
    }
}
ALSA
echo "  ~/.asoundrc written."

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
echo "=== Setup complete ==="
echo ""
echo "Next: reboot to activate the I2S mic overlay:"
echo "  sudo reboot"
echo ""
echo "After reboot — test manually:"
echo "  cd $SCRIPT_DIR && $VENV_DIR/bin/python3 main.py"
echo ""
echo "Or activate the venv first:"
echo "  source $VENV_DIR/bin/activate"
echo "  cd $SCRIPT_DIR && python3 main.py"
