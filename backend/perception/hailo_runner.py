"""
perception/hailo_runner.py — Hailo-8L inference thread.

Architecture
────────────
This module owns the ONLY Hailo device handle.  It runs in a dedicated
OS thread (not asyncio) so blocking HEF inference never stalls the navigation
event loop.

Thread boundary:
  HailoRunner (OS thread)
      └─► detection_queue (thread-safe queue.Queue)
              └─► PerceptionManager (asyncio, main thread)

CPU / Hailo split:
  Hailo-8L  : preprocessing (letterbox + normalise) + YOLO inference + NMS
  Pi CPU    : queue put/get, confidence threshold, dataclass construction

Why a separate OS thread?
  hailo_platform APIs are synchronous and blocking.  asyncio cannot await
  blocking C extensions.  The queue bridges the two worlds without copying
  frame buffers — only lightweight detection dataclasses cross the boundary.

Mock mode
─────────
When HailoConfig.MOCK_MODE = True the runner injects synthetic detections
at a configurable interval.  This allows full end-to-end pipeline validation
on a desktop without Hailo hardware or a camera.

Real-hardware activation
────────────────────────
Set config.hailo_cfg().MOCK_MODE = False and ensure:
  - hailo_platform Python wheel is installed (pip install hailort)
  - AI HAT+ is present and driver loaded (check: hailortcli scan)
  - HEF file exists at config.hailo_cfg().hef_path
  - CSI camera accessible via /dev/video0 (libcamera or V4L2)
"""

import queue
import threading
import time
import math
from typing import Optional

from config import hailo_cfg, perception_cfg
from perception.detection import RawDetection, BBox
from utils.logger import log


# ── Constants ─────────────────────────────────────────────────────────────────

# Letterbox padding colour (black)
_PAD_VALUE = 114


class HailoRunner:
    """
    Captures camera frames, runs HEF inference on Hailo-8L, and pushes
    RawDetection objects into a thread-safe queue.

    Usage:
        runner = HailoRunner(detection_queue)
        runner.start()
        # ... main program runs ...
        runner.stop()
    """

    def __init__(self, detection_queue: queue.Queue):
        self._q        = detection_queue
        self._stop_evt = threading.Event()
        self._thread:  Optional[threading.Thread] = None
        self._hcfg  = hailo_cfg()
        self._pcfg  = perception_cfg()

    # ── Public API ─────────────────────────────────────────────────────────────

    def start(self):
        """Start the inference thread."""
        self._stop_evt.clear()
        if self._hcfg.MOCK_MODE:
            target = self._mock_loop
            log.info("HailoRunner starting in MOCK mode")
        else:
            target = self._hailo_loop
            log.info("HailoRunner starting with Hailo-8L hardware",
                     hef=self._hcfg.hef_path)

        self._thread = threading.Thread(
            target=target, daemon=True, name="hailo-inference"
        )
        self._thread.start()

    def stop(self):
        """Signal the inference thread to stop and wait for it."""
        self._stop_evt.set()
        if self._thread is not None:
            self._thread.join(timeout=3.0)
            self._thread = None
        log.info("HailoRunner stopped")

    # ── Mock loop ──────────────────────────────────────────────────────────────

    def _mock_loop(self):
        """
        Inject synthetic detections for pipeline validation without hardware.

        Sequence (repeats every MOCK_INJECT_INTERVAL_S seconds):
          t=0.0  — trash  detection, centre-left,  moderate confidence
          t=2.0  — trash  detection, centre,       high confidence
          t=4.0  — obstacle on left, large
          t=6.0  — trash  detection, centre-right, moderate
          t=8.0  — nothing (clear)
        """
        import itertools
        interval = self._hcfg.MOCK_INJECT_INTERVAL_S
        fw, fh = self._pcfg.input_width, self._pcfg.input_height
        cx, cy = fw / 2, fh / 2

        mock_sequence = itertools.cycle([
            # (class_id, confidence, x, y, w, h)
            (1, 0.72, cx - 60, cy - 40, 80,  80),   # trash, left-of-centre
            (1, 0.88, cx - 20, cy - 50, 120, 120),  # trash, near-centre, larger
            (0, 0.81, 40,      cy - 80, 200, 300),  # obstacle, left edge
            (1, 0.65, cx + 80, cy - 30, 70,  70),   # trash, right-of-centre
            None,                                    # clear frame
        ])

        for scenario in mock_sequence:
            if self._stop_evt.is_set():
                break

            if scenario is not None:
                cls, conf, x, y, w, h = scenario
                det = RawDetection(
                    class_id=cls,
                    confidence=conf,
                    bbox=BBox(x=x, y=y, w=w, h=h),
                    frame_w=fw,
                    frame_h=fh,
                )
                self._push(det)

            time.sleep(interval)

    # ── Hailo inference loop ───────────────────────────────────────────────────

    def _hailo_loop(self):
        """
        Real Hailo-8L inference loop.

        Pipeline:
          1. Open camera (V4L2 / libcamera via picamera2)
          2. Open HEF on Hailo device
          3. For each frame:
             a. Letterbox-resize to model input size
             b. Normalise to uint8 (Hailo expects uint8 BGR)
             c. Run inference (blocking, executes on Hailo ASIC — zero Pi CPU)
             d. Parse NMS output tensors → RawDetection list
             e. Push to queue (drop oldest if full)
        """
        try:
            import numpy as np
            from hailo_platform import (
                VDevice, HailoSchedulingAlgorithm,
                FormatType, HailoStreamInterface,
            )
            from hailo_platform.pyhailort.pyhailort import ConfigureParams
        except ImportError as e:
            log.error("hailo_platform not installed — falling back to mock",
                      exc=str(e))
            self._mock_loop()
            return

        # ── Camera ────────────────────────────────────────────────────────────
        cap = self._open_camera()
        if cap is None:
            log.error("Camera open failed — HailoRunner stopping")
            return

        # ── Hailo device + network ────────────────────────────────────────────
        try:
            params = VDevice.create_params()
            params.scheduling_algorithm = HailoSchedulingAlgorithm.ROUND_ROBIN

            with VDevice(params=params) as target:
                hef_path = self._pcfg.hef_path if hasattr(self._pcfg, 'hef_path') \
                           else self._hcfg.hef_path

                with open(hef_path, 'rb') as f:
                    hef_bytes = f.read()

                # Configure the network group
                cfg_params = ConfigureParams.create_from_hef(
                    hef=hef_bytes,
                    interface=HailoStreamInterface.PCIe,
                )
                network_groups = target.configure(hef_bytes, cfg_params)
                ng = network_groups[0]

                with ng.activate():
                    instream  = ng.get_input_vstreams_params()
                    outstream = ng.get_output_vstreams_params()

                    with ng.create_input_vstreams(instream) as inputs, \
                         ng.create_output_vstreams(outstream) as outputs:

                        in_stream  = inputs[0]
                        out_stream = outputs[0]

                        in_h = in_stream.info.shape[0]
                        in_w = in_stream.info.shape[1]

                        log.info("Hailo network loaded",
                                 input_shape=(in_h, in_w))

                        min_period = 1.0 / self._hcfg.MAX_FPS

                        while not self._stop_evt.is_set():
                            t0 = time.monotonic()

                            # Read frame
                            ret, frame = cap.read()
                            if not ret:
                                log.warning("Camera read failed")
                                time.sleep(0.1)
                                continue

                            # Preprocess
                            blob = self._letterbox(frame, in_w, in_h)

                            # Infer (blocking on Hailo ASIC — Pi CPU is free)
                            in_stream.write(blob.tobytes())
                            raw_out = out_stream.read()

                            # Parse output
                            dets = self._parse_output(
                                raw_out, in_w, in_h
                            )
                            for d in dets:
                                self._push(d)

                            # Rate limit
                            elapsed = time.monotonic() - t0
                            sleep   = min_period - elapsed
                            if sleep > 0:
                                time.sleep(sleep)

        except Exception as e:
            log.exception("HailoRunner fatal error", e)
        finally:
            if cap is not None:
                cap.release()

    # ── Camera helpers ────────────────────────────────────────────────────────

    @staticmethod
    def _open_camera():
        """
        Try picamera2 first (Raspberry Pi CSI), fall back to OpenCV V4L2.
        Returns an object with a .read() method returning (bool, np.ndarray).
        """
        # Try picamera2 (preferred for Pi CSI cameras)
        try:
            from picamera2 import Picamera2
            import numpy as np

            class PiCam2Wrapper:
                def __init__(self):
                    self._cam = Picamera2()
                    cfg = self._cam.create_preview_configuration(
                        main={"size": (640, 640), "format": "RGB888"}
                    )
                    self._cam.configure(cfg)
                    self._cam.start()

                def read(self):
                    import cv2
                    frame = self._cam.capture_array()
                    bgr   = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
                    return True, bgr

                def release(self):
                    self._cam.stop()

            log.info("Camera: picamera2 (CSI)")
            return PiCam2Wrapper()
        except Exception:
            pass

        # Fall back to OpenCV (USB camera or V4L2)
        try:
            import cv2
            cap = cv2.VideoCapture(0, cv2.CAP_V4L2)
            cap.set(cv2.CAP_PROP_FRAME_WIDTH,  640)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 640)
            cap.set(cv2.CAP_PROP_FPS, 30)
            if cap.isOpened():
                log.info("Camera: OpenCV V4L2 /dev/video0")
                return cap
        except Exception:
            pass

        log.error("No camera available")
        return None

    # ── Preprocessing ─────────────────────────────────────────────────────────

    @staticmethod
    def _letterbox(frame, target_w: int, target_h: int):
        """
        Letterbox-resize frame to (target_h, target_w) without distortion.
        Pads with grey (114).  Returns uint8 BGR numpy array.
        """
        import cv2
        import numpy as np

        h, w = frame.shape[:2]
        scale = min(target_w / w, target_h / h)
        nw    = int(round(w * scale))
        nh    = int(round(h * scale))

        resized = cv2.resize(frame, (nw, nh), interpolation=cv2.INTER_LINEAR)

        blob = np.full((target_h, target_w, 3), _PAD_VALUE, dtype=np.uint8)
        pad_y = (target_h - nh) // 2
        pad_x = (target_w - nw) // 2
        blob[pad_y:pad_y + nh, pad_x:pad_x + nw] = resized
        return blob

    # ── Output parsing ────────────────────────────────────────────────────────

    def _parse_output(self, raw_out, frame_w: int, frame_h: int):
        """
        Parse YOLO NMS output tensor from Hailo.

        Hailo's post-processed YOLO output format (after on-chip NMS):
          Shape: (N_detections, 6+) where columns are:
            [y_min, x_min, y_max, x_max, score, class_id, ...]
          All bbox coordinates are normalised [0, 1].

        Returns list of RawDetection.

        Note: exact tensor layout depends on the HEF compilation parameters.
        If your model uses a different output format, adjust indexing below.
        """
        import numpy as np

        pcfg = self._pcfg
        dets = []

        try:
            if isinstance(raw_out, (bytes, bytearray)):
                arr = np.frombuffer(raw_out, dtype=np.float32)
            else:
                arr = np.array(raw_out, dtype=np.float32).flatten()

            # Hailo YOLO NMS post-processor output: rows of 6+ values
            # [ymin, xmin, ymax, xmax, score, class_id]
            if arr.size < 6:
                return dets

            n_cols = 6
            n_rows = arr.size // n_cols
            arr    = arr[:n_rows * n_cols].reshape(n_rows, n_cols)

            for row in arr:
                ymin, xmin, ymax, xmax, score, cls = (
                    float(row[0]), float(row[1]),
                    float(row[2]), float(row[3]),
                    float(row[4]), int(row[5]),
                )

                # Skip low-confidence or unknown classes
                if cls == pcfg.CLASS_OBSTACLE:
                    if score < pcfg.OBSTACLE_CONFIDENCE_THRESHOLD:
                        continue
                elif cls == pcfg.CLASS_TRASH:
                    if score < pcfg.TRASH_CONFIDENCE_THRESHOLD:
                        continue
                else:
                    continue

                # Convert normalised → absolute pixels
                x = xmin * frame_w
                y = ymin * frame_h
                w = (xmax - xmin) * frame_w
                h = (ymax - ymin) * frame_h

                if w * h < pcfg.MIN_BBOX_AREA_PX2:
                    continue

                dets.append(RawDetection(
                    class_id=cls,
                    confidence=score,
                    bbox=BBox(x=x, y=y, w=w, h=h),
                    frame_w=frame_w,
                    frame_h=frame_h,
                ))
        except Exception as e:
            log.warning("Hailo output parse error", exc=str(e))

        return dets

    # ── Queue helper ──────────────────────────────────────────────────────────

    def _push(self, det: RawDetection):
        """
        Push detection to queue.  Drop the oldest entry if the queue is full
        (prefer fresh detections over stale ones).
        """
        try:
            self._q.put_nowait(det)
        except queue.Full:
            try:
                self._q.get_nowait()   # discard oldest
                self._q.put_nowait(det)
            except queue.Empty:
                pass
