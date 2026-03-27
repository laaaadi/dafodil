"""Process 1: Audio capture → Vosk speech recognition.

Captures 16kHz mono from INMP441 I2S mic via sounddevice.
Sends partial/final words with per-word peak RMS to the renderer.
Forwards raw audio chunks to the YAMNet process.
"""

import json
import math
import struct
import time
import numpy as np
import multiprocessing as mp


def rms_of_chunk(samples_int16):
    """Compute RMS of int16 samples, return 0.0–1.0 normalized."""
    if len(samples_int16) == 0:
        return 0.0
    floats = samples_int16.astype(np.float32) / 32768.0
    return float(np.sqrt(np.mean(floats ** 2)))


def audio_worker(
    speech_queue: mp.Queue,
    yamnet_queue: mp.Queue,
    control_event: mp.Event,
    stop_event: mp.Event,
    model_path: str,
):
    """Main loop for audio capture and Vosk recognition."""
    import sounddevice as sd
    from vosk import Model, KaldiRecognizer

    SAMPLE_RATE = 16000
    BLOCK_SIZE = 1024  # ~64ms at 16kHz

    model = Model(model_path)
    rec = KaldiRecognizer(model, SAMPLE_RATE)
    rec.SetWords(True)

    # Track peak RMS between word boundaries
    peak_rms = 0.0
    prev_partial_words = []

    def process_partial(result_json):
        """Handle Vosk partial result — detect new words, send with volume."""
        nonlocal peak_rms, prev_partial_words
        data = json.loads(result_json)
        partial_text = data.get("partial", "")
        if not partial_text:
            return
        words = partial_text.split()
        # Find new words compared to previous partial
        new_start = len(prev_partial_words)
        if new_start < len(words):
            # Check that existing words match (Vosk can revise)
            match = True
            for i in range(min(new_start, len(words))):
                if i < len(prev_partial_words) and prev_partial_words[i] != words[i]:
                    match = False
                    break
            if not match:
                # Words were revised — send all as new
                new_start = 0

            for i in range(new_start, len(words)):
                speech_queue.put({
                    "type": "word",
                    "word": words[i],
                    "rms": peak_rms,
                    "final": False,
                    "time": time.time(),
                })
                peak_rms = 0.0  # Reset after each word

        prev_partial_words = words[:]

    def process_final(result_json):
        """Handle Vosk final result — commit the phrase."""
        nonlocal peak_rms, prev_partial_words
        data = json.loads(result_json)
        text = data.get("text", "")
        if text:
            speech_queue.put({
                "type": "final",
                "text": text,
                "time": time.time(),
            })
        prev_partial_words = []
        peak_rms = 0.0

    def audio_callback(indata, frames, time_info, status):
        """sounddevice callback — runs in a separate thread."""
        nonlocal peak_rms
        if not control_event.is_set():
            return
        # indata is float32 from sounddevice; convert to int16 for Vosk
        samples_f32 = indata[:, 0]
        samples_int16 = (samples_f32 * 32767).astype(np.int16)

        # Update peak RMS
        chunk_rms = rms_of_chunk(samples_int16)
        if chunk_rms > peak_rms:
            peak_rms = chunk_rms

        # Send raw int16 to YAMNet process (non-blocking)
        try:
            yamnet_queue.put_nowait(samples_int16.tobytes())
        except Exception:
            pass  # Drop if queue is full

        # Feed Vosk
        data_bytes = samples_int16.tobytes()
        if rec.AcceptWaveform(data_bytes):
            process_final(rec.Result())
        else:
            process_partial(rec.PartialResult())

    print("[Audio] Starting audio capture...")
    try:
        with sd.InputStream(
            samplerate=SAMPLE_RATE,
            blocksize=BLOCK_SIZE,
            channels=1,
            dtype="float32",
            callback=audio_callback,
        ):
            while not stop_event.is_set():
                stop_event.wait(timeout=0.1)
    except Exception as e:
        print(f"[Audio] Error: {e}")
    finally:
        print("[Audio] Stopped.")
