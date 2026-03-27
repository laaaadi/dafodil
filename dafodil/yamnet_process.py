"""Process 2: Audio → YAMNet TFLite sound classification.

Accumulates audio to 15600 samples (~1 second at 16kHz).
Runs YAMNet inference ~1/second.
Sends top class name + score to renderer.
"""

import os
import time
import numpy as np
import multiprocessing as mp


# Classes to ignore
IGNORE_CLASSES = {"Silence", "Static", "White noise", "Background noise"}

CONFIDENCE_THRESHOLD = 0.15
YAMNET_INPUT_SAMPLES = 15600  # ~0.975s at 16kHz


def yamnet_worker(
    audio_queue: mp.Queue,
    result_queue: mp.Queue,
    control_event: mp.Event,
    stop_event: mp.Event,
    model_path: str,
    classes_path: str,
):
    """Main loop for YAMNet classification."""
    # TFLite import — try all known package names in order:
    #   ai-edge-litert  : Google's new name (supports Python 3.13+)
    #   tflite-runtime  : legacy name (Python ≤ 3.11)
    #   tensorflow      : full TF (last resort, huge)
    tflite = None
    for _mod in ("ai_edge_litert.interpreter", "tflite_runtime.interpreter"):
        try:
            import importlib
            tflite = importlib.import_module(_mod)
            break
        except ImportError:
            pass
    if tflite is None:
        try:
            from tensorflow import lite as tflite
        except ImportError:
            pass
    if tflite is None:
        print("[YAMNet] No TFLite runtime found — sound classification disabled.")
        # Drain the queue so the audio process doesn't block
        while not stop_event.is_set():
            try:
                audio_queue.get(timeout=0.5)
            except Exception:
                pass
        return

    # Load class names
    with open(classes_path, "r") as f:
        class_names = [line.strip() for line in f.readlines()]

    # Load TFLite model
    interpreter = tflite.Interpreter(model_path=model_path)
    interpreter.allocate_tensors()
    input_details = interpreter.get_input_details()
    output_details = interpreter.get_output_details()

    audio_buffer = np.zeros(0, dtype=np.int16)

    print("[YAMNet] Ready.")

    while not stop_event.is_set():
        if not control_event.is_set():
            # Drain queue while paused
            while not audio_queue.empty():
                try:
                    audio_queue.get_nowait()
                except Exception:
                    break
            stop_event.wait(timeout=0.1)
            continue

        # Collect audio chunks from queue
        chunks_received = False
        while True:
            try:
                chunk_bytes = audio_queue.get(timeout=0.05)
                chunk = np.frombuffer(chunk_bytes, dtype=np.int16)
                audio_buffer = np.concatenate([audio_buffer, chunk])
                chunks_received = True
            except Exception:
                break

        # Run inference when we have enough samples
        if len(audio_buffer) >= YAMNET_INPUT_SAMPLES:
            # Take exactly YAMNET_INPUT_SAMPLES from the front
            segment = audio_buffer[:YAMNET_INPUT_SAMPLES]
            audio_buffer = audio_buffer[YAMNET_INPUT_SAMPLES:]

            # Normalize to float32 [-1, 1]
            waveform = segment.astype(np.float32) / 32768.0

            # Run inference
            try:
                interpreter.set_tensor(input_details[0]["index"], waveform)
                interpreter.invoke()
                scores = interpreter.get_tensor(output_details[0]["index"])

                # scores shape may be (1, 521) or (N, 521) — take mean across frames
                if scores.ndim > 1 and scores.shape[0] > 1:
                    scores = scores.mean(axis=0)
                else:
                    scores = scores.flatten()

                top_idx = int(np.argmax(scores))
                top_score = float(scores[top_idx])
                top_class = class_names[top_idx] if top_idx < len(class_names) else f"Class {top_idx}"

                if (
                    top_score >= CONFIDENCE_THRESHOLD
                    and top_class not in IGNORE_CLASSES
                ):
                    result_queue.put({
                        "type": "sound",
                        "class": top_class,
                        "score": top_score,
                        "time": time.time(),
                    })
            except Exception as e:
                print(f"[YAMNet] Inference error: {e}")

        elif not chunks_received:
            stop_event.wait(timeout=0.05)

    print("[YAMNet] Stopped.")
