#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
MODELS_DIR="$SCRIPT_DIR/models"

echo "=== SoundSight RPi3 Setup ==="

# 1. Install system packages
echo "[1/8] Installing system packages..."
sudo apt-get update
sudo apt-get install -y \
    python3-dev python3-pip python3-numpy \
    libsdl2-dev libsdl2-ttf-dev libatlas-base-dev \
    python3-libcamera python3-kms++ \
    libportaudio2 portaudio19-dev \
    unzip wget

# 2. Install Python packages
echo "[2/8] Installing Python packages..."

# picamera2 and kms++ are best installed via apt on Raspbian
sudo apt-get install -y python3-picamera2 python3-kms++ python3-prctl libcap-dev

# Core pip packages
pip3 install --break-system-packages \
    vosk sounddevice pygame numpy

# opencv-python-headless: try apt first (faster, pre-compiled for ARM)
sudo apt-get install -y python3-opencv || \
    pip3 install --break-system-packages opencv-python-headless

# tflite-runtime: try piwheels wheel for current Python version, fall back to tflite
PYVER=$(python3 -c "import sys; print(f'{sys.version_info.major}{sys.version_info.minor}')")
echo "  Python version: $PYVER"
pip3 install --break-system-packages tflite-runtime 2>/dev/null || \
pip3 install --break-system-packages \
    --index-url https://google-coral.github.io/py-repo/ \
    tflite-runtime 2>/dev/null || \
pip3 install --break-system-packages \
    "https://github.com/google-coral/pycoral/releases/download/v2.0.0/tflite_runtime-2.5.0.post1-cp${PYVER}-cp${PYVER}-linux_armv7l.whl" 2>/dev/null || \
{ echo "  WARNING: tflite-runtime not installed via pip — trying apt tensorflow-lite..."; \
  sudo apt-get install -y python3-tflite-runtime 2>/dev/null || \
  echo "  WARNING: Could not install tflite-runtime. YAMNet will be disabled."; }

# 3. Download Vosk small English model
echo "[3/8] Downloading Vosk model..."
mkdir -p "$MODELS_DIR"
if [ ! -d "$MODELS_DIR/vosk-model-small-en-us-0.15" ]; then
    wget -q --show-progress -O /tmp/vosk-model.zip \
        https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip
    unzip -q /tmp/vosk-model.zip -d "$MODELS_DIR"
    rm /tmp/vosk-model.zip
    echo "  Vosk model downloaded."
else
    echo "  Vosk model already exists, skipping."
fi

# 4. Download YAMNet TFLite int8 model
echo "[4/8] Downloading YAMNet TFLite model..."
if [ ! -f "$MODELS_DIR/yamnet.tflite" ]; then
    wget -q --show-progress -O "$MODELS_DIR/yamnet.tflite" \
        https://storage.googleapis.com/tfhub-lite-models/google/lite-model/yamnet/classification/tflite/1.tflite
    echo "  YAMNet model downloaded."
else
    echo "  YAMNet model already exists, skipping."
fi

# 5. Download OpenCV face detection model
echo "[5/8] Downloading OpenCV face detection model..."
if [ ! -f "$MODELS_DIR/deploy.prototxt" ]; then
    wget -q --show-progress -O "$MODELS_DIR/deploy.prototxt" \
        https://raw.githubusercontent.com/opencv/opencv/master/samples/dnn/face_detector/deploy.prototxt
    echo "  deploy.prototxt downloaded."
else
    echo "  deploy.prototxt already exists, skipping."
fi

if [ ! -f "$MODELS_DIR/res10_300x300_ssd_iter_140000_fp16.caffemodel" ]; then
    wget -q --show-progress -O "$MODELS_DIR/res10_300x300_ssd_iter_140000_fp16.caffemodel" \
        https://raw.githubusercontent.com/opencv/opencv_3rdparty/dnn_samples_face_detector_20180205_fp16/res10_300x300_ssd_iter_140000_fp16.caffemodel
    echo "  Caffe model downloaded."
else
    echo "  Caffe model already exists, skipping."
fi

# 6. Download and parse AudioSet class map into yamnet_classes.txt
echo "[6/8] Downloading YAMNet class map..."
if [ ! -f "$MODELS_DIR/yamnet_classes.txt" ]; then
    wget -q -O /tmp/class_map.csv \
        https://raw.githubusercontent.com/tensorflow/models/master/research/audioset/yamnet/yamnet_class_map.csv
    # Extract display_name column (third column), skip header
    tail -n +2 /tmp/class_map.csv | while IFS=, read -r _index _mid display_name; do
        # Remove surrounding quotes if present
        display_name="${display_name%\"}"
        display_name="${display_name#\"}"
        echo "$display_name"
    done > "$MODELS_DIR/yamnet_classes.txt"
    rm /tmp/class_map.csv
    echo "  yamnet_classes.txt created ($(wc -l < "$MODELS_DIR/yamnet_classes.txt") classes)."
else
    echo "  yamnet_classes.txt already exists, skipping."
fi

# 7. Add I2S overlay to /boot/config.txt
echo "[7/8] Configuring I2S overlay..."
BOOT_CONFIG="/boot/config.txt"
if [ -f "/boot/firmware/config.txt" ]; then
    BOOT_CONFIG="/boot/firmware/config.txt"
fi

if ! grep -q "dtoverlay=googlevoicehat-soundcard" "$BOOT_CONFIG" 2>/dev/null; then
    echo "" | sudo tee -a "$BOOT_CONFIG" > /dev/null
    echo "# INMP441 I2S microphone" | sudo tee -a "$BOOT_CONFIG" > /dev/null
    echo "dtoverlay=googlevoicehat-soundcard" | sudo tee -a "$BOOT_CONFIG" > /dev/null
    echo "  I2S overlay added to $BOOT_CONFIG"
else
    echo "  I2S overlay already configured."
fi

# 8. Write ALSA config
echo "[8/8] Writing ALSA config..."
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

echo ""
echo "=== Setup complete ==="
echo "Please reboot for I2S changes to take effect:"
echo "  sudo reboot"
echo ""
echo "Then run:"
echo "  cd $SCRIPT_DIR && python3 main.py"
