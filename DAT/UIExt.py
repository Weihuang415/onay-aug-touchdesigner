"""
Web Server DAT callbacks for the ONAY control UI (synced to webserver1's
callbacks DAT inside TOX/UI.tox).

Serves the dashboard from UI/web/ — edit those files and refresh the browser,
no TD reload needed.

Endpoints:
  GET  /                 -> UI/web/index.html (all static files served from UI/web/)
  GET  /api/status       -> JSON: cameras, monitors, windows, settings, performance
  GET  /api/cam?path=... -> JPEG snapshot of any TOP (used for live previews)
  POST /api/action       -> JSON body {action: ..., ...} control commands
"""

import contextlib
import io
import json
import os
import re
import time
import traceback

WEB_ROOT = os.path.normpath(os.path.join(project.folder, 'UI', 'web'))

# The camera views shown on the dashboard. 'name' is the COMP (or TOP) to look
# for anywhere in the project; the preview shows its output TOP, and the
# Video Device In found inside it provides device/connection status.
CAMERA_VIEWS = [
    # CAM Inside now captures directly in YOLO_FACES (videodevin1 inside it
    # provides the device dropdown) — MediaPipe is out of the video chain.
    {'label': 'CAM Inside — Face Tracking', 'name': 'YOLO_FACES'},
    {'label': 'CAM Outside — no FX', 'name': 'CAM_BEHIND'},
]

# Custom-parameter panels shown under the Cameras section.
# Styles supported by the web UI: Toggle, Menu, Pulse, Float/Int/XYZW/WH/RGBA (as number inputs).
# 'camera': render the panel inside that camera's card instead of its own section
PARAM_PANELS = [
    {'label': 'YOLO Faces', 'op': '/project1/YOLO_FACES',
     'camera': 'CAM Inside — Face Tracking',
     'pars': ['Confidence', 'Holdframes', 'Rollseconds', 'Glidesecs',
              'Padscale', 'Flipx', 'Showboxes']},
    {'label': 'Face Crop', 'op': '/project1/CROP',
     'camera': 'CAM Inside — Face Tracking',
     'pars': ['Useyolo', 'Checkrectangle', 'Index',
              'Valuex', 'Valuey', 'Valuez', 'Valuew']},
]

# What each display is actually showing — previewed in the Displays section.
# Use 'path' to point at an exact op, or 'name' to search the whole project.
# 'window': the Window COMP that puts this display on screen — its open state,
# monitor and source are shown on the card.
OUTPUT_VIEWS = [
    {'label': 'Display 1', 'path': '/project1/Monitors_layout/monitor1/output',
     'window': '/project1/window1'},
    {'label': 'Display 2', 'path': '/project1/Monitors_layout/monitor2/output',
     'window': '/project1/window1'},
]

# Window COMPs hidden from the dashboard (TD's own perform window etc.)
IGNORE_WINDOWS = {'/perform'}

# Custom toggle that switches the displays to a test pattern
TEST_PATTERN = {'op': '/project1/Monitors_layout', 'par': 'Pattern'}

# Expected show state, verified by the Health Check panel (/api/health).
HEALTH = {
    # camera label -> substring that must appear in the selected device name.
    # 'SDI' matches both the factory name ("USB Capture SDI") and renamed
    # devices — after renaming them in the Magewell USB Capture Utility
    # (e.g. "SDI Inside" / "SDI Outside"), tighten these to pin each camera
    # to its own device.
    'cameraDevice': {
        'CAM Inside — Face Tracking': 'SDI',
        'CAM Outside — no FX': 'SDI',
    },
    # current TEST setup: 2 output monitors + 1 operator screen, window1 on
    # monitor 0. Resolution check disabled while testing — re-enable for the
    # venue with the real projector specs, e.g.:
    #   'monitors': {'count': 3, 'resolutions': [[1920,1080],[1920,1080],[2560,1440]]},
    'monitors': {'count': 3},
    'windows': [
        {'path': '/project1/window1', 'monitor': 0,
         'source': '/project1/Monitors_layout', 'mustBeOpen': True},
    ],
    'fpsRatio': 0.9,          # fail when actual fps < target * ratio
    'patternShouldBeOff': True,
    'checkAssetPath': True,   # asset_path from table_settings must exist on disk
}

# seconds of identical pixels before a live camera feed counts as frozen
FREEZE_SECONDS = 5

# The MediaPipe plugin's embedded browser picks its webcam by NAME, which
# breaks with two identical capture cards. We bypass it by injecting JS into
# the page (webrender1.executeJavaScript) that opens the camera by POSITION
# in the browser's device list — that's what the CAM Inside dropdown uses.
MP_WEBRENDER = '/project1/MediaPipe/webBrowser1/webrender1'

_INSIDE_CAM_JS = """
(async () => {
  try {
    const ds = await navigator.mediaDevices.enumerateDevices();
    const cams = ds.filter(d => d.kind === 'videoinput');
    const t = cams[__IDX__];
    if (!t) { console.log('no camera at index __IDX__'); return; }
    const v = document.getElementById('webcam');
    // acquire the new device BEFORE releasing the old one — if it fails
    // (e.g. still held by CAM_BEHIND) the current picture keeps running.
    // Retry a few times: right after a swap the other consumer needs a
    // moment to actually release the card (DirectShow teardown race).
    let s = null;
    for (let i = 0; i < 5 && !s; i++) {
      try {
        s = await navigator.mediaDevices.getUserMedia({video: {
          deviceId: {exact: t.deviceId},
          width: {ideal: 1920}, height: {ideal: 1080}}});
      } catch (e) {
        console.log('camera __IDX__ attempt ' + i + ' failed: ' + e);
        await new Promise(r => setTimeout(r, 800));
      }
    }
    if (!s) { console.log('camera __IDX__: giving up'); return; }
    if (v.srcObject) v.srcObject.getTracks().forEach(tr => tr.stop());
    v.srcObject = s;
    console.log('inside camera -> index __IDX__');
  } catch (e) { console.log('inside camera switch failed: ' + e); }
})();
"""


def _inject_inside_cam(idx):
    wr = op(MP_WEBRENDER)
    if wr is None:
        return False
    wr.executeJavaScript(_INSIDE_CAM_JS.replace('__IDX__', str(int(idx))))
    return True


def ApplyStoredInsideCam():
    """Re-apply the stored index-based camera choice — only relevant while
    MediaPipe captures the camera. No-op now that YOLO_FACES has its own
    Video Device In (and whenever MediaPipe cooking is off)."""
    try:
        mp = op('/project1/MediaPipe')
        if mp is None or not mp.allowCooking:
            return
        idx = op('/project1/UI').fetch('inside_cam_idx', 'auto')
    except Exception:
        return
    if isinstance(idx, int):
        _inject_inside_cam(idx)

# the web Python console executes arbitrary code — localhost only by default
CONSOLE_ALLOW_REMOTE = False

MIME = {
    '.html': 'text/html; charset=utf-8',
    '.js': 'text/javascript; charset=utf-8',
    '.css': 'text/css; charset=utf-8',
    '.png': 'image/png',
    '.jpg': 'image/jpeg',
    '.svg': 'image/svg+xml',
    '.ico': 'image/x-icon',
}

# rolling state for actual-FPS measurement (module globals persist between requests)
_fps_state = {'frame': 0.0, 'wall': 0.0, 'fps': 0.0}

# rolling pixel signatures per TOP, for frozen-feed detection
_feed_state = {}

# preview settings — the source TOP is downscaled ON THE GPU (through
# UI/preview_select -> UI/preview_res) before the JPEG readback, so each
# request costs a few ms instead of stalling the render loop on a full-res
# readback. Web Server DAT callbacks run on TD's main thread: every ms spent
# here is a ms the timeline can't cook.
PREVIEW_MAX_W = 480
PREVIEW_TTL = 0.3     # serve the same JPEG to everyone within this window
_preview_cache = {}   # TOP path -> (perf_counter timestamp, JPEG bytes)

# rolling HTTP request log for diagnosing timeline hitches (see /api/reqlog)
_req_log = []


def _show_mode():
    """Show mode = exhibition setting: /api/cam does zero TOP cooking /
    readback so the dashboard adds no render load at all, no matter how many
    tabs are polling. Stored on the UI comp -> persists in the .toe."""
    try:
        return bool(op('/project1/UI').fetch('show_mode', False))
    except Exception:
        return False


def _ensure_preview_ops():
    """Lazy-create the shared GPU downscale chain inside the UI comp."""
    ui = op('/project1/UI')
    sel = ui.op('preview_select')
    if sel is None:
        sel = ui.create(selectTOP, 'preview_select')
        sel.nodeX, sel.nodeY = 0, -600
    res = ui.op('preview_res')
    if res is None:
        res = ui.create(resolutionTOP, 'preview_res')
        res.nodeX, res.nodeY = 200, -600
        res.par.outputresolution = 'custom'
        res.par.resolutionw = PREVIEW_MAX_W
        res.par.resolutionh = PREVIEW_MAX_W
        res.inputConnectors[0].connect(sel)
    return sel, res

# resolved CAMERA_VIEWS/OUTPUT_VIEWS ops, so root.findChildren() (a whole-
# project search) doesn't run on every status poll
_op_cache = {}        # view key -> op path

# window COMP list refreshes every 30 s instead of every poll
_win_cache = {'t': 0.0, 'paths': []}


def _feed_signature(top):
    """Cheap fingerprint of the current frame: a handful of sampled pixels.
    A live camera always has sensor noise, so identical samples over several
    seconds mean the feed is frozen (device present but not delivering)."""
    try:
        pts = [(0.1, 0.1), (0.5, 0.5), (0.9, 0.9), (0.2, 0.8), (0.8, 0.2)]
        sig = []
        for u, v in pts:
            c = top.sample(u=u, v=v)
            sig.append(tuple(round(ch, 4) for ch in c))
        return tuple(sig)
    except Exception:
        return None


def _feed_fresh(top):
    """True = frames changing, False = frozen for >= FREEZE_SECONDS,
    None = not enough samples yet."""
    now = time.time()
    sig = _feed_signature(top)
    if sig is None:
        return None
    st = _feed_state.get(top.path)
    if st is None or st['sig'] != sig:
        _feed_state[top.path] = {'sig': sig, 'changed': now}
        return True if st is not None else None
    if now - st['changed'] >= FREEZE_SECONDS:
        return False
    return None


# ---------------------------------------------------------------- helpers

def _node_name():
    try:
        n = var('NODE')
    except Exception:
        n = ''
    return n or 'WF'


def _settings_table():
    try:
        comp = op.SETTINGS
        if comp:
            t = comp.op('table_settings')
            if t:
                return t
    except Exception:
        pass
    hits = root.findChildren(name='table_settings', type=tableDAT)
    return hits[0] if hits else None


def _actual_fps():
    # state lives in comp storage, not a module global — the module reloads
    # whenever UIExt.py changes on disk (or something touches .module),
    # which would reset the counter and briefly report 0.0
    ui = op('/project1/UI')
    st = ui.fetch('fps_state', None) if ui else None
    if not isinstance(st, dict):
        st = {'frame': 0.0, 'wall': 0.0, 'fps': 0.0}
    now_wall = time.time()
    now_frame = absTime.frame
    dt = now_wall - st['wall']
    if dt > 0.2:
        df = now_frame - st['frame']
        # only average over recent windows; a huge dt (project just opened,
        # long pause) would produce a misleading number
        if st['wall'] > 0 and df >= 0 and dt < 10:
            st['fps'] = df / dt
        st['wall'] = now_wall
        st['frame'] = now_frame
        if ui:
            ui.store('fps_state', st)
    return round(st['fps'], 1)


def _resolve_view_top(o):
    """Best TOP to preview for an op: the op itself, or a COMP's output TOP."""
    if o is None:
        return None
    if o.isTOP:
        return o
    if o.isCOMP:
        outs = o.findChildren(type=outTOP, maxDepth=1)
        if outs:
            return outs[0]
        tops = o.findChildren(type=TOP, maxDepth=1)
        if tops:
            return tops[-1]
    return None


def _find_videodevins(o):
    """All Video Device In TOPs inside o (matched by type string — the python
    class name differs between TD builds)."""
    return [c for c in o.findChildren(type=TOP) if c.type == 'videodevin']


_vid_cache = {}   # comp path -> its videodevin path ('' = has none)


def _videodevin_status(comp):
    """Device/connection info from the Video Device In inside a comp (if any).
    The recursive findChildren is cached per comp — running it over a big
    container (like MediaPipe) on every status poll causes visible hitches."""
    vids = []
    if comp is not None and comp.isCOMP:
        cached = _vid_cache.get(comp.path)
        if cached is None:
            found = _find_videodevins(comp)
            _vid_cache[comp.path] = found[0].path if found else ''
            vids = found
        elif cached:
            v = op(cached)
            if v is None:
                _vid_cache.pop(comp.path, None)
            else:
                vids = [v]
    if not vids and comp is not None and comp.isTOP and comp.type == 'videodevin':
        vids = [comp]
    if not vids:
        return {'controlPath': None, 'device': '', 'active': True, 'errors': '', 'warnings': ''}
    v = vids[0]
    try:
        errs = v.errors() or ''
    except Exception:
        errs = ''
    try:
        warns = v.warnings() or ''
    except Exception:
        warns = ''
    active = True
    device = ''
    try:
        active = bool(v.par.active.eval())
        device = str(v.par.device.eval())
        # the device par value is an internal id like "V1|||\\?\usb#...|||USB Capture SDI";
        # the friendly name is the last segment
        if '|||' in device:
            device = device.split('|||')[-1]
    except Exception:
        pass
    return {'controlPath': v.path, 'device': device, 'active': active,
            'errors': errs.strip(), 'warnings': warns.strip()}


def _pretty_device_label(label):
    """Unified display name for a capture device. The browser reports
    'Name (vid:pid)' while DirectShow reports 'Name - N' for the same card —
    strip the vid:pid suffix so a card reads the same in every dropdown."""
    return re.sub(r'\s*\([0-9a-f]{4}:[0-9a-f]{4}\)$', '', label, flags=re.I)


def _device_menu(op_path, p):
    """Menu info for a camera-device parameter, for the card's dropdown."""
    names = list(getattr(p, 'menuNames', []) or [])
    labels = [_pretty_device_label(l)
              for l in (getattr(p, 'menuLabels', []) or [])]
    val = str(p.eval())
    # entries with the same value AND label are indistinguishable — picking
    # either sends the identical value, so showing both is just confusing.
    # Collapse them and tell the UI it happened (duplicates flag).
    seen = set()
    unames, ulabels = [], []
    for n, l in zip(names, labels):
        if (n, l) in seen:
            continue
        seen.add((n, l))
        unames.append(n)
        ulabels.append(l)
    duplicates = len(unames) < len(names)
    try:
        label = ulabels[unames.index(val)]
    except (ValueError, IndexError):
        label = (val.split('|||')[-1] if '|||' in val
                 else _pretty_device_label(val))
    return {'op': op_path, 'par': p.name, 'value': val, 'valueLabel': label,
            'menuNames': unames, 'menuLabels': ulabels,
            'duplicates': duplicates}


def _index_device_menu(comp):
    """Index-based picker for the MediaPipe camera (see MP_WEBRENDER note).
    Entries: 'auto' = the plugin's own name matching, '0'/'1'/... = open the
    n-th camera in the browser's device list via JS injection."""
    try:
        devs = json.loads(comp.op('webcam_list').text or '[]')
    except Exception:
        devs = []
    n = max(len(devs), 2)
    names = ['auto'] + [str(i) for i in range(n)]
    labels = ['Auto (by device name)']
    for i in range(n):
        lbl = 'Capture #{}'.format(i + 1)
        if i < len(devs) and devs[i].get('label'):
            lbl += ' — ' + _pretty_device_label(devs[i]['label'])
        labels.append(lbl)
    stored = op('/project1/UI').fetch('inside_cam_idx', 'auto')
    val = str(stored)
    try:
        vlabel = labels[names.index(val)]
    except ValueError:
        vlabel = val
    return {'op': comp.path, 'par': 'Webcam', 'value': val,
            'valueLabel': vlabel, 'menuNames': names, 'menuLabels': labels,
            'duplicates': False, 'action': 'set_inside_cam'}


def _camera_device_allowed(op_path, par_name):
    """Device-menu pars on camera views may be set from the web."""
    for view in CAMERA_VIEWS:
        comp = _find_view_op(view)
        if comp is None:
            continue
        vid = _videodevin_status(comp)
        if vid['controlPath'] and op_path == vid['controlPath'] and par_name == 'device':
            return True
        if view.get('devicePar') and op_path == comp.path and par_name == view['devicePar']:
            return True
    return False


def _cameras(check_fresh=False):
    """check_fresh runs the frozen-feed pixel sampling — that's a sync GPU
    readback, so it only happens during health checks, not status polls."""
    cams = []
    for view in CAMERA_VIEWS:
        comp = _find_view_op(view)
        top = _resolve_view_top(comp)
        if comp is None or top is None:
            missing = view.get('name') or view.get('path')
            cams.append({
                'path': '', 'name': missing, 'label': view['label'],
                'controlPath': None, 'device': '', 'active': False,
                'width': 0, 'height': 0, 'connected': False,
                'errors': f"'{missing}' not found in project", 'warnings': '',
            })
            continue
        vid = _videodevin_status(comp)
        dev_menu = None
        if vid['controlPath']:
            v = op(vid['controlPath'])
            p = getattr(v.par, 'device', None) if v else None
            if p is not None:
                dev_menu = _device_menu(v.path, p)
        elif view.get('indexSelect'):
            dev_menu = _index_device_menu(comp)
        elif view.get('devicePar'):
            p = getattr(comp.par, view['devicePar'], None)
            if p is not None:
                dev_menu = _device_menu(comp.path, p)
        if dev_menu and not vid['device']:
            vid['device'] = dev_menu['valueLabel']
        connected = vid['active'] and top.width > 1 and not vid['errors']
        fresh = (_feed_fresh(top)
                 if (check_fresh and vid['active'] and top.width > 1) else None)
        cams.append({
            'path': top.path,
            'name': comp.name,
            'label': view['label'],
            'fresh': fresh,
            'controlPath': vid['controlPath'],
            'device': vid['device'],
            'deviceMenu': dev_menu,
            'active': vid['active'],
            'width': top.width,
            'height': top.height,
            'connected': connected,
            'errors': vid['errors'],
            'warnings': vid['warnings'],
        })
    # fallback: if nothing was configured/found, list raw Video Device In TOPs
    if not any(c['path'] for c in cams):
        for c in sorted(_find_videodevins(root), key=lambda o: o.path):
            vid = _videodevin_status(c)
            cams.append({
                'path': c.path, 'name': c.name, 'label': c.name,
                'controlPath': vid['controlPath'], 'device': vid['device'],
                'active': vid['active'], 'width': c.width, 'height': c.height,
                'connected': vid['active'] and c.width > 1 and not vid['errors'],
                'errors': vid['errors'], 'warnings': vid['warnings'],
            })
    return cams


def _monitors():
    out = []
    try:
        for i in range(len(monitors)):
            m = monitors[i]
            out.append({
                'index': i,
                'description': getattr(m, 'description', ''),
                'displayName': getattr(m, 'displayName', ''),
                'width': getattr(m, 'width', 0),
                'height': getattr(m, 'height', 0),
                'left': getattr(m, 'left', 0),
                'top': getattr(m, 'top', 0),
                'refreshRate': getattr(m, 'refreshRate', 0),
                'isPrimary': bool(getattr(m, 'isPrimary', False)),
            })
    except Exception:
        pass
    return out


def _find_view_op(view):
    """Resolve a view config entry: exact 'path' first, else search by 'name'.
    Resolutions are cached — findChildren over the whole project is far too
    slow to run on every status poll."""
    key = view.get('path') or view.get('name') or ''
    cached = _op_cache.get(key)
    if cached:
        o = op(cached)
        if o is not None:
            return o
    if view.get('path'):
        o = op(view['path'])
    else:
        hits = root.findChildren(name=view['name'])
        o = hits[0] if hits else None
    if o is not None:
        _op_cache[key] = o.path
    return o


def _window_info(w):
    if w is None:
        return None
    try:
        mon = int(w.par.monitor.eval())
    except Exception:
        mon = -1
    try:
        src = w.par.winop.eval()
        src = src.path if src else ''
    except Exception:
        src = ''
    return {'path': w.path, 'name': w.name, 'isOpen': bool(getattr(w, 'isOpen', False)),
            'monitor': mon, 'source': src}


def _outputs():
    """Preview views of what each display is showing (see OUTPUT_VIEWS)."""
    out = []
    for view in OUTPUT_VIEWS:
        win = _window_info(op(view['window'])) if view.get('window') else None
        top = _resolve_view_top(_find_view_op(view))
        if top is None:
            missing = view.get('path') or view.get('name')
            out.append({'label': view['label'], 'path': '', 'width': 0, 'height': 0,
                        'window': win, 'error': f"'{missing}' not found"})
            continue
        out.append({'label': view['label'], 'path': top.path,
                    'width': top.width, 'height': top.height,
                    'window': win, 'error': ''})
    return out


def _windows():
    now = time.time()
    if now - _win_cache['t'] > 30 or not _win_cache['paths']:
        _win_cache['paths'] = sorted(
            w.path for w in root.findChildren(type=windowCOMP))
        _win_cache['t'] = now
    out = []
    for p in _win_cache['paths']:
        w = op(p)
        if w is None:            # window deleted — rescan next poll
            _win_cache['t'] = 0.0
            continue
        if w.path in IGNORE_WINDOWS:
            continue
        entry = {'path': w.path, 'name': w.name, 'isOpen': bool(getattr(w, 'isOpen', False))}
        try:
            entry['monitor'] = int(w.par.monitor.eval())
        except Exception:
            entry['monitor'] = -1
        try:
            src = w.par.winop.eval()
            entry['source'] = src.path if src else ''
        except Exception:
            entry['source'] = ''
        out.append(entry)
    return out


def _settings():
    tbl = _settings_table()
    node = _node_name()
    if not tbl or tbl.numRows < 1:
        return {'node': node, 'columns': [], 'values': {}}
    cols = [c.val for c in tbl.row(0)]
    values = {}
    if tbl[node, 0] is not None:
        for c in cols[1:]:
            cell = tbl[node, c]
            values[c] = cell.val if cell is not None else ''
    return {'node': node, 'columns': cols[1:], 'values': values}


def _par_info(p):
    val = p.eval()
    if not isinstance(val, (int, float, str, bool, type(None))):
        val = str(val)
    return {
        'name': p.name, 'label': p.label, 'style': p.style, 'value': val,
        'min': getattr(p, 'normMin', 0), 'max': getattr(p, 'normMax', 1),
        'menuNames': list(getattr(p, 'menuNames', []) or []),
        'menuLabels': list(getattr(p, 'menuLabels', []) or []),
    }


def _panels():
    out = []
    for cfg in PARAM_PANELS:
        o = op(cfg['op'])
        if not o:
            out.append({'label': cfg['label'], 'op': cfg['op'], 'camera': cfg.get('camera'),
                        'pars': [], 'error': f"{cfg['op']} not found"})
            continue
        pars = []
        for pname in cfg['pars']:
            p = getattr(o.par, pname, None)
            if p is not None:
                pars.append(_par_info(p))
        out.append({'label': cfg['label'], 'op': cfg['op'], 'camera': cfg.get('camera'),
                    'pars': pars, 'error': ''})
    return out


def _par_allowed(op_path, par_name):
    """Only pars explicitly exposed in PARAM_PANELS may be set from the web."""
    for cfg in PARAM_PANELS:
        if cfg['op'] == op_path and par_name in cfg['pars']:
            return True
    return False


def _test_pattern_par():
    try:
        o = op(TEST_PATTERN['op'])
        if o is None:
            return None
        return getattr(o.par, TEST_PATTERN['par'], None)
    except Exception:
        return None


# ---------------------------------------------------------------- web console

# persistent namespace so variables survive between console commands (op, root,
# project etc. resolve through TD's injected builtins)
_console_env = {}


def _console_exec(code):
    buf = io.StringIO()
    result = None
    err = ''
    try:
        with contextlib.redirect_stdout(buf):
            try:
                result = eval(compile(code, '<webconsole>', 'eval'), _console_env)
            except SyntaxError:
                exec(compile(code, '<webconsole>', 'exec'), _console_env)
        if result is not None:
            _console_env['_'] = result
    except Exception:
        err = traceback.format_exc()
    return {'ok': not err, 'out': buf.getvalue(),
            'result': repr(result) if result is not None else '', 'error': err}


def _client_is_local(request):
    addr = str(request.get('clientAddress', ''))
    return addr.startswith('127.') or addr in ('::1', 'localhost') or addr.startswith('::ffff:127.')


# ---------------------------------------------------------------- health check

def _chk(label, status, detail=''):
    return {'label': label, 'status': status, 'detail': detail}


def _health():
    """Compare live state against the HEALTH expected-state config."""
    checks = []

    # --- cameras
    exp_dev = HEALTH.get('cameraDevice', {})
    for c in _cameras(check_fresh=True):
        lbl = c['label']
        exp = exp_dev.get(lbl)
        if exp:
            if exp.lower() in (c['device'] or '').lower():
                checks.append(_chk(f'{lbl} · device', 'ok', c['device']))
            else:
                checks.append(_chk(f'{lbl} · device', 'fail',
                                   f"got '{c['device'] or 'none'}', expected '{exp}'"))
        if c['errors']:
            checks.append(_chk(f'{lbl} · signal', 'fail', c['errors']))
        elif not c['active']:
            checks.append(_chk(f'{lbl} · signal', 'fail', 'camera is inactive'))
        elif c['width'] <= 1:
            checks.append(_chk(f'{lbl} · signal', 'fail', 'no image (0x0)'))
        else:
            checks.append(_chk(f'{lbl} · signal', 'ok', f"{c['width']}x{c['height']}"))
        if c['active'] and c['width'] > 1:
            if c.get('fresh') is False:
                checks.append(_chk(f'{lbl} · feed', 'fail',
                                   f'frozen — pixels unchanged for {FREEZE_SECONDS}s+'))
            elif c.get('fresh') is None:
                checks.append(_chk(f'{lbl} · feed', 'warn',
                                   'collecting baseline — run check again in a few seconds'))
            else:
                checks.append(_chk(f'{lbl} · feed', 'ok', 'frames updating'))

    # --- monitors
    mons = _monitors()
    mcfg = HEALTH.get('monitors', {})
    if mcfg.get('count'):
        ok = len(mons) == mcfg['count']
        checks.append(_chk('monitors · count', 'ok' if ok else 'fail',
                           f"found {len(mons)}, expected {mcfg['count']}"))
    if mcfg.get('resolutions'):
        want = sorted(tuple(r) for r in mcfg['resolutions'])
        got = sorted((m['width'], m['height']) for m in mons)
        ok = want == got
        checks.append(_chk('monitors · resolution', 'ok' if ok else 'fail',
                           f"got {['%dx%d' % g for g in got]}, expected {['%dx%d' % w for w in want]}"))

    # --- windows
    for wexp in HEALTH.get('windows', []):
        name = wexp['path'].rsplit('/', 1)[-1]
        info = _window_info(op(wexp['path']))
        if info is None:
            checks.append(_chk(f'{name} · window', 'fail', f"{wexp['path']} not found"))
            continue
        if wexp.get('mustBeOpen') and not info['isOpen']:
            checks.append(_chk(f'{name} · open', 'fail', 'window is closed'))
        else:
            checks.append(_chk(f'{name} · open', 'ok', 'open' if info['isOpen'] else 'closed (not required)'))
        if 'monitor' in wexp:
            ok = info['monitor'] == wexp['monitor']
            checks.append(_chk(f'{name} · monitor', 'ok' if ok else 'fail',
                               f"on monitor {info['monitor']}, expected {wexp['monitor']}"))
        if wexp.get('source'):
            ok = info['source'] == wexp['source']
            checks.append(_chk(f'{name} · source', 'ok' if ok else 'fail',
                               f"showing {info['source'] or 'nothing'}, expected {wexp['source']}"))

    # --- performance
    fps = _actual_fps()
    target = project.cookRate
    ratio = HEALTH.get('fpsRatio', 0.9)
    if fps >= target * ratio:
        checks.append(_chk('performance · fps', 'ok', f'{fps} / {target}'))
    else:
        checks.append(_chk('performance · fps', 'fail', f'{fps} / {target} (below {int(ratio * 100)}%)'))
    if not project.realTime:
        checks.append(_chk('performance · realtime', 'warn', 'realtime is OFF'))

    # --- show state
    if HEALTH.get('patternShouldBeOff'):
        p = _test_pattern_par()
        if p is not None and bool(p.eval()):
            checks.append(_chk('show · test pattern', 'warn', 'test pattern is ON'))
        else:
            checks.append(_chk('show · test pattern', 'ok', 'off'))

    # --- asset path
    if HEALTH.get('checkAssetPath'):
        path = _settings().get('values', {}).get('asset_path', '')
        if path and os.path.isdir(path):
            checks.append(_chk('files · asset_path', 'ok', path))
        else:
            checks.append(_chk('files · asset_path', 'fail', f"missing: {path or '(empty)'}"))

    summary = {'ok': 0, 'warn': 0, 'fail': 0}
    for c in checks:
        summary[c['status']] += 1
    return {'checks': checks, 'summary': summary, 'time': time.strftime('%H:%M:%S')}


def _status():
    perform = False
    try:
        perform = bool(ui.performMode)
    except Exception:
        pass
    pattern_par = _test_pattern_par()
    return {
        'testPattern': bool(pattern_par.eval()) if pattern_par is not None else None,
        'showMode': _show_mode(),
        'project': project.name,
        'tdVersion': f"{app.version}",
        'fps': _actual_fps(),
        'targetFps': project.cookRate,
        'realtime': bool(project.realTime),
        'performMode': perform,
        'time': time.strftime('%H:%M:%S'),
        'cameras': _cameras(),
        'panels': _panels(),
        'outputs': _outputs(),
        'monitors': _monitors(),
        'windows': _windows(),
        'settings': _settings(),
    }


# ---------------------------------------------------------------- actions

def _do_action(payload):
    action = payload.get('action', '')

    if action == 'cam_active':
        o = op(payload.get('path', ''))
        if not o:
            return {'ok': False, 'error': 'camera not found'}
        o.par.active = 1 if payload.get('value') else 0
        return {'ok': True}

    if action == 'open_window':
        o = op(payload.get('path', ''))
        if not o:
            return {'ok': False, 'error': 'window not found'}
        o.par.winopen.pulse()
        return {'ok': True}

    if action == 'close_window':
        o = op(payload.get('path', ''))
        if not o:
            return {'ok': False, 'error': 'window not found'}
        o.par.winclose.pulse()
        return {'ok': True}

    if action == 'perform_mode':
        ui.performMode = bool(payload.get('value'))
        return {'ok': True}

    if action == 'set_par':
        op_path = payload.get('op', '')
        par_name = payload.get('par', '')
        if not (_par_allowed(op_path, par_name) or _camera_device_allowed(op_path, par_name)):
            return {'ok': False, 'error': f'{op_path}:{par_name} is not exposed to the web UI'}
        o = op(op_path)
        p = getattr(o.par, par_name, None) if o else None
        if p is None:
            return {'ok': False, 'error': f'par {par_name} not found on {op_path}'}
        if p.style == 'Pulse':
            p.pulse()
        else:
            p.val = payload.get('value')
        return {'ok': True}

    if action == 'set_window_monitor':
        o = op(payload.get('path', ''))
        if not o or o.type != 'window':
            return {'ok': False, 'error': 'window not found'}
        o.par.monitor = int(payload.get('monitor', 0))
        return {'ok': True}

    if action == 'test_pattern':
        p = _test_pattern_par()
        if p is None:
            return {'ok': False, 'error': f"par {TEST_PATTERN['par']} not found on {TEST_PATTERN['op']}"}
        p.val = 1 if payload.get('value') else 0
        return {'ok': True}

    if action == 'set_settings':
        tbl = _settings_table()
        if not tbl:
            return {'ok': False, 'error': 'table_settings not found'}
        node = _node_name()
        if tbl[node, 0] is None:
            return {'ok': False, 'error': f'no row for node {node}'}
        for col, val in payload.get('values', {}).items():
            if tbl[node, col] is not None:
                tbl[node, col] = str(val)
        # write back to the .tsv if the table is file-backed
        try:
            f = tbl.par.file.eval()
            if f:
                tbl.save(f)
        except Exception:
            pass
        # re-apply settings
        try:
            op.SETTINGS.Startup()
        except Exception:
            pass
        return {'ok': True}

    if action == 'reload_settings':
        try:
            op.SETTINGS.Startup()
            return {'ok': True}
        except Exception as e:
            return {'ok': False, 'error': str(e)}

    if action == 'show_mode':
        op('/project1/UI').store('show_mode', bool(payload.get('value')))
        return {'ok': True}

    if action == 'set_inside_cam':
        # CAM Inside dropdown: 'auto' = plugin's own name matching (reload),
        # '0'/'1'/... = open the n-th browser device via JS injection.
        val = str(payload.get('value', ''))
        mp = op('/project1/MediaPipe')
        if mp is None:
            return {'ok': False, 'error': 'MediaPipe not found'}
        if val == 'auto':
            op('/project1/UI').store('inside_cam_idx', 'auto')
            mp.par.Reset.pulse()   # reload the page -> name matching again
            return {'ok': True}
        try:
            idx = int(val)
        except ValueError:
            return {'ok': False, 'error': f'bad camera index: {val}'}
        if not _inject_inside_cam(idx):
            return {'ok': False, 'error': f'{MP_WEBRENDER} not found'}
        op('/project1/UI').store('inside_cam_idx', idx)
        return {'ok': True}

    if action == 'swap_sdi':
        # Swap the SDI cards between the inside (YOLO_FACES) and outside
        # (CAM_BEHIND) Video Device Ins: release both, exchange devices,
        # reacquire. Both are TD-native now, so this is fast and reliable.
        v_in = op('/project1/YOLO_FACES/videodevin1')
        v_out = op('/project1/CAM_BEHIND/videodevin1')
        if v_in is None or v_out is None:
            return {'ok': False, 'error': 'videodevin not found in YOLO_FACES or CAM_BEHIND'}
        ni = len(v_in.par.device.menuNames)
        no = len(v_out.par.device.menuNames)
        if ni < 2 or no < 2:
            return {'ok': False, 'error': 'fewer than 2 capture devices found'}
        di_in = v_in.par.device.menuIndex
        di_out = v_out.par.device.menuIndex
        v_in.par.active = 0
        v_out.par.active = 0
        run("a = op('/project1/YOLO_FACES/videodevin1')\n"
            "b = op('/project1/CAM_BEHIND/videodevin1')\n"
            "a.par.device.menuIndex = {}\n"
            "b.par.device.menuIndex = {}\n"
            "a.par.active = 1\n"
            "b.par.active = 1".format(di_out, di_in),
            delayMilliSeconds=900)
        return {'ok': True}

    return {'ok': False, 'error': f'unknown action: {action}'}


# ---------------------------------------------------------------- serving

def _json_response(response, obj, code=200):
    response['statusCode'] = code
    response['statusReason'] = 'OK' if code == 200 else 'Error'
    response['Content-Type'] = 'application/json'
    response['data'] = json.dumps(obj)
    return response


def _serve_static(uri, response):
    rel = uri.lstrip('/') or 'index.html'
    full = os.path.normpath(os.path.join(WEB_ROOT, rel))
    if not full.startswith(WEB_ROOT) or not os.path.isfile(full):
        response['statusCode'] = 404
        response['statusReason'] = 'Not Found'
        response['data'] = '404 - not found'
        return response
    ext = os.path.splitext(full)[1].lower()
    with open(full, 'rb') as f:
        response['data'] = f.read()
    response['statusCode'] = 200
    response['statusReason'] = 'OK'
    response['Content-Type'] = MIME.get(ext, 'application/octet-stream')
    # the UI files change often while iterating — never let the browser cache them
    response['Cache-Control'] = 'no-store'
    return response


def _serve_cam(request, response):
    if _show_mode():
        response['statusCode'] = 204
        response['statusReason'] = 'No Content'
        response['data'] = ''
        return response
    path = request.get('pars', {}).get('path', '')
    o = op(path)
    if not o or not o.isTOP:
        return _json_response(response, {'error': 'TOP not found'}, 404)
    now = time.perf_counter()
    cached = _preview_cache.get(path)
    if cached and now - cached[0] < PREVIEW_TTL:
        data = cached[1]     # several tabs / fast pollers share one grab
    else:
        try:
            sel, res = _ensure_preview_ops()
            sel.par.top = path
            w = max(o.width, 1)
            res.par.resolutionw = min(PREVIEW_MAX_W, w)
            res.par.resolutionh = max(
                1, round(min(PREVIEW_MAX_W, w) * o.height / float(w)))
            data = bytes(res.saveByteArray('.jpg', quality=0.6))
        except Exception:
            try:
                data = bytes(o.saveByteArray('.jpg', quality=0.6))
            except TypeError:
                data = bytes(o.saveByteArray('.jpg'))
        _preview_cache[path] = (now, data)
    response['statusCode'] = 200
    response['statusReason'] = 'OK'
    response['Content-Type'] = 'image/jpeg'
    response['Cache-Control'] = 'no-store'
    response['data'] = data
    return response


def onHTTPRequest(dat, request, response):
    uri = request.get('uri', '/')
    _req_log.append((round(time.perf_counter(), 3), uri))
    if len(_req_log) > 300:
        del _req_log[:100]
    try:
        if uri.startswith('/api/reqlog'):
            return _json_response(response, {'now': round(time.perf_counter(), 3),
                                             'log': _req_log[-120:]})

        if uri.startswith('/api/status'):
            return _json_response(response, _status())

        if uri.startswith('/api/health'):
            return _json_response(response, _health())

        if uri.startswith('/api/cam'):
            return _serve_cam(request, response)

        if uri.startswith('/api/pars'):
            o = op(request.get('pars', {}).get('path', ''))
            if not o:
                return _json_response(response, {'error': 'op not found'}, 404)
            pars = [_par_info(p) for p in o.customPars]
            return _json_response(response, {'path': o.path, 'pars': pars})

        if uri.startswith('/api/ops'):
            o = op(request.get('pars', {}).get('path', '/'))
            if not o or not o.isCOMP:
                return _json_response(response, {'error': 'COMP not found'}, 404)
            kids = [{'name': c.name, 'type': c.type,
                     'family': c.family if isinstance(c.family, str) else str(c.family),
                     'size': f'{c.width}x{c.height}' if c.isTOP else ''}
                    for c in o.children]
            return _json_response(response, {'path': o.path, 'children': kids})

        if uri.startswith('/api/exec'):
            if not CONSOLE_ALLOW_REMOTE and not _client_is_local(request):
                return _json_response(response, {'error': 'console is localhost-only'}, 403)
            body = request.get('data', b'') or b'{}'
            if isinstance(body, (bytes, bytearray)):
                body = body.decode('utf-8-sig')
            code = json.loads(body).get('code', '')
            return _json_response(response, _console_exec(code))

        if uri.startswith('/api/action'):
            body = request.get('data', b'') or b'{}'
            if isinstance(body, (bytes, bytearray)):
                body = body.decode('utf-8-sig')
            return _json_response(response, _do_action(json.loads(body)))

        return _serve_static(uri, response)

    except Exception:
        debug(traceback.format_exc())
        return _json_response(response, {'error': traceback.format_exc()}, 500)


# ---------------------------------------------------------------- websocket / lifecycle

def onWebSocketOpen(dat, client, uri):
    return


def onWebSocketClose(dat, client):
    return


def onWebSocketReceiveText(dat, client, data):
    return


def onWebSocketReceiveBinary(dat, client, data):
    return


def onWebSocketReceivePing(dat, client, data):
    dat.webSocketSendPong(client, data=data)
    return


def onWebSocketReceivePong(dat, client, data):
    return


def onServerStart(dat):
    print(f'ONAY UI server started on port {dat.par.port.eval()}')
    return


def onServerStop(dat):
    return
