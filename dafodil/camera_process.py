"""Process 3: Camera → Face detection.

Captures from Pi Camera via picamera2 at 320×240.
Runs OpenCV DNN face detection every ~150ms.
Sends face bounding box as screen percentages to renderer.
Shares camera frames via shared_memory for display.
"""

import time
import struct
import numpy as np
import multiprocessing as mp
from multiprocessing import shared_memory


# Camera capture resolution
CAM_W, CAM_H = 320, 240
FRAME_BYTES = CAM_W * CAM_H * 3  # RGB
FACE_DETECT_INTERVAL = 0.15  # seconds
FACE_CONFIDENCE_THRESHOLD = 0.5

# Shared memory layout:
#   [4 bytes: frame counter (uint32)] [FRAME_BYTES: RGB frame]
SHM_SIZE = 4 + FRAME_BYTES
SHM_NAME = "soundsight_cam"


def camera_worker(
    face_queue: mp.Queue,
    control_event: mp.Event,
    stop_event: mp.Event,
    prototxt_path: str,
    caffemodel_path: str,
):
    """Main loop for camera capture and face detection."""
    import cv2

    # Create shared memory for camera frames
    try:
        shm = shared_memory.SharedMemory(name=SHM_NAME, create=True, size=SHM_SIZE)
    except FileExistsError:
        # Clean up stale shared memory
        old_shm = shared_memory.SharedMemory(name=SHM_NAME, create=False)
        old_shm.close()
        old_shm.unlink()
        shm = shared_memory.SharedMemory(name=SHM_NAME, create=True, size=SHM_SIZE)

    # Initialize frame counter to 0
    struct.pack_into("I", shm.buf, 0, 0)

    # Load face detection model
    net = cv2.dnn.readNetFromCaffe(prototxt_path, caffemodel_path)

    # Initialize camera
    camera = None
    try:
        from picamera2 import Picamera2
        camera = Picamera2()
        config = camera.create_still_configuration(
            main={"size": (CAM_W, CAM_H), "format": "RGB888"}
        )
        camera.configure(config)
        camera.start()
        print("[Camera] Pi Camera started.")
    except Exception as e:
        print(f"[Camera] Pi Camera init failed: {e}")
        # Fallback to USB webcam via OpenCV
        try:
            camera = cv2.VideoCapture(0)
            camera.set(cv2.CAP_PROP_FRAME_WIDTH, CAM_W)
            camera.set(cv2.CAP_PROP_FRAME_HEIGHT, CAM_H)
            if not camera.isOpened():
                print("[Camera] No camera available.")
                camera = None
            else:
                print("[Camera] USB camera fallback started.")
        except Exception as e2:
            print(f"[Camera] No camera: {e2}")
            camera = None

    frame_counter = 0
    last_detect_time = 0

    print("[Camera] Ready.")

    try:
        while not stop_event.is_set():
            if not control_event.is_set():
                stop_event.wait(timeout=0.1)
                continue

            # Capture frame
            frame = None
            try:
                if hasattr(camera, "capture_array"):
                    # picamera2
                    frame = camera.capture_array()
                elif camera is not None:
                    # OpenCV VideoCapture
                    ret, bgr = camera.read()
                    if ret:
                        frame = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
                        frame = cv2.resize(frame, (CAM_W, CAM_H))
            except Exception:
                pass

            if frame is None:
                stop_event.wait(timeout=0.05)
                continue

            # Ensure frame is correct shape/dtype
            if frame.shape != (CAM_H, CAM_W, 3):
                frame = cv2.resize(frame, (CAM_W, CAM_H))
            frame = frame.astype(np.uint8)

            # Write frame to shared memory
            frame_counter += 1
            struct.pack_into("I", shm.buf, 0, frame_counter)
            shm.buf[4 : 4 + FRAME_BYTES] = frame.tobytes()

            # Face detection at interval
            now = time.time()
            if now - last_detect_time >= FACE_DETECT_INTERVAL:
                last_detect_time = now

                # Prepare blob for DNN
                blob = cv2.dnn.blobFromImage(
                    frame, 1.0, (300, 300), (104.0, 177.0, 123.0),
                    swapRB=False, crop=False,
                )
                net.setInput(blob)
                detections = net.forward()

                best_face = None
                best_conf = 0

                for i in range(detections.shape[2]):
                    confidence = float(detections[0, 0, i, 2])
                    if confidence > FACE_CONFIDENCE_THRESHOLD and confidence > best_conf:
                        box = detections[0, 0, i, 3:7]
                        # box is [x1, y1, x2, y2] as fractions of image size
                        x1, y1, x2, y2 = box
                        # Convert to center position as percentage
                        cx = float((x1 + x2) / 2.0)
                        cy = float((y1 + y2) / 2.0)
                        fw = float(x2 - x1)
                        fh = float(y2 - y1)
                        # Clamp to [0, 1]
                        cx = max(0.0, min(1.0, cx))
                        cy = max(0.0, min(1.0, cy))
                        best_face = (cx, cy, fw, fh)
                        best_conf = confidence

                face_queue.put({
                    "type": "face",
                    "detected": best_face is not None,
                    "cx": best_face[0] if best_face else 0.5,
                    "cy": best_face[1] if best_face else 0.5,
                    "fw": best_face[2] if best_face else 0.0,
                    "fh": best_face[3] if best_face else 0.0,
                    "confidence": best_conf,
                    "time": time.time(),
                })

            # Pace capture to ~30fps max
            time.sleep(0.03)

    except Exception as e:
        print(f"[Camera] Error: {e}")
    finally:
        if camera is not None:
            try:
                if hasattr(camera, "stop"):
                    camera.stop()
                elif hasattr(camera, "release"):
                    camera.release()
            except Exception:
                pass
        try:
            shm.close()
            shm.unlink()
        except Exception:
            pass
        print("[Camera] Stopped.")
