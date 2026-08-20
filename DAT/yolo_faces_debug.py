# yolo_faces_debug.py
# Script TOP callbacks: debug view for the YOLO_FACES container.
# Draws every detected face from the sibling 'faces' table (yellow, the
# currently-selected one thicker) plus the square box actually being sent
# to CROP (green, read from the 'detect' Script CHOP output channels).
# Pure visualization — no inference happens here.

import numpy as np
import cv2


def onSetupParameters(scriptOp):
    return


def onPulse(par):
    return


def onCook(scriptOp):
    if not scriptOp.inputs:
        return
    inp = scriptOp.inputs[0]
    try:
        arr = inp.numpyArray(delayed=True, writable=True)
    except TypeError:
        arr = inp.numpyArray(delayed=True)
        if arr is not None:
            arr = arr.copy()
    if arr is None or arr.size == 0:
        return
    if not arr.flags.writeable:
        arr = arr.copy()

    comp = scriptOp.parent()
    H, W = arr.shape[:2]

    if not int(getattr(comp.par, 'Showboxes', 1)):
        scriptOp.copyNumpyArray(arr)
        return

    # one YELLOW box per tracked person + one GREEN square each (their
    # smoothed crop window); the thick green one is on the output right now
    labels = []
    src = comp.op('video_in')
    sw = max(1, src.width if src else W)
    sh = max(1, src.height if src else H)
    t = comp.op('faces')
    if t is not None and t.numRows > 1:
        for r in range(1, t.numRows):
            try:
                tid = t[r, 'id'].val
                cx = float(t[r, 'cx'])
                cy = float(t[r, 'cy'])
                w = float(t[r, 'w'])
                h = float(t[r, 'h'])
                cur = t[r, 'current'].val == '1'
                scx = float(t[r, 'scx'])
                scy = float(t[r, 'scy'])
                sside = float(t[r, 'sside'])
            except Exception:
                continue
            x1 = int(max(0, (cx - w * 0.5) * W))
            x2 = int(min(W - 1, (cx + w * 0.5) * W))
            # top-down box -> bottom-up pixel rows
            y1 = int(max(0, (1.0 - cy - h * 0.5) * H))
            y2 = int(min(H - 1, (1.0 - cy + h * 0.5) * H))
            cv2.rectangle(arr, (x1, y1), (x2, y2), (1.0, 0.85, 0.1, 1.0), 1)
            # remember the label position in TOP-DOWN coords — text is
            # drawn in one flipped pass at the end so it reads upright
            labels.append(('#' + tid, x1 + 3, max(12, H - y2 + 14)))
            if sside > 0:
                bw = sside / sw
                bh = sside / sh
                x1 = int(max(0, (scx - bw * 0.5) * W))
                x2 = int(min(W - 1, (scx + bw * 0.5) * W))
                y1 = int(max(0, (1.0 - scy - bh * 0.5) * H))
                y2 = int(min(H - 1, (1.0 - scy + bh * 0.5) * H))
                cv2.rectangle(arr, (x1, y1), (x2, y2), (0.2, 1.0, 0.2, 1.0),
                              2 if cur else 1)

    # the THICK green box is the actual output window (during a glide it
    # travels between faces; with Glidesecs 0 it sits on the current face)
    det = comp.op('detect')
    try:
        side = float(det['width'])
        if side > 0:
            tx = float(det['tx'])
            ty = -float(det['ty'])
            cx = (tx + side * 0.5) / sw
            cy = (ty + side * 0.5) / sh
            bw = side / sw
            bh = side / sh
            x1 = int(max(0, (cx - bw * 0.5) * W))
            x2 = int(min(W - 1, (cx + bw * 0.5) * W))
            y1 = int(max(0, (1.0 - cy - bh * 0.5) * H))
            y2 = int(min(H - 1, (1.0 - cy + bh * 0.5) * H))
            cv2.rectangle(arr, (x1, y1), (x2, y2), (0.2, 1.0, 0.2, 1.0), 4)
    except Exception:
        pass

    if labels:
        arr = cv2.flip(arr, 0)   # top-down so text reads upright
        for txt, x, y in labels:
            cv2.putText(arr, txt, (x, y), cv2.FONT_HERSHEY_SIMPLEX,
                        0.5, (1.0, 1.0, 1.0, 1.0), 1, cv2.LINE_AA)
        arr = cv2.flip(arr, 0)

    scriptOp.copyNumpyArray(arr)
