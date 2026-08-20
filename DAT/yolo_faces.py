# yolo_faces.py
# Script CHOP callbacks: multi-face detection with ROTATION SWEEP and
# IoU identity tracking, driving a "rolling" face selection.
#
# Rotation sweep: the background worker cycles through a set of angles,
# rotating the (letterboxed) detection canvas before inference so tilted
# faces appear upright to the model, then maps detections back into the
# original frame. Results from all angles are merged with a light NMS.
#
# IoU tracking: detections are matched to persistent tracks by overlap,
# so each person keeps a stable id (and stable rolling order) while
# moving around — no more left-to-right index shuffling.
#
# Output channels (convention matches CROP's glsl1_pixel — tx/ty are the
# box's TOP-LEFT corner):
#   tx         = box LEFT edge, pixels from the left
#   ty         = NEGATIVE box TOP edge, pixels (top-down, negated)
#   width      = box side in pixels — always SQUARE (width == height)
#   height     = same as width
#   text       = 0 (kept for channel-layout compatibility)
#   confidence = detector score of the current person (0 when nobody)
#
# When nobody is present width/height/confidence go to 0 so the existing
# "nothing" fallback logic in CROP keeps working.
#
# Expected custom parameters on the parent COMP (page "YOLO Faces"):
#   Source, Modelfile, Flipx, Active, Confidence, Holdframes, Smoothsecs,
#   Sweepmode, Rollseconds, Glidesecs, Padscale, Showboxes

import os
import sys
import time
import threading

import numpy as np
import cv2

NMS_THRESHOLD = 0.45
MERGE_IOU = 0.35         # dedupe threshold when merging sweep angles
ROTATED_CONF_BOOST = 0.2  # rotated passes see more junk — demand more score
TRACK_MATCH_IOU = 0.1    # min overlap to keep a detection on its track
TRACK_MISS_LIMIT = 45    # cooks without a match before a track is dropped
TRACK_MIN_AGE = 4        # matches needed before a track may appear/roll
TRACK_MISS_HIDE = 8      # missed cooks after which a track is hidden

# the lindevs yolov8-face ONNX has a STATIC 640 input — other sizes fail
MODEL_INPUT_SIZE = 640

# seconds before a silent worker thread is considered hung
WORKER_TIMEOUT = 5.0

# angles (degrees) per sweep mode; the worker cycles through the list
SWEEP_ANGLES = {
    'off':  [0],
    'tilt': [0, 45, -45],
    'wide': [0, 45, -45, 90, -90],
    'full': [0, 45, -45, 90, -90, 135, -135, 180],
}

# make DEP/.venv packages (onnxruntime-directml) importable even if
# StartupExt.AddDependenciesToPath() has not run this session
try:
    _dep = os.path.normpath(
        os.path.join(project.folder, 'DEP/.venv/Lib/site-packages'))
    if os.path.isdir(_dep) and _dep not in sys.path:
        sys.path.insert(0, _dep)
    import importlib
    importlib.invalidate_caches()
except NameError:
    pass  # running outside TouchDesigner (offline tests)

_DET = None          # _Detector instance, persists across cooks
_INIT_ERROR = None   # model load error string, if any

# persistent tracks: {'id', 'cur' [cx,cy,w,h], 'conf', 'missed'}
# ids are the SMALLEST free number, so a lone person is always #1 and the
# count never climbs just because someone briefly dropped out
_TRACKS = []

# the OUTPUT window [cx, cy, side_px] — follows the current track's square;
# Glidesecs > 0 makes it glide between people, 0 makes it a hard cut
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


def _iou(a, b):
    """IoU of two (cx, cy, w, h, ...) boxes in normalized coords."""
    ax1, ax2 = a[0] - a[2] * 0.5, a[0] + a[2] * 0.5
    ay1, ay2 = a[1] - a[3] * 0.5, a[1] + a[3] * 0.5
    bx1, bx2 = b[0] - b[2] * 0.5, b[0] + b[2] * 0.5
    by1, by2 = b[1] - b[3] * 0.5, b[1] + b[3] * 0.5
    iw = min(ax2, bx2) - max(ax1, bx1)
    ih = min(ay2, by2) - max(ay1, by1)
    if iw <= 0 or ih <= 0:
        return 0.0
    inter = iw * ih
    union = a[2] * a[3] + b[2] * b[3] - inter
    return inter / union if union > 0 else 0.0


class _Detector:
    """Runs YOLOv8-face on a background thread, one job in flight,
    cycling through sweep angles. Boxes are (cx, cy, w, h, conf),
    normalized, top-down, already mapped back to the unrotated frame."""

    def __init__(self, model_path, size):
        self.model_path = model_path
        self.size = int(size)
        self.error = None
        self._results = {}   # angle -> {'boxes': [...], 'hold': n}
        self._raw = None     # latest thread result (angle, boxes)
        self._angle_i = 0
        self._busy = False
        self._busy_since = 0.0
        self._stalls = 0

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

    def submit(self, rgb_u8, dw, dh, conf, angles):
        """rgb_u8: top-down uint8 RGB already resized to fit self.size.
        angles: current sweep list; the worker picks the next one."""
        if not self.idle:
            return
        angle = angles[self._angle_i % len(angles)]
        self._angle_i += 1
        self._busy = True
        self._busy_since = time.time()
        t = threading.Thread(
            target=self._run, args=(rgb_u8, dw, dh, conf, angle), daemon=True)
        t.start()

    def _run(self, rgb, dw, dh, conf, angle):
        try:
            self._raw = (angle, self._infer(rgb, dw, dh, conf, angle))
            self.error = None
        except Exception as e:
            self.error = 'YOLO inference failed: {}'.format(e)
            self._raw = (angle, [])
        finally:
            self._busy = False

    def _forward(self, blob):
        if self._net is not None:
            self._net.setInput(blob)
            return self._net.forward()
        return self._ort.run(None, {self._ort_input: blob})[0]

    def _infer(self, rgb, dw, dh, conf, angle):
        if angle:
            # rotated views produce more false positives — ask for more
            conf = min(0.9, conf + ROTATED_CONF_BOOST)
        s = self.size
        canvas = np.full((s, s, 3), 114, np.uint8)
        px, py = (s - dw) // 2, (s - dh) // 2
        canvas[py:py + dh, px:px + dw] = rgb

        # rotate the square canvas so faces tilted by `angle` look upright
        # (90° steps are lossless on a square; 45° steps lose the extreme
        # corners of the letterboxed band — acceptable)
        if angle:
            M = cv2.getRotationMatrix2D((s * 0.5, s * 0.5), angle, 1.0)
            canvas = cv2.warpAffine(
                canvas, M, (s, s), flags=cv2.INTER_LINEAR,
                borderMode=cv2.BORDER_CONSTANT, borderValue=(114, 114, 114))

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

        # rotate detected centers back into the unrotated canvas, then
        # un-letterbox and normalize. Sizes stay as detected (the crop is
        # square-of-max anyway, so box orientation doesn't matter).
        if angle:
            rad = np.deg2rad(angle)
            ca, sa = np.cos(rad), np.sin(rad)
        result = []
        for i in np.array(idxs).flatten():
            i = int(i)
            cx, cy, w, h = boxes[i]
            if angle:
                dx, dy = cx - s * 0.5, cy - s * 0.5
                cx = s * 0.5 + ca * dx - sa * dy
                cy = s * 0.5 + sa * dx + ca * dy
            side = max(float(w), float(h))
            result.append((
                (cx - px) / dw,     # normalized, top-down
                (cy - py) / dh,
                side / dw,
                side / dh,
                float(scores[i]),
            ))
        return result

    def collect(self, hold_frames, angles):
        """Merge the latest thread result into the per-angle store, then
        return the NMS-merged union of every angle's current boxes."""
        if self._raw is not None:
            angle, res = self._raw
            self._raw = None
            if res:
                self._results[angle] = {'boxes': res,
                                        'hold': int(hold_frames)}
            elif angle in self._results:
                self._results[angle]['hold'] -= 1
                if self._results[angle]['hold'] <= 0:
                    del self._results[angle]
        # drop angles no longer in the sweep (mode changed)
        for a in [a for a in self._results if a not in angles]:
            del self._results[a]
        merged = []
        for r in self._results.values():
            merged.extend(r['boxes'])
        merged.sort(key=lambda b: -b[4])
        out = []
        for b in merged:
            dup = False
            for k in out:
                # same face seen from two sweep angles: high overlap OR
                # centers closer than half a box (rotation mapping wobbles)
                if (_iou(b, k) >= MERGE_IOU
                        or (abs(b[0] - k[0]) < 0.5 * max(b[2], k[2])
                            and abs(b[1] - k[1]) < 0.5 * max(b[3], k[3]))):
                    dup = True
                    break
            if not dup:
                out.append(b)
        return out


# ---------------------------------------------------------------- tracking

def _update_tracks(dets, k):
    """Greedy IoU matching of detections to persistent tracks. Returns the
    live tracks in id (creation) order — the rolling order stays stable
    while people move around or briefly drop out."""
    for t in _TRACKS:
        t['matched'] = False
    pairs = []
    for ti, t in enumerate(_TRACKS):
        for di, d in enumerate(dets):
            v = _iou(t['cur'], d)
            if v >= TRACK_MATCH_IOU:
                pairs.append((v, ti, di))
    pairs.sort(key=lambda x: -x[0])
    used = set()
    for v, ti, di in pairs:
        t = _TRACKS[ti]
        if t['matched'] or di in used:
            continue
        t['matched'] = True
        used.add(di)
        d = dets[di]
        for j in range(4):
            t['cur'][j] += (d[j] - t['cur'][j]) * k
        t['conf'] = d[4]
        t['missed'] = 0
        t['age'] += 1
    for di, d in enumerate(dets):
        if di not in used:
            taken = {t['id'] for t in _TRACKS}
            nid = 1
            while nid in taken:
                nid += 1
            _TRACKS.append({'id': nid, 'cur': list(d[:4]), 'conf': d[4],
                            'missed': 0, 'age': 1, 'matched': True})
    for t in list(_TRACKS):
        if not t['matched']:
            t['missed'] += 1
            t['conf'] = 0.0
            if t['missed'] > TRACK_MISS_LIMIT:
                _TRACKS.remove(t)
    return sorted(_TRACKS, key=lambda t: t['id'])


# ---------------------------------------------------------------- helpers

def _write_faces_table(comp, tracks, squares, current_id):
    t = comp.op('faces')
    if t is None:
        return
    rows = [['id', 'cx', 'cy', 'w', 'h', 'confidence', 'current',
             'scx', 'scy', 'sside']]
    for i, tr in enumerate(tracks):
        scx, scy, sside = squares[i]
        rows.append([str(tr['id']),
                     '%.4f' % tr['cur'][0], '%.4f' % tr['cur'][1],
                     '%.4f' % tr['cur'][2], '%.4f' % tr['cur'][3],
                     '%.3f' % tr['conf'],
                     '1' if tr['id'] == current_id else '0',
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
    angles = SWEEP_ANGLES.get(str(_par(comp, 'Sweepmode', 'tilt')),
                              SWEEP_ANGLES['tilt'])

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
            _DET.submit(rgb, dw, dh, conf_thresh, angles)

    dets = _DET.collect(int(_par(comp, 'Holdframes', 12)), angles)

    # --- identity tracking (smoothing folded into the track update)
    now = absTime.seconds
    dt = max(0.0, now - _LAST_T[0]) or 1.0 / 60.0
    _LAST_T[0] = now
    tau = float(_par(comp, 'Smoothsecs', 0.25))
    k = 1.0 if tau <= 0.0 else 1.0 - float(np.exp(-dt / tau))
    all_tracks = _update_tracks(dets, k)
    # only ESTABLISHED, currently-present tracks take part in the roll:
    # one-frame false positives never show, and a person who leaves stops
    # occupying a slot within ~0.13 s (no more boxes hanging in the air)
    tracks = [t for t in all_tracks
              if t['age'] >= TRACK_MIN_AGE and t['missed'] <= TRACK_MISS_HIDE]

    n = len(tracks)
    if n == 0:
        _OUT[2] = 0.0   # next person snaps in instead of gliding from nowhere
        _write_faces_table(comp, [], [], -1)
        _set_chans(scriptOp, 0.5 * W, -(0.5 * H), 0, 0, 0, 0)
        return

    # --- rolling: which track is on the output right now
    roll_s = max(0.2, float(_par(comp, 'Rollseconds', 2.5)))
    current = tracks[int(absTime.seconds / roll_s) % n]

    # --- each track's SQUARE window in pixels (width == height)
    pad = float(_par(comp, 'Padscale', 1.0))
    squares = []
    for tr in tracks:
        c = tr['cur']
        side = min(max(c[2] * W, c[3] * H) * pad, W, H)
        squares.append((c[0], c[1], side))

    _write_faces_table(comp, tracks, squares, current['id'])

    scx, scy, side = squares[tracks.index(current)]
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
               current['conf'])
