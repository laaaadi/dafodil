#!/usr/bin/env python3
"""Dafodil — Raspberry Pi 3 native app.

Listens through a microphone, transcribes speech, classifies non-speech sounds,
detects faces, and displays everything as animated white text on a black screen.

Architecture: 4 Python processes (one per core)
  Process 1: Audio → Vosk speech recognition
  Process 2: Audio → YAMNet sound classification
  Process 3: Camera → Face detection
  Process 4: Renderer (Pygame, this main process)
"""

import os
import sys
import signal
import multiprocessing as mp

# Ensure we're in the right directory for model paths
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(SCRIPT_DIR)

# Model paths
VOSK_MODEL = os.path.join(SCRIPT_DIR, "models", "vosk-model-small-en-us-0.15")
YAMNET_MODEL = os.path.join(SCRIPT_DIR, "models", "yamnet.tflite")
YAMNET_CLASSES = os.path.join(SCRIPT_DIR, "models", "yamnet_classes.txt")
FACE_PROTOTXT = os.path.join(SCRIPT_DIR, "models", "deploy.prototxt")
FACE_CAFFEMODEL = os.path.join(SCRIPT_DIR, "models", "res10_300x300_ssd_iter_140000_fp16.caffemodel")


def check_models():
    """Verify all model files exist."""
    missing = []
    checks = [
        (VOSK_MODEL, "Vosk model directory"),
        (YAMNET_MODEL, "YAMNet TFLite model"),
        (YAMNET_CLASSES, "YAMNet class names"),
        (FACE_PROTOTXT, "Face detection prototxt"),
        (FACE_CAFFEMODEL, "Face detection caffemodel"),
    ]
    for path, name in checks:
        if not os.path.exists(path):
            missing.append(f"  {name}: {path}")
    if missing:
        print("Missing model files:")
        print("\n".join(missing))
        print("\nRun setup.sh first to download all models.")
        sys.exit(1)


def main():
    check_models()

    # Set SDL video driver for framebuffer (no X server)
    if "DISPLAY" not in os.environ and "WAYLAND_DISPLAY" not in os.environ:
        if "SDL_VIDEODRIVER" not in os.environ:
            os.environ["SDL_VIDEODRIVER"] = "kmsdrm"

    # Use spawn for clean process creation on Linux
    mp.set_start_method("spawn", force=True)

    # Shared events
    control_event = mp.Event()  # True = running, False = paused
    stop_event = mp.Event()     # True = shutdown

    # Queues
    speech_queue = mp.Queue(maxsize=200)    # Audio → Renderer (words)
    yamnet_audio_queue = mp.Queue(maxsize=50)  # Audio → YAMNet (raw chunks)
    sound_queue = mp.Queue(maxsize=50)      # YAMNet → Renderer (classifications)
    face_queue = mp.Queue(maxsize=30)       # Camera → Renderer (face positions)

    # Import worker functions
    from audio_process import audio_worker
    from yamnet_process import yamnet_worker
    from camera_process import camera_worker
    from renderer import Renderer

    # Create worker processes
    p_audio = mp.Process(
        target=audio_worker,
        args=(speech_queue, yamnet_audio_queue, control_event, stop_event, VOSK_MODEL),
        name="Dafodil-Audio",
        daemon=True,
    )
    p_yamnet = mp.Process(
        target=yamnet_worker,
        args=(yamnet_audio_queue, sound_queue, control_event, stop_event,
              YAMNET_MODEL, YAMNET_CLASSES),
        name="Dafodil-YAMNet",
        daemon=True,
    )
    p_camera = mp.Process(
        target=camera_worker,
        args=(face_queue, control_event, stop_event, FACE_PROTOTXT, FACE_CAFFEMODEL),
        name="Dafodil-Camera",
        daemon=True,
    )

    # Signal handler for clean shutdown
    def signal_handler(sig, frame):
        print("\n[Main] Shutting down...")
        stop_event.set()

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Start worker processes
    print("[Main] Starting Dafodil...")
    p_audio.start()
    p_yamnet.start()
    p_camera.start()

    # Run renderer in main process (Pygame requires main thread)
    renderer = Renderer(speech_queue, sound_queue, face_queue, control_event, stop_event)
    try:
        renderer.run()
    except KeyboardInterrupt:
        pass
    finally:
        print("[Main] Shutting down workers...")
        stop_event.set()

        # Wait for workers to finish
        for p in (p_audio, p_yamnet, p_camera):
            p.join(timeout=3)
            if p.is_alive():
                p.terminate()

        print("[Main] Done.")


if __name__ == "__main__":
    main()
