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

import json
import os
import time
import traceback

WEB_ROOT = os.path.normpath(os.path.join(project.folder, 'UI', 'web'))

# The camera views shown on the dashboard. 'name' is the COMP (or TOP) to look
# for anywhere in the project; the preview shows its output TOP, and the
# Video Device In found inside it provides device/connection status.
CAMERA_VIEWS = [
    # devicePar: custom par on the comp that holds the camera device name
    {'label': 'CAM Inside — Face Tracking', 'name': 'MediaPipe', 'devicePar': 'Webcam'},
    {'label': 'CAM Outside — no FX', 'name': 'CAM_BEHIND'},
]

# Custom-parameter panels shown under the Cameras section.
# Styles supported by the web UI: Toggle, Menu, Pulse, Float/Int/XYZW/WH/RGBA (as number inputs).
# 'camera': render the panel inside that camera's card instead of its own section
PARAM_PANELS = [
    {'label': 'Face Crop', 'op': '/project1/CROP',
     'camera': 'CAM Inside — Face Tracking',
     'pars': ['Checkrectangle', 'Index', 'Valuex', 'Valuey', 'Valuez', 'Valuew']},
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
    now_wall = time.time()
    now_frame = absTime.frame
    dt = now_wall - _fps_state['wall']
    if dt > 0.2:
        df = now_frame - _fps_state['frame']
        if _fps_state['wall'] > 0 and df >= 0:
            _fps_state['fps'] = df / dt
        _fps_state['wall'] = now_wall
        _fps_state['frame'] = now_frame
    return round(_fps_state['fps'], 1)


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


def _videodevin_status(comp):
    """Device/connection info from the Video Device In inside a comp (if any)."""
    vids = _find_videodevins(comp) if comp and comp.isCOMP else []
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


def _device_menu(op_path, p):
    """Menu info for a camera-device parameter, for the card's dropdown."""
    names = list(getattr(p, 'menuNames', []) or [])
    labels = list(getattr(p, 'menuLabels', []) or [])
    val = str(p.eval())
    try:
        label = labels[names.index(val)]
    except (ValueError, IndexError):
        label = val.split('|||')[-1] if '|||' in val else val
    return {'op': op_path, 'par': p.name, 'value': val, 'valueLabel': label,
            'menuNames': names, 'menuLabels': labels}


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


def _cameras():
    cams = []
    for view in CAMERA_VIEWS:
        comp = _find_view_op(view)
        top = _resolve_view_top(comp)
        if comp is None or top is None:
            cams.append({
                'path': '', 'name': view['name'], 'label': view['label'],
                'controlPath': None, 'device': '', 'active': False,
                'width': 0, 'height': 0, 'connected': False,
                'errors': f"'{view['name']}' not found in project", 'warnings': '',
            })
            continue
        vid = _videodevin_status(comp)
        dev_menu = None
        if vid['controlPath']:
            v = op(vid['controlPath'])
            p = getattr(v.par, 'device', None) if v else None
            if p is not None:
                dev_menu = _device_menu(v.path, p)
        elif view.get('devicePar'):
            p = getattr(comp.par, view['devicePar'], None)
            if p is not None:
                dev_menu = _device_menu(comp.path, p)
        if dev_menu and not vid['device']:
            vid['device'] = dev_menu['valueLabel']
        connected = vid['active'] and top.width > 1 and not vid['errors']
        cams.append({
            'path': top.path,
            'name': comp.name,
            'label': view['label'],
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
    """Resolve a view config entry: exact 'path' first, else search by 'name'."""
    if view.get('path'):
        return op(view['path'])
    hits = root.findChildren(name=view['name'])
    return hits[0] if hits else None


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
    out = []
    for w in sorted(root.findChildren(type=windowCOMP), key=lambda o: o.path):
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


def _status():
    perform = False
    try:
        perform = bool(ui.performMode)
    except Exception:
        pass
    pattern_par = _test_pattern_par()
    return {
        'testPattern': bool(pattern_par.eval()) if pattern_par is not None else None,
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
    path = request.get('pars', {}).get('path', '')
    o = op(path)
    if not o or not o.isTOP:
        return _json_response(response, {'error': 'TOP not found'}, 404)
    try:
        data = o.saveByteArray('.jpg', quality=0.6)
    except TypeError:
        data = o.saveByteArray('.jpg')
    response['statusCode'] = 200
    response['statusReason'] = 'OK'
    response['Content-Type'] = 'image/jpeg'
    response['Cache-Control'] = 'no-store'
    response['data'] = bytes(data)
    return response


def onHTTPRequest(dat, request, response):
    uri = request.get('uri', '/')
    try:
        if uri.startswith('/api/status'):
            return _json_response(response, _status())

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

        if uri.startswith('/api/action'):
            body = request.get('data', b'') or b'{}'
            if isinstance(body, (bytes, bytearray)):
                body = body.decode('utf-8')
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
