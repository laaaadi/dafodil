#!/bin/bash
# Dafodil — Raspberry Pi 3 setup script
# Run with:  bash setup.sh

# ── Auto-fix Windows CRLF line endings ────────────────────────────────────────
# If this file was saved on Windows it contains \r\n; bash on Linux hates that.
# This block detects it, strips the \r characters, then re-runs the clean file.
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
PIP="python3 -m pip"

echo "=== Dafodil RPi3 Setup ==="
echo "Script dir : $SCRIPT_DIR"
echo "Models dir : $MODELS_DIR"
echo ""

# ── 1. Update package lists ───────────────────────────────────────────────────
echo "[1/8] Updating package lists..."
sudo apt-get update -q || echo "  Warning: apt-get update had errors, continuing..."

# ── 2. System packages ────────────────────────────────────────────────────────
echo "[2/8] Installing system packages..."

sudo apt-get install -y --fix-missing \
    python3-dev python3-pip python3-numpy \
    libsdl2-dev libsdl2-ttf-dev libatlas-base-dev \
    libportaudio2 portaudio19-dev \
    unzip wget \
    || echo "  Warning: some system packages failed, continuing..."

# Camera stack — Raspberry Pi OS only; may be missing on plain Debian
sudo apt-get install -y --fix-missing python3-picamera2 2>/dev/null \
    || echo "  Warning: python3-picamera2 not in apt (will try pip)"

sudo apt-get install -y --fix-missing python3-kms++ 2>/dev/null \
    || sudo apt-get install -y --fix-missing python3-kmsxx 2>/dev/null \
    || echo "  Warning: python3-kms++ not available"

sudo apt-get install -y --fix-missing libcap-dev 2>/dev/null \
    || echo "  Warning: libcap-dev not available"

# ── 3. Python packages ────────────────────────────────────────────────────────
echo "[3/8] Installing Python packages..."

# Upgrade pip
$PIP install --break-system-packages --upgrade pip --quiet

# Core packages (required — abort if these fail)
$PIP install --break-system-packages vosk sounddevice pygame numpy \
    || { echo "ERROR: core pip packages failed. Check your internet connection."; exit 1; }

# opencv — use the pip headless wheel; avoids pulling ~1 GB of Qt5/VTK via apt
echo "  Installing opencv-python-headless (pip, ~50 MB)..."
$PIP install --break-system-packages opencv-python-headless \
    || echo "  Warning: opencv install failed — face detection will be disabled"

# picamera2 via pip if apt didn't have it
python3 -c "import picamera2" 2>/dev/null \
    || $PIP install --break-system-packages picamera2 \
    || echo "  Warning: picamera2 not installed — camera will be disabled"

# tflite-runtime: try PyPI, then Coral repo, then apt
echo "  Installing tflite-runtime..."
if $PIP install --break-system-packages tflite-runtime 2>/dev/null; then
    echo "  tflite-runtime installed (PyPI)"
elif $PIP install --break-system-packages \
        --extra-index-url https://google-coral.github.io/py-repo/ \
        tflite-runtime 2>/dev/null; then
    echo "  tflite-runtime installed (Coral repo)"
elif sudo apt-get install -y python3-tflite-runtime 2>/dev/null; then
    echo "  tflite-runtime installed (apt)"
else
    echo "  WARNING: tflite-runtime not installed — YAMNet sound classification will be disabled"
fi

# ── 4. Vosk model ─────────────────────────────────────────────────────────────
echo "[4/8] Downloading Vosk model (~40 MB)..."
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

# ── 5. YAMNet TFLite model ────────────────────────────────────────────────────
echo "[5/8] Downloading YAMNet TFLite model (~3 MB)..."
if [ ! -f "$MODELS_DIR/yamnet.tflite" ]; then
    wget --show-progress -O "$MODELS_DIR/yamnet.tflite" \
        https://storage.googleapis.com/tfhub-lite-models/google/lite-model/yamnet/classification/tflite/1.tflite
    echo "  YAMNet model downloaded."
else
    echo "  YAMNet model already present, skipping."
fi

# ── 6. Face detection model ───────────────────────────────────────────────────
echo "[6/8] Downloading face detection model (~5 MB)..."
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

# ── 7. YAMNet class map ───────────────────────────────────────────────────────
# Use Python to parse the CSV — avoids bash CRLF / quoting issues
echo "[7/8] Building YAMNet class list..."
if [ ! -f "$MODELS_DIR/yamnet_classes.txt" ]; then
    wget -q -O /tmp/class_map.csv \
        https://raw.githubusercontent.com/tensorflow/models/master/research/audioset/yamnet/yamnet_class_map.csv
    MODELS_DIR="$MODELS_DIR" python3 -c "
import csv, os
out = os.path.join(os.environ['MODELS_DIR'], 'yamnet_classes.txt')
with open('/tmp/class_map.csv') as f_in, open(out, 'w') as f_out:
    reader = csv.reader(f_in)
    next(reader)  # skip header row
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

# ── 8. I2S overlay + ALSA config ─────────────────────────────────────────────
echo "[8/8] Configuring I2S mic and ALSA..."

# Find boot config (location changed in newer Raspberry Pi OS)
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
echo "After reboot — test manually (errors show in terminal):"
echo "  cd $SCRIPT_DIR && python3 main.py"
echo ""
echo "Or if the systemd service is installed, it starts automatically."
