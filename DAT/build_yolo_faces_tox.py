# build_yolo_faces_tox.py
# Builds the "YOLO_FACES" multi-face rolling-box prototype and saves it
# to TOX/yolo_faces.tox.
#
# Run inside the TouchDesigner Textport (or the web UI python console):
#   exec(open(project.folder + '/DAT/build_yolo_faces_tox.py', encoding='utf-8').read())
#
# The container does NOT crop — it outputs the box of one face at a time
# (cycling through everyone every Rollseconds) as CHOP channels in the same
# convention CROP already consumes from the MediaPipe face_detector:
#   tx, ty (negated pixels), width == height (square), text, confidence
#
# Point a Select CHOP at /project1/YOLO_FACES/out1 (like CROP/select5).
#
# Signal flow:
#   video_in (select, <- Source par) -> small1 (640x360)
#   clock (constant, absTime.frame) -> detect (Script CHOP) -> out1 (CHOP)
#        detect reads small1's pixels, runs YOLO on a background thread
#   small1 -> debugview (Script TOP, draws boxes) -> out2 (TOP)

import os

DEST_PATH = '/project1'
NAME = 'YOLO_FACES'

dest = op(DEST_PATH)
if dest is None:
    raise RuntimeError('Destination {} not found'.format(DEST_PATH))

old = dest.op(NAME)
if old is not None:
    print('[build_yolo_faces] destroying existing', old.path)
    old.destroy()

c = dest.create(baseCOMP, NAME)

# --- custom parameters -------------------------------------------------------
page = c.appendCustomPage('YOLO Faces')


def _num(pg, val, lo=None, hi=None, clamp=False):
    p = pg[0]
    if lo is not None:
        p.normMin = lo
        if clamp:
            p.min = lo
            p.clampMin = True
    if hi is not None:
        p.normMax = hi
        if clamp:
            p.max = hi
            p.clampMax = True
    p.default = val
    p.val = val
    return p


page.appendHeader('Hinput', label='INPUT')

# 'flip1' = the internal Video Device In chain (independent of MediaPipe);
# set a TOP path here to use an external source instead
p = page.appendStr('Source', label='Video Source (TOP path)')[0]
p.default = 'flip1'
p.val = 'flip1'

p = page.appendFile('Modelfile', label='Model File (.onnx)')[0]
p.default = 'DEP/models/yolov8n-face-lindevs.onnx'
p.val = 'DEP/models/yolov8n-face-lindevs.onnx'

p = page.appendToggle('Flipx', label='Flip Horizontal (mirror)')[0]
p.default = True
p.val = True

page.appendHeader('Hdetect', label='DETECTION')

p = page.appendToggle('Active', label='Active')[0]
p.default = True
p.val = True

_num(page.appendFloat('Confidence', label='Confidence'), 0.35, 0.0, 1.0, True)
_num(page.appendInt('Holdframes', label='Hold Frames'), 12, 0, 60)
_num(page.appendFloat('Smoothsecs', label='Jitter Smoothing (s)'),
     0.25, 0.0, 2.0)

p = page.appendMenu('Sweepmode', label='Rotation Sweep')[0]
p.menuNames = ['off', 'tilt', 'wide', 'full']
p.menuLabels = ['Off (upright only)', 'Tilt (±45°)',
                'Wide (±90°)', 'Full (all angles)']
# off by default: rotated passes mistake static rectangles (door frames,
# pictures) for faces — enable at the venue only if visitors will
# actually be tilted/lying down, and re-tune Confidence there
p.default = 'off'
p.val = 'off'

page.appendHeader('Hbox', label='OUTPUT BOX')

_num(page.appendFloat('Rollseconds', label='Seconds Per Face'), 2.5, 0.3, 10.0)
_num(page.appendFloat('Glidesecs', label='Glide Between Faces (s, 0 = cut)'),
     0.0, 0.0, 2.0)
_num(page.appendFloat('Padscale', label='Box Padding'), 1.0, 0.5, 4.0)

page.appendHeader('Hdebug', label='DEBUG')

p = page.appendToggle('Showboxes', label='Show Boxes (debug out2)')[0]
p.default = True
p.val = True

# --- nodes -------------------------------------------------------------------
# own capture chain: SDI card straight into the container, no MediaPipe
videodevin1 = c.create(videodeviceinTOP, 'videodevin1')

flip1 = c.create(flipTOP, 'flip1')
flip1.par.flipx.expr = 'parent().par.Flipx'
flip1.inputConnectors[0].connect(videodevin1)

video_in = c.create(selectTOP, 'video_in')
video_in.par.top.expr = 'parent().par.Source'

# stable video output for downstream consumers (CROP's picture input)
out_video = c.create(outTOP, 'out_video')
out_video.inputConnectors[0].connect(flip1)

small1 = c.create(resolutionTOP, 'small1')
small1.par.outputresolution = 'custom'
small1.par.resolutionw = 640
small1.par.resolutionh = 360
small1.inputConnectors[0].connect(video_in)

# changes every frame so the Script CHOP re-cooks whenever CROP pulls it
clock = c.create(constantCHOP, 'clock')
clock.par.value0.expr = 'absTime.frame'
try:
    clock.par.name0 = 'clk'
except Exception:
    pass

det_cb = c.create(textDAT, 'detect_callbacks')
det_cb.par.file = 'DAT/yolo_faces.py'
det_cb.par.syncfile = True
with open(os.path.join(project.folder, 'DAT', 'yolo_faces.py'),
          encoding='utf-8') as f:
    det_cb.text = f.read()

detect = c.create(scriptCHOP, 'detect')
detect.par.callbacks = 'detect_callbacks'
detect.inputConnectors[0].connect(clock)

faces = c.create(tableDAT, 'faces')
faces.clear()
faces.appendRow(['index', 'cx', 'cy', 'w', 'h', 'confidence', 'current'])

out1 = c.create(outCHOP, 'out1')
out1.inputConnectors[0].connect(detect)

dbg_cb = c.create(textDAT, 'debug_callbacks')
dbg_cb.par.file = 'DAT/yolo_faces_debug.py'
dbg_cb.par.syncfile = True
with open(os.path.join(project.folder, 'DAT', 'yolo_faces_debug.py'),
          encoding='utf-8') as f:
    dbg_cb.text = f.read()

debugview = c.create(scriptTOP, 'debugview')
debugview.par.callbacks = 'debug_callbacks'
debugview.inputConnectors[0].connect(small1)

out2 = c.create(outTOP, 'out2')
out2.inputConnectors[0].connect(debugview)

# --- layout ------------------------------------------------------------------
videodevin1.nodeX, videodevin1.nodeY = -400, 0
flip1.nodeX, flip1.nodeY = -200, 0
out_video.nodeX, out_video.nodeY = 0, 150
video_in.nodeX, video_in.nodeY = 0, 0
small1.nodeX, small1.nodeY = 200, 0
clock.nodeX, clock.nodeY = 200, -300
detect.nodeX, detect.nodeY = 450, -300
det_cb.nodeX, det_cb.nodeY = 450, -500
faces.nodeX, faces.nodeY = 700, -500
out1.nodeX, out1.nodeY = 700, -300
debugview.nodeX, debugview.nodeY = 450, 0
dbg_cb.nodeX, dbg_cb.nodeY = 450, 200
out2.nodeX, out2.nodeY = 700, 0

c.nodeX, c.nodeY = 0, -800

# --- save as external tox ----------------------------------------------------
tox_abs = os.path.join(project.folder, 'TOX', 'yolo_faces.tox')
try:
    c.par.externaltox = 'TOX/yolo_faces.tox'
except Exception as e:
    print('[build_yolo_faces] externaltox:', e)
c.save(tox_abs)

print('[build_yolo_faces] done  ->', c.path)
print('[build_yolo_faces] saved ->', tox_abs)
print('[build_yolo_faces] box channels at {}/out1 (CHOP), debug view at {}/out2'
      .format(c.path, c.path))
