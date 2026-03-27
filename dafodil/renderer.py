"""Process 4: Renderer (Pygame, main process).

Reads all queues every frame.
Renders text with fade-out, draws camera feed when toggled.
Targets 15–20fps on RPi3.
"""

import os
import math
import time
import struct
import random
import numpy as np
import multiprocessing as mp
from multiprocessing import shared_memory

# Display constants
SCREEN_W, SCREEN_H = 800, 480
FADE_DURATION = 1.5  # seconds
TARGET_FPS = 18

# Camera frame dimensions (must match camera_process)
CAM_W, CAM_H = 320, 240
CAM_FRAME_BYTES = CAM_W * CAM_H * 3
SHM_NAME = "soundsight_cam"

# Font size from RMS: quiet → small, loud → large
FONT_SIZE_MIN = 12
FONT_SIZE_MAX = 50

# Face movement threshold (fraction of screen)
FACE_MOVE_THRESHOLD = 0.20

# Text colors
SPEECH_COLOR = (255, 255, 255)
SOUND_COLOR = (187, 187, 187)  # #bbb

# Rotation range (degrees)
ROTATION_RANGE = 14


def rms_to_font_size(rms):
    """Map RMS (0.0–1.0) to font size."""
    # Apply a curve so moderate volumes are more visible
    t = min(1.0, rms * 5.0)  # Amplify — mic RMS is usually low
    t = t ** 0.6  # Compress dynamic range
    return int(FONT_SIZE_MIN + t * (FONT_SIZE_MAX - FONT_SIZE_MIN))


class TextElement:
    """A piece of text that fades out over time."""

    __slots__ = [
        "text", "x", "y", "font_size", "rotation", "color",
        "created_at", "alpha", "is_speech", "phrase_id",
    ]

    def __init__(self, text, x, y, font_size, rotation, color, is_speech=True, phrase_id=0):
        self.text = text
        self.x = x
        self.y = y
        self.font_size = font_size
        self.rotation = rotation
        self.color = color
        self.created_at = time.time()
        self.alpha = 255
        self.is_speech = is_speech
        self.phrase_id = phrase_id

    def update(self, now):
        """Update alpha based on time elapsed. Returns False if fully faded."""
        elapsed = now - self.created_at
        if elapsed >= FADE_DURATION:
            self.alpha = 0
            return False
        self.alpha = int(255 * (1.0 - elapsed / FADE_DURATION))
        return True


class Renderer:
    """Main renderer using Pygame on framebuffer."""

    def __init__(
        self,
        speech_queue: mp.Queue,
        sound_queue: mp.Queue,
        face_queue: mp.Queue,
        control_event: mp.Event,
        stop_event: mp.Event,
    ):
        self.speech_queue = speech_queue
        self.sound_queue = sound_queue
        self.face_queue = face_queue
        self.control_event = control_event
        self.stop_event = stop_event

        self.text_elements = []
        self.show_camera = False
        self.running = False

        # Face state
        self.face_detected = False
        self.face_cx = 0.5
        self.face_cy = 0.5
        self.face_fw = 0.0
        self.face_fh = 0.0

        # Phrase tracking — anchor position for current speech phrase
        self.phrase_id = 0
        self.phrase_anchor_x = 0.5
        self.phrase_anchor_y = 0.5
        self.phrase_rotation = 0.0
        self.phrase_word_count = 0

        # Shared memory for camera frames
        self.shm = None
        self.last_frame_counter = 0

    def _init_pygame(self):
        """Initialize Pygame with framebuffer."""
        # Try kmsdrm first, then fbcon, then default
        for driver in ("kmsdrm", "fbcon", ""):
            if driver:
                os.environ["SDL_VIDEODRIVER"] = driver
            try:
                import pygame
                import pygame.freetype
                pygame.init()
                self.pygame = pygame
                break
            except Exception:
                if not driver:
                    raise
                continue

        pygame = self.pygame
        # Fullscreen
        try:
            self.screen = pygame.display.set_mode(
                (SCREEN_W, SCREEN_H), pygame.FULLSCREEN | pygame.NOFRAME
            )
        except Exception:
            self.screen = pygame.display.set_mode((SCREEN_W, SCREEN_H), pygame.NOFRAME)

        pygame.display.set_caption("SoundSight")
        pygame.mouse.set_visible(False)

        # Load font
        pygame.freetype.init()
        # Try DejaVu Sans, fall back to default
        font_paths = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
        ]
        self.font_path = None
        for fp in font_paths:
            if os.path.exists(fp):
                self.font_path = fp
                break

        self.clock = pygame.time.Clock()

    def _get_font(self, size):
        """Get a freetype font at given size."""
        if self.font_path:
            return self.pygame.freetype.Font(self.font_path, size)
        return self.pygame.freetype.SysFont("dejavusans,sans", size)

    def _connect_shm(self):
        """Try to connect to camera shared memory."""
        if self.shm is not None:
            return True
        try:
            self.shm = shared_memory.SharedMemory(name=SHM_NAME, create=False)
            return True
        except Exception:
            return False

    def _read_camera_frame(self):
        """Read latest camera frame from shared memory. Returns numpy RGB or None."""
        if not self._connect_shm():
            return None
        try:
            counter = struct.unpack_from("I", self.shm.buf, 0)[0]
            if counter == self.last_frame_counter:
                return None  # No new frame
            self.last_frame_counter = counter
            frame_bytes = bytes(self.shm.buf[4 : 4 + CAM_FRAME_BYTES])
            frame = np.frombuffer(frame_bytes, dtype=np.uint8).reshape((CAM_H, CAM_W, 3))
            return frame
        except Exception:
            return None

    def _new_phrase(self, anchor_x, anchor_y):
        """Start a new phrase at given anchor position."""
        self.phrase_id += 1
        self.phrase_anchor_x = anchor_x
        self.phrase_anchor_y = anchor_y
        self.phrase_rotation = random.uniform(-ROTATION_RANGE, ROTATION_RANGE)
        self.phrase_word_count = 0

    def _process_queues(self):
        """Read all pending messages from queues."""
        # Speech words
        while True:
            try:
                msg = self.speech_queue.get_nowait()
            except Exception:
                break

            if msg["type"] == "word":
                word = msg["word"]
                rms = msg["rms"]
                font_size = rms_to_font_size(rms)

                # Determine position
                if self.face_detected:
                    # Check if face moved far from phrase anchor
                    dx = abs(self.face_cx - self.phrase_anchor_x)
                    dy = abs(self.face_cy - self.phrase_anchor_y)
                    if dx > FACE_MOVE_THRESHOLD or dy > FACE_MOVE_THRESHOLD:
                        # Start new phrase at new face position
                        self._new_phrase(self.face_cx, self.face_cy)

                    if self.phrase_word_count == 0:
                        # First word of phrase — set anchor to face
                        self.phrase_anchor_x = self.face_cx
                        self.phrase_anchor_y = self.face_cy
                        self.phrase_rotation = random.uniform(-ROTATION_RANGE, ROTATION_RANGE)
                else:
                    if self.phrase_word_count == 0:
                        # No face — random position
                        self._new_phrase(
                            random.uniform(0.1, 0.9),
                            random.uniform(0.1, 0.9),
                        )

                # Place word relative to phrase anchor
                # Offset horizontally based on word count within phrase
                offset_x = self.phrase_word_count * (font_size * 0.7)
                px = int(self.phrase_anchor_x * SCREEN_W + offset_x - 100)
                py = int(self.phrase_anchor_y * SCREEN_H)

                # Wrap if going off screen
                if px > SCREEN_W - 50:
                    px = int(self.phrase_anchor_x * SCREEN_W - 100)
                    py += font_size + 4

                elem = TextElement(
                    word, px, py, font_size, self.phrase_rotation,
                    SPEECH_COLOR, is_speech=True, phrase_id=self.phrase_id,
                )
                self.text_elements.append(elem)
                self.phrase_word_count += 1

            elif msg["type"] == "final":
                # Phrase finished — next word starts a new phrase
                self.phrase_word_count = 0

        # Sound classifications
        while True:
            try:
                msg = self.sound_queue.get_nowait()
            except Exception:
                break

            if msg["type"] == "sound":
                class_name = msg["class"]
                font_size = random.randint(16, 28)
                x = random.randint(20, SCREEN_W - 150)
                y = random.randint(20, SCREEN_H - 40)
                rotation = random.uniform(-ROTATION_RANGE, ROTATION_RANGE)
                elem = TextElement(
                    class_name, x, y, font_size, rotation,
                    SOUND_COLOR, is_speech=False,
                )
                self.text_elements.append(elem)

        # Face updates
        while True:
            try:
                msg = self.face_queue.get_nowait()
            except Exception:
                break

            if msg["type"] == "face":
                self.face_detected = msg["detected"]
                if self.face_detected:
                    self.face_cx = msg["cx"]
                    self.face_cy = msg["cy"]
                    self.face_fw = msg["fw"]
                    self.face_fh = msg["fh"]

    def _render_text(self, elem):
        """Render a single text element with rotation and alpha."""
        pygame = self.pygame
        font = self._get_font(elem.font_size)
        color = (*elem.color, elem.alpha)

        # Render text to surface
        surf, rect = font.render(elem.text, fgcolor=color)

        if abs(elem.rotation) > 0.5:
            # Rotate
            surf = pygame.transform.rotate(surf, elem.rotation)

        self.screen.blit(surf, (elem.x, elem.y))

    def _draw_face_rect(self):
        """Draw rectangle around detected face when camera is visible."""
        if not self.face_detected or not self.show_camera:
            return
        pygame = self.pygame
        fx = int((self.face_cx - self.face_fw / 2) * SCREEN_W)
        fy = int((self.face_cy - self.face_fh / 2) * SCREEN_H)
        fw = int(self.face_fw * SCREEN_W)
        fh = int(self.face_fh * SCREEN_H)
        pygame.draw.rect(self.screen, (255, 255, 255), (fx, fy, fw, fh), 1)

    def run(self):
        """Main render loop."""
        self._init_pygame()
        pygame = self.pygame
        self.running = True
        self.control_event.set()  # Start in running state
        cam_surface = None

        print("[Renderer] Running. Space/Enter=start/stop, C=camera, Q/Esc=quit")

        while not self.stop_event.is_set():
            now = time.time()

            # Handle events
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    self.stop_event.set()
                    break
                elif event.type == pygame.KEYDOWN:
                    if event.key in (pygame.K_q, pygame.K_ESCAPE):
                        self.stop_event.set()
                        break
                    elif event.key in (pygame.K_SPACE, pygame.K_RETURN):
                        if self.running:
                            self.running = False
                            self.control_event.clear()
                            print("[Renderer] Paused.")
                        else:
                            self.running = True
                            self.control_event.set()
                            print("[Renderer] Resumed.")
                    elif event.key == pygame.K_c:
                        self.show_camera = not self.show_camera
                        print(f"[Renderer] Camera {'ON' if self.show_camera else 'OFF'}")

            if self.stop_event.is_set():
                break

            # Process incoming data
            self._process_queues()

            # Clear screen
            self.screen.fill((0, 0, 0))

            # Draw camera feed if enabled
            if self.show_camera:
                frame = self._read_camera_frame()
                if frame is not None:
                    # Convert numpy RGB to pygame surface
                    # frame is (H, W, 3) — pygame wants (W, H) with swapped axes
                    surf = pygame.surfarray.make_surface(
                        np.transpose(frame, (1, 0, 2))
                    )
                    cam_surface = pygame.transform.scale(surf, (SCREEN_W, SCREEN_H))
                if cam_surface is not None:
                    self.screen.blit(cam_surface, (0, 0))

            # Draw face rectangle
            self._draw_face_rect()

            # Update and draw text elements
            alive = []
            for elem in self.text_elements:
                if elem.update(now):
                    self._render_text(elem)
                    alive.append(elem)
            self.text_elements = alive

            pygame.display.flip()
            self.clock.tick(TARGET_FPS)

        # Cleanup
        if self.shm is not None:
            try:
                self.shm.close()
            except Exception:
                pass
        pygame.quit()
        print("[Renderer] Quit.")
