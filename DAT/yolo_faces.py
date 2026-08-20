# yolo_faces.py
# Script CHOP callbacks: YOLOv8-face multi-face detection + "rolling" face
# selection. Outputs ONE face box per cook as channels, cycling through all
# detected faces every Rollseconds.
#
# Channel convention matches the MediaPipe face_detector chain that CROP
# already consumes (see CROP/select5 and the glsl1_pixel shader, which
# treats tx/ty as the box's TOP-LEFT corner):
#   tx         = box LEFT edge, pixels from the left
#   ty         = NEGATIVE box TOP edge, pixels (top-down, negated)
#   width      = box side in pixels — always SQUARE (width == height)
#   height     = same as width
#   text       = 0 (kept for channel-layout compatibility)
#   confidence = detector score of the current face (0 when no face)
#
# When no face is present width/height/confidence go to 0 so the existing
# "nothing" fallback logic in CROP keeps working.
#
# Detection runs on a background thread (onnxruntime-directml on GPU,
# cv2.dnn CPU fallback) — the main thread never waits for inference.
# The frame is read from the sibling 'small1' TOP (640x360 downscale).
#
# Expected custom parameters on the parent COMP (page "YOLO Faces"):
#   Source, Modelfile, Confidence, Holdframes, Rollseconds, Smoothsecs,
#   Padscale, Active

import os
import sys
import time
import threading

import numpy as np
import cv2

NMS_THRESHOLD = 0.45

# the lindevs yolov8-face ONNX has a STATIC 640 input — other sizes fail
MODEL_INPUT_SIZE = 640

# seconds before a silent worker thread is considered hung
WORKER_TIMEOUT = 5.0

# make DEP/.venv packages (onnxruntime-directml) importable even if
# StartupExt.AddDependenciesToPath() has not run this session
try:
    _dep = os.path.normpath(
        os.path.join(project.folder, 'DEP/.venv/Lib/site-packages'))
    if os.path.isdir(_dep) and _dep not in sys.path:
        sys.path.insert(0, _dep)
    # if the packages were copied in AFTER this path first entered sys.path,
    # Python's negative finder cache hides them until invalidated
    import importlib
    importlib.invalidate_caches()
except NameError:
    pass  # running outside TouchDesigner (offline tests)

_DET = None          # _Detector instance, persists across cooks
_INIT_ERROR = None   # model load error string, if any

# PER-FACE smoothed box states, normalized top-down [cx, cy, w, h].
# Each detected face keeps its own smoothed box, so switching faces can be
# a hard CUT (like switching cameras) instead of a glide across the frame.
_CURS = []
# the OUTPUT window [cx, cy, side_px] — follows the current face's square;
# Glidesecs > 0 makes it glide from face to face, 0 makes it cut
_OUT = [0.5, 0.5, 0.0]
_LAST_T = [0.0]
_LAST_ROWS = [None]  # last faces-table contents, to skip redundant writes


def _par(comp, name, default):
    p = getattr(comp.par, name, None)
    if p is None:
        return default
    try:
        return p.eval()
    except Exception:
        return default


def _resolve_model_path(raw):
    if not raw:
        return ''
    path = str(raw)
    if not os.path.isabs(path):
        path = os.path.join(project.folder, path)
    return os.path.normpath(path)


class _Detector:
    """Runs YOLOv8-face on a background thread. Only one job in flight.
    Boxes are (cx, cy, w, h, conf), normalized, top-down."""

    def __init__(self, model_path, size):
        self.model_path = model_path
        self.size = int(size)
        self.error = None
        self.boxes = []
        self._raw = None
        self._busy = False
        self._busy_since = 0.0
        self._stalls = 0
        self._hold = 0

        self._net = None
        self._ort = None
        self._ort_input = None
        self.backend = ''
        # onnxruntime first: DirectML runs on the GPU (~2 ms vs ~68 ms on
        # CPU) and, unlike cv2.dnn, its forward() does not deadlock when
        # called from a background thread inside TouchDesigner.
        try:
            import onnxruntime as ort
            self._ort = ort.InferenceSession(
                model_path,
                providers=['DmlExecutionProvider', 'CPUExecutionProvider'])
            self._ort_input = self._ort.get_inputs()[0].name
            self.backend = 'onnxruntime/' + self._ort.get_providers()[0]
        except Exception as ort_err:
            try:
                self._net = cv2.dnn.readNetFromONNX(model_path)
                cv2.setNumThreads(1)
                self.backend = 'cv2.dnn/CPU'
            except Exception as cv_err:
                raise RuntimeError(
                    'onnxruntime failed ({}) and cv2.dnn failed ({})'
                    .format(ort_err, cv_err))

    @property
    def idle(self):
        if self._busy and (time.time() - self._busy_since) > WORKER_TIMEOUT:
            self._stalls += 1
            self._busy = False
            if self._stalls >= 3:
                self.error = ('detection worker hung 3x (backend {}) - '
                              'detection disabled'.format(self.backend))
        return not self._busy and self._stalls < 3

    def submit(self, rgb_u8, dw, dh, conf):
        """rgb_u8: top-down uint8 RGB image already resized to fit self.size."""
        if not self.idle:
            return
        self._busy = True
        self._busy_since = time.time()
        t = threading.Thread(
            target=self._run, args=(rgb_u8, dw, dh, conf), daemon=True)
        t.start()

    def _run(self, rgb, dw, dh, conf):
        try:
            self._raw = self._infer(rgb, dw, dh, conf)
            self.error = None
        except Exception as e:
            self.error = 'YOLO inference failed: {}'.format(e)
            self._raw = []
        finally:
            self._busy = False

    def _forward(self, blob):
        if self._net is not None:
            self._net.setInput(blob)
            return self._net.forward()
        return self._ort.run(None, {self._ort_input: blob})[0]

    def _infer(self, rgb, dw, dh, conf):
        s = self.size
        canvas = np.full((s, s, 3), 114, np.uint8)
        px, py = (s - dw) // 2, (s - dh) // 2
        canvas[py:py + dh, px:px + dw] = rgb

        blob = cv2.dnn.blobFromImage(
            canvas, 1.0 / 255.0, (s, s), swapRB=False, crop=False)
        pred = self._forward(blob)[0]  # (4 + extras, 8400)

        scores = pred[4]
        keep = scores >= conf
        if not np.any(keep):
            return []
        boxes = pred[:4, keep].T.astype(np.float32)  # (n, 4) cx cy w h
        scores = scores[keep].astype(np.float32)

        xywh = boxes.copy()
        xywh[:, 0] -= xywh[:, 2] * 0.5
        xywh[:, 1] -= xywh[:, 3] * 0.5
        idxs = cv2.dnn.NMSBoxes(
            xywh.tolist(), scores.tolist(), conf, NMS_THRESHOLD)
        if idxs is None or len(idxs) == 0:
            return []

        result = []
        for i in np.array(idxs).flatten():
            i = int(i)
            cx, cy, w, h = boxes[i]
            result.append((
                (cx - px) / dw,   # normalized, top-down
                (cy - py) / dh,
                w / dw,
                h / dh,
                float(scores[i]),
            ))
        return result

    def collect(self, hold_frames):
        """Merge the latest thread result into self.boxes (main thread)."""
        res = self._raw
        if res is not None:
            self._raw = None
            if res:
                self.boxes = res
                self._hold = int(hold_frames)
            else:
                self._hold -= 1
                if self._hold <= 0:
                    self.boxes = []
        return self.boxes


def _write_faces_table(comp, boxes, squares, current_idx):
    """Raw detections + each face's smoothed SQUARE window (scx, scy
    normalized center; sside in source pixels) for the debug view."""
    t = comp.op('faces')
    if t is None:
        return
    rows = [['index', 'cx', 'cy', 'w', 'h', 'confidence', 'current',
             'scx', 'scy', 'sside']]
    for i, (cx, cy, w, h, cf) in enumerate(boxes):
        scx, scy, sside = squares[i] if i < len(squares) else (cx, cy, 0.0)
        rows.append([str(i), '%.4f' % cx, '%.4f' % cy, '%.4f' % w,
                     '%.4f' % h, '%.3f' % cf,
                     '1' if i == current_idx else '0',
                     '%.4f' % scx, '%.4f' % scy, '%.1f' % sside])
    if rows == _LAST_ROWS[0]:
        return
    _LAST_ROWS[0] = rows
    t.clear()
    for r in rows:
        t.appendRow(r)


def _ensure_detector(comp):
    global _DET, _INIT_ERROR
    model_path = _resolve_model_path(_par(comp, 'Modelfile', ''))
    if (_DET is None or _DET.model_path != model_path
            or _DET.size != MODEL_INPUT_SIZE):
        _DET = None
        _INIT_ERROR = None
        if not model_path or not os.path.isfile(model_path):
            _INIT_ERROR = 'Model file not found: {}'.format(model_path)
        else:
            try:
                _DET = _Detector(model_path, MODEL_INPUT_SIZE)
                print('[yolo_faces] backend:', _DET.backend)
            except Exception as e:
                _INIT_ERROR = str(e)
                print('[yolo_faces] model load failed:', e)


def _set_chans(scriptOp, tx, ty, w, h, text, conf):
    scriptOp.clear()
    scriptOp.numSamples = 1
    for name, val in (('tx', tx), ('ty', ty), ('width', w), ('height', h),
                      ('text', text), ('confidence', conf)):
        c = scriptOp.appendChan(name)
        c[0] = float(val)


def onSetupParameters(scriptOp):
    return


def onPulse(par):
    return


def onCook(scriptOp):
    comp = scriptOp.parent()
    src = comp.op('video_in')
    W = max(1, src.width if src else 1920)
    H = max(1, src.height if src else 1080)

    if not int(_par(comp, 'Active', 1)):
        _set_chans(scriptOp, 0.5 * W, -(0.5 * H), 0, 0, 0, 0)
        return

    _ensure_detector(comp)
    if _DET is None:
        scriptOp.addError(_INIT_ERROR or 'YOLO model not loaded')
        _set_chans(scriptOp, 0.5 * W, -(0.5 * H), 0, 0, 0, 0)
        return
    if _DET.error:
        scriptOp.addError(_DET.error)

    conf_thresh = float(_par(comp, 'Confidence', 0.35))

    # hand a fresh downscaled frame to the worker whenever it is idle
    small_top = comp.op('small1')
    if _DET.idle and small_top is not None:
        arr = small_top.numpyArray(delayed=True)
        if arr is not None and arr.size:
            h_, w_ = arr.shape[:2]
            scale = MODEL_INPUT_SIZE / float(max(w_, h_))
            dw = max(1, int(round(w_ * scale)))
            dh = max(1, int(round(h_ * scale)))
            small = cv2.resize(arr, (dw, dh), interpolation=cv2.INTER_AREA)
            rgb = np.clip(small[::-1, :, :3], 0.0, 1.0)
            rgb = (rgb * 255.0).astype(np.uint8)  # top-down uint8 RGB
            _DET.submit(rgb, dw, dh, conf_thresh)

    boxes = _DET.collect(int(_par(comp, 'Holdframes', 12)))
    # left-to-right order keeps face indices stable frame to frame
    boxes = sorted(boxes, key=lambda b: b[0])

    # --- rolling: which face is on the output right now
    n = len(boxes)
    roll_s = max(0.2, float(_par(comp, 'Rollseconds', 2.5)))
    current_idx = int(absTime.seconds / roll_s) % n if n else -1

    if n == 0:
        # width/height/confidence 0 -> CROP's "nothing" fallback takes over
        del _CURS[:]
        _OUT[2] = 0.0   # next face snaps in instead of gliding from nowhere
        _write_faces_table(comp, boxes, [], current_idx)
        _set_chans(scriptOp, 0.5 * W, -(0.5 * H), 0, 0, 0, 0)
        return

    # --- per-face exponential smoothing (Smoothsecs = jitter time constant).
    # Each face has its OWN smoothed box; the output CUTS between them.
    now = absTime.seconds
    dt = max(0.0, now - _LAST_T[0]) or 1.0 / 60.0
    _LAST_T[0] = now
    tau = float(_par(comp, 'Smoothsecs', 0.25))
    k = 1.0 if tau <= 0.0 else 1.0 - float(np.exp(-dt / tau))
    del _CURS[n:]
    for i, b in enumerate(boxes):
        if i >= len(_CURS):
            _CURS.append([b[0], b[1], b[2], b[3]])   # new face: snap in
        else:
            c = _CURS[i]
            for j in range(4):
                c[j] += (b[j] - c[j]) * k

    # --- each face's SQUARE window in pixels (width == height)
    pad = float(_par(comp, 'Padscale', 1.0))
    squares = []
    for c in _CURS:
        side = min(max(c[2] * W, c[3] * H) * pad, W, H)
        squares.append((c[0], c[1], side))

    _write_faces_table(comp, boxes, squares, current_idx)

    # --- output window: cut or glide between faces (Glidesecs par)
    scx, scy, side = squares[current_idx]
    g = float(_par(comp, 'Glidesecs', 0.0))
    if g <= 0.0 or _OUT[2] <= 0.0:
        _OUT[0], _OUT[1], _OUT[2] = scx, scy, side       # hard cut
    else:
        kg = 1.0 - float(np.exp(-dt / g))                # glide across
        _OUT[0] += (scx - _OUT[0]) * kg
        _OUT[1] += (scy - _OUT[1]) * kg
        _OUT[2] += (side - _OUT[2]) * kg
    ocx, ocy, oside = _OUT

    _set_chans(scriptOp,
               ocx * W - oside * 0.5,       # tx: LEFT edge in pixels
               -(ocy * H - oside * 0.5),    # ty: NEGATIVE top edge
               oside, oside,                # square
               0,
               boxes[current_idx][4])       # confidence of the current face
