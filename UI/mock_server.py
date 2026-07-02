"""Mock of the TD Web Server DAT endpoints, for testing UI/web in a browser
without TouchDesigner running:  python UI/mock_server.py  ->  http://127.0.0.1:9980
"""
import json
import http.server
import os

WEB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")

STATUS = {
    "project": "ONAY-Main", "tdVersion": "2023.12120", "fps": 58.7,
    "targetFps": 60, "realtime": True, "performMode": False, "testPattern": False,
    "time": "14:22:31",
    "cameras": [
        {"path": "/project1/MediaPipe/out1", "name": "MediaPipe",
         "label": "CAM Inside — Face Tracking",
         "controlPath": None,
         "device": "Logitech BRIO", "active": True, "width": 1920, "height": 1080,
         "connected": True, "errors": "", "warnings": "",
         "deviceMenu": {"op": "/project1/MediaPipe", "par": "Webcam",
                        "value": "brio", "valueLabel": "Logitech BRIO",
                        "menuNames": ["brio", "sdi"],
                        "menuLabels": ["Logitech BRIO", "USB Capture SDI"]}},
        {"path": "/project1/CAM_BEHIND/out1", "name": "CAM_BEHIND",
         "label": "CAM Outside — no FX",
         "controlPath": "/project1/CAM_BEHIND/videodevin1",
         "device": "OBSBOT Tiny", "active": True, "width": 0, "height": 0,
         "connected": False, "errors": "Device not found.", "warnings": "",
         "deviceMenu": {"op": "/project1/CAM_BEHIND/videodevin1", "par": "device",
                        "value": "obsbot", "valueLabel": "OBSBOT Tiny",
                        "menuNames": ["obsbot", "sdi"],
                        "menuLabels": ["OBSBOT Tiny", "USB Capture SDI"]}},
    ],
    "monitors": [
        {"index": 0, "description": "Dell U2723QE", "displayName": r"\\.\DISPLAY1",
         "width": 3840, "height": 2160, "left": 0, "top": 0, "refreshRate": 60, "isPrimary": True},
        {"index": 1, "description": "LG Portrait", "displayName": r"\\.\DISPLAY2",
         "width": 1920, "height": 1920, "left": 3840, "top": 0, "refreshRate": 60, "isPrimary": False},
    ],
    "panels": [
        {"label": "Face Crop", "op": "/project1/CROP", "error": "",
         "camera": "CAM Inside — Face Tracking", "pars": [
            {"name": "Checkrectangle", "label": "Rectangle check", "style": "Toggle", "value": True,
             "min": 0, "max": 1, "menuNames": [], "menuLabels": []},
            {"name": "Index", "label": "GLSL Mode", "style": "Toggle", "value": False,
             "min": 0, "max": 1, "menuNames": [], "menuLabels": []},
            {"name": "Valuex", "label": "Padding", "style": "XYZW", "value": -0.1,
             "min": 0, "max": 1, "menuNames": [], "menuLabels": []},
            {"name": "Valuey", "label": "Padding", "style": "XYZW", "value": 1.0,
             "min": 0, "max": 1, "menuNames": [], "menuLabels": []},
            {"name": "Valuez", "label": "Padding", "style": "XYZW", "value": 1.0,
             "min": 0, "max": 1, "menuNames": [], "menuLabels": []},
            {"name": "Valuew", "label": "Padding", "style": "XYZW", "value": 1.0,
             "min": 0, "max": 1, "menuNames": [], "menuLabels": []},
        ]},
    ],
    "outputs": [
        {"label": "Display 1", "path": "/project1/Monitors_layout/monitor1/output",
         "width": 1920, "height": 1920, "error": "",
         "window": {"path": "/project1/window1", "name": "window1", "isOpen": True,
                    "monitor": 1, "source": "/project1/Monitors_layout"}},
        {"label": "Display 2", "path": "/project1/Monitors_layout/monitor2/output",
         "width": 1920, "height": 1920, "error": "",
         "window": {"path": "/project1/window2", "name": "window2", "isOpen": False,
                    "monitor": 0, "source": "/project1/Main_container"}},
    ],
    "windows": [
        {"path": "/project1/window1", "name": "window1", "isOpen": True, "monitor": 1,
         "source": "/project1/Monitors_layout"},
        {"path": "/project1/window2", "name": "window2", "isOpen": False, "monitor": 0,
         "source": "/project1/Monitors_layout"},
    ],
    "settings": {"node": "WF", "columns": ["asset_path", "resX", "resY", "camX", "camY"],
                 "values": {"asset_path": "C:/Users/days4/Desktop/Projects/2026/Onay_Aug/ASSETS",
                            "resX": "1920", "resY": "1920", "camX": "1920", "camY": "1080"}},
}

# 1x1 grey jpeg
JPEG = bytes.fromhex(
    'ffd8ffe000104a46494600010100000100010000ffdb004300080606070605080707070909080a0c140d0c0b0b0c1912130f141d1a1f1e1d1a1c1c20242e2720222c231c1c2837292c30313434341f27393d38323c2e333432ffc0000b080001000101011100ffc4001f0000010501010101010100000000000000000102030405060708090a0bffc400b5100002010303020403050504040000017d01020300041105122131410613516107227114328191a1082342b1c11552d1f02433627282090a161718191a25262728292a3435363738393a434445464748494a535455565758595a636465666768696a737475767778797a838485868788898a92939495969798999aa2a3a4a5a6a7a8a9aab2b3b4b5b6b7b8b9bac2c3c4c5c6c7c8c9cad2d3d4d5d6d7d8d9dae1e2e3e4e5e6e7e8e9eaf1f2f3f4f5f6f7f8f9faffda0008010100003f00f7fa28a2803fffd9')


class H(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=WEB, **kw)

    def _json(self, obj):
        body = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith('/api/status'):
            return self._json(STATUS)
        if self.path.startswith('/api/cam'):
            self.send_response(200)
            self.send_header('Content-Type', 'image/jpeg')
            self.send_header('Content-Length', str(len(JPEG)))
            self.end_headers()
            self.wfile.write(JPEG)
            return
        return super().do_GET()

    def do_POST(self):
        n = int(self.headers.get('Content-Length', 0))
        payload = json.loads(self.rfile.read(n) or b'{}')
        print('ACTION:', payload)
        if payload.get('action') == 'perform_mode':
            STATUS['performMode'] = bool(payload.get('value'))
        if payload.get('action') == 'test_pattern':
            STATUS['testPattern'] = bool(payload.get('value'))
        if payload.get('action') == 'set_window_monitor':
            for o in STATUS['outputs']:
                if o.get('window') and o['window']['path'] == payload.get('path'):
                    o['window']['monitor'] = int(payload.get('monitor', 0))
            for w in STATUS['windows']:
                if w['path'] == payload.get('path'):
                    w['monitor'] = int(payload.get('monitor', 0))
        if payload.get('action') == 'set_par':
            for cam in STATUS['cameras']:
                dm = cam.get('deviceMenu')
                if dm and dm['op'] == payload.get('op') and dm['par'] == payload.get('par'):
                    dm['value'] = payload.get('value')
                    i = dm['menuNames'].index(dm['value'])
                    dm['valueLabel'] = dm['menuLabels'][i]
                    cam['device'] = dm['valueLabel']
            for panel in STATUS['panels']:
                if panel['op'] != payload.get('op'):
                    continue
                for p in panel['pars']:
                    if p['name'] == payload.get('par'):
                        p['value'] = payload.get('value')
        if payload.get('action') == 'cam_active':
            for c in STATUS['cameras']:
                if c['path'] == payload.get('path'):
                    c['active'] = bool(payload.get('value'))
        return self._json({'ok': True})

    def log_message(self, *a):
        pass


PORT = int(os.environ.get('PORT', '9980'))
print(f'mock on http://127.0.0.1:{PORT}')
http.server.ThreadingHTTPServer(('127.0.0.1', PORT), H).serve_forever()
