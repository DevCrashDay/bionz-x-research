#!/usr/bin/env python3
"""A control panel for the camera in a browser: live view, settings, and a shutter that tells the truth.

The terminal remote (``camera_remote.py``) is for scripts. This is the thing you actually use to
shoot: a page with the camera's live image, its settings, and one button per action.

Everything it offers is read from the camera at start-up. The panel has no hard-coded feature
list, so it cannot drift from what the body really does: a control appears only when the camera
advertises the method behind it, and a control the camera withheld is shown disabled with the
camera's own reason. On the owner's ILCE-6400 that means the half-press, the burst and the
settings work, while the single-shot shutter appears greyed out as **"No Such Method"** — which is
true, and is not the same thing as a broken panel.

Two design rules that are not obvious:

* Proxy everything through this process. The camera serves its API on plain HTTP with no CORS
  headers, so a browser page opened from ``file://`` or another origin cannot call it directly.
* Stream frames as multipart rather than base64 in JSON. A 38 KB frame base64-encoded costs a
  third more bytes and arrives as one unrenderable blob; multipart lets ``<img>`` show it live.

Usage::

    python3 -I tools/research/camera_panel.py --host 192.168.122.1
    # then open http://127.0.0.1:8765/

Offline, with a recorded session and no camera at all::

    python3 -I tools/research/camera_panel.py --offline \\
        docs/evidence/cross_model/a6400-scalarwebapi-session.json
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))

from camera_remote import jpeg_frames  # noqa: E402
from scalarwebapi import (  # noqa: E402
    DEFAULT_HOST,
    DEFAULT_PORT,
    ApiError,
    ScalarWebApi,
    TransportError,
)

#: Controls the panel can draw. Each one names the method that must be advertised for it to be
#: enabled, so the page never offers something the camera will refuse.
CONTROLS: dict[str, dict[str, str]] = {
    "half_press": {"method": "actHalfPressShutter", "label": "Half-press (focus/AE)"},
    "half_release": {"method": "cancelHalfPressShutter", "label": "Release half-press"},
    "shutter": {"method": "actTakePicture", "label": "Shutter"},
    "burst": {"method": "startContShooting", "label": "Burst start"},
    "burst_stop": {"method": "stopContShooting", "label": "Burst stop"},
}

#: Settings the panel exposes as dropdowns. ``getter``/``setter`` are the camera methods;
#: ``choices`` is the method naming the allowed values.
SETTINGS: dict[str, dict[str, str]] = {
    "iso": {"getter": "getIsoSpeedRate", "setter": "setIsoSpeedRate",
            "choices": "getSupportedIsoSpeedRate", "label": "ISO"},
    "aperture": {"getter": "getFNumber", "setter": "setFNumber",
                 "choices": "getSupportedFNumber", "label": "Aperture"},
    "shutter_speed": {"getter": "getShutterSpeed", "setter": "setShutterSpeed",
                      "choices": "getSupportedShutterSpeed", "label": "Shutter speed"},
    "white_balance": {"getter": "getWhiteBalance", "setter": "setWhiteBalance",
                      "choices": "getSupportedWhiteBalance", "label": "White balance"},
    "ev": {"getter": "getExposureCompensation", "setter": "setExposureCompensation",
           "choices": "getSupportedExposureCompensation", "label": "Exposure compensation"},
}

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>a6400 remote</title>
<style>
  :root { color-scheme: dark; }
  body { margin:0; font:14px/1.45 system-ui,sans-serif; background:#15171a; color:#e6e6e6;
         display:grid; grid-template-columns: minmax(320px,1fr) 340px; gap:16px; padding:16px; }
  h2 { font-size:13px; text-transform:uppercase; letter-spacing:.08em; color:#8d939c;
       margin:18px 0 8px; }
  #stage { background:#000; border-radius:8px; display:flex; align-items:center;
           justify-content:center; min-height:280px; overflow:hidden; }
  #live { max-width:100%; max-height:70vh; display:block; }
  .row { display:flex; gap:8px; flex-wrap:wrap; margin-bottom:8px; }
  button { flex:1 1 130px; padding:11px 12px; border-radius:7px; border:1px solid #33373d;
           background:#22262b; color:#e6e6e6; font-size:13px; cursor:pointer; }
  button:hover:not(:disabled) { background:#2c3138; }
  button:disabled { opacity:.45; cursor:not-allowed; }
  button.primary { background:#1f4d34; border-color:#2c6b47; }
  button.primary:hover:not(:disabled) { background:#276042; }
  label { display:block; margin-bottom:10px; }
  select { width:100%; margin-top:4px; padding:7px; border-radius:6px; background:#22262b;
           color:#e6e6e6; border:1px solid #33373d; font-size:13px; }
  #log { font:12px/1.5 ui-monospace,monospace; color:#9aa2ad; white-space:pre-wrap;
         max-height:150px; overflow:auto; border-top:1px solid #262a2f; padding-top:8px; }
  .badge { display:inline-block; padding:2px 7px; border-radius:20px; font-size:11px;
           background:#3a2a2a; color:#f0a5a5; }
  .badge.ok { background:#22392c; color:#9fd6b4; }
  #status { margin-bottom:10px; color:#8d939c; font-size:12px; }
</style></head>
<body>
<div>
  <div id="stage"><img id="live" alt="camera live view"></div>
  <h2>Actions</h2>
  <div class="row" id="actions"></div>
  <h2>Response</h2>
  <div id="log">ready</div>
</div>
<div>
  <div id="status">connecting…</div>
  <h2>Settings</h2>
  <div id="settings"></div>
  <h2>Camera methods</h2>
  <div id="methods"></div>
</div>
<script>
const state = { api: [], scene: null };

function log(msg) {
  const el = document.getElementById('log');
  el.textContent = new Date().toLocaleTimeString() + '  ' + msg + '\\n' + el.textContent;
}

function disabledReason(method) {
  if (!state.api.length) return 'camera not read yet';
  if (state.api.includes(method)) return null;
  return 'the camera does not advertise ' + method;
}

function buildActions() {
  const holder = document.getElementById('actions');
  holder.innerHTML = '';
  for (const [key, c] of Object.entries(state.scene.controls)) {
    const b = document.createElement('button');
    b.textContent = c.label;
    if (key === 'shutter') b.className = 'primary';
    const why = disabledReason(c.method);
    if (why) { b.disabled = true; b.title = why; }
    b.onclick = () => act(key);
    holder.appendChild(b);
  }
}

async function act(key) {
  log('→ ' + key);
  try {
    const r = await fetch('/api/act?key=' + encodeURIComponent(key), { method: 'POST' });
    const j = await r.json();
    log((j.ok ? '✓ ' : '✗ ') + key + ': ' + (j.detail || ''));
  } catch (e) { log('✗ ' + key + ': ' + e); }
}

async function buildSettings() {
  const holder = document.getElementById('settings');
  holder.innerHTML = '';
  for (const [key, s] of Object.entries(state.scene.settings)) {
    const label = document.createElement('label');
    label.textContent = s.label;
    const sel = document.createElement('select');
    sel.id = 'set-' + key;
    const info = await (await fetch('/api/setting?key=' + key)).json();
    if (info.values) {
      for (const v of info.values) {
        const o = document.createElement('option');
        o.value = v; o.textContent = v;
        if (v === info.current) o.selected = true;
        sel.appendChild(o);
      }
      sel.onchange = async () => {
        const r = await fetch('/api/set?key=' + key + '&value=' + encodeURIComponent(sel.value),
                              { method: 'POST' });
        const j = await r.json();
        log((j.ok ? '✓ ' : '✗ ') + s.label + ' → ' + sel.value + ': ' + (j.detail || ''));
      };
    } else {
      const o = document.createElement('option');
      o.textContent = info.error || 'unavailable';
      sel.appendChild(o); sel.disabled = true;
    }
    label.appendChild(sel);
    holder.appendChild(label);
  }
}

async function boot() {
  const info = await (await fetch('/api/scene')).json();
  state.scene = info;
  state.api = info.api;
  const st = document.getElementById('status');
  st.innerHTML = info.api.length
    ? `<span class="badge ok">${info.api.length} methods</span> ${info.host}`
    : `<span class="badge">no camera</span> ${info.error || ''}`;
  const m = document.getElementById('methods');
  m.innerHTML = '<div style="font:12px ui-monospace,monospace;color:#9aa2ad;max-height:220px;overflow:auto">'
    + info.api.slice().sort().join('<br>') + '</div>';
  buildActions();
  await buildSettings();
  const img = document.getElementById('live');
  if (info.api.includes('startLiveview')) {
    img.src = '/stream?t=' + Date.now();
  } else {
    document.getElementById('stage').textContent = 'live view not advertised';
  }
}
boot();
</script>
</body></html>
"""


class Camera:
    """The panel's one connection to a body, or a recorded session standing in for one."""

    def __init__(self, host: str, port: int, offline: Path | None) -> None:
        self.offline = offline
        self.recorded: dict[str, Any] = {}
        if offline:
            self.recorded = json.loads(offline.read_text(encoding="utf-8"))
            self.api = list(self.recorded.get("api_list", []))
            self.host = "offline"
            self.api_client: ScalarWebApi | None = None
        else:
            self.host = host
            self.api_client = ScalarWebApi(host=host, port=port)
            try:
                self.api = self.api_client.available_api_list()
            except (TransportError, ApiError):
                self.api = []

    def call(self, method: str, params: list[Any] | None = None) -> Any:
        if self.api_client is None:
            # Replay. A setter has no recorded answer, so it is treated as accepted and the
            # matching getter is updated in the replay, exactly as the camera would. Without
            # this the panel would report the old value after a successful change, which is
            # the one thing worse than refusing: a silent lie.
            if method.startswith("set") and params:
                self._apply_replayed_set(method, params[0])
                return []
            answer = self.recorded.get("answers", {}).get(method)
            if answer is None:
                raise ApiError(12, "not recorded", method)
            if "error" in answer:
                raise ApiError(answer["error"][0], answer["error"][1], method)
            return answer.get("result")
        return self.api_client.call(method, params)

    def _apply_replayed_set(self, setter: str, value: Any) -> None:
        """Record a set in the replay so the paired getter answers the new value."""
        answers = self.recorded.setdefault("answers", {})
        getter = "get" + setter[3:]
        current = answers.get(getter)
        if current is None:
            answers[getter] = {"result": [value]}
            return
        field = self._NAME_FIELD.get(getter)
        if field:
            answers[getter] = {"result": [{field: value}]}
        else:
            answers[getter] = {"result": [value]}

    #: The field a value carries its own name in, per method. White balance and the
    #: continuous-shooting modes answer lists of objects rather than lists of strings, and
    #: rendering such an object with ``str`` produces a Python repr a person cannot read.
    _NAME_FIELD = {
        "getSupportedWhiteBalance": "whiteBalanceMode",
        "getWhiteBalance": "whiteBalanceMode",
        "getSupportedContShootingMode": "contShootingMode",
        "getContShootingMode": "contShootingMode",
    }

    def supported_values(self, method: str) -> list[str] | None:
        """The allowed values for a setting, as plain names, whatever shape the camera uses."""
        try:
            raw = self.call(method)
        except (ApiError, TransportError):
            return None
        if not isinstance(raw, list) or not raw:
            return None
        field = self._NAME_FIELD.get(method)
        items = raw[0] if isinstance(raw[0], list) else raw
        if not isinstance(items, list):
            return None
        values: list[str] = []
        for item in items:
            if isinstance(item, dict):
                if field and isinstance(item.get(field), str):
                    values.append(item[field])
                elif isinstance(item.get("candidate"), list):
                    values.extend(str(v) for v in item["candidate"])
                else:
                    continue
            else:
                values.append(str(item))
        return values or None

    def current_value(self, method: str) -> str | None:
        """The current setting as one plain name, unwrapping the object shapes."""
        try:
            raw = self.call(method)
        except (ApiError, TransportError):
            return None
        field = self._NAME_FIELD.get(method)
        value: Any = raw[0] if isinstance(raw, list) and raw else raw
        if isinstance(value, dict) and field:
            return str(value.get(field)) if value.get(field) is not None else None
        if isinstance(value, list) and value:
            return str(value[0])
        return None if value is None else str(value)


def build_handler(camera: Camera) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_args: Any) -> None:  # keep the console quiet
            pass

        def _send(self, body: bytes, content_type: str, status: int = 200) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, payload: dict[str, Any], status: int = 200) -> None:
            self._send(json.dumps(payload).encode(), "application/json", status)

        def do_GET(self) -> None:  # noqa: N802
            url = urlparse(self.path)
            if url.path == "/":
                self._send(PAGE.encode(), "text/html; charset=utf-8")
                return
            if url.path == "/api/scene":
                self._json({
                    "host": camera.host,
                    "api": camera.api,
                    "controls": CONTROLS,
                    "settings": SETTINGS,
                })
                return
            if url.path == "/api/setting":
                key = parse_qs(url.query).get("key", [""])[0]
                spec = SETTINGS.get(key)
                if not spec:
                    self._json({"error": "unknown setting"}, 400)
                    return
                error = None
                current = camera.current_value(spec["getter"])
                if current is None and spec["getter"] not in camera.api:
                    error = f"the camera does not advertise {spec['getter']}"
                values = camera.supported_values(spec["choices"])
                self._json({"current": current, "values": values, "error": error})
                return
            if url.path == "/stream":
                self._stream()
                return
            self._json({"error": "not found"}, 404)

        def do_POST(self) -> None:  # noqa: N802
            url = urlparse(self.path)
            query = parse_qs(url.query)
            if url.path == "/api/act":
                key = query.get("key", [""])[0]
                spec = CONTROLS.get(key)
                if not spec:
                    self._json({"ok": False, "detail": "unknown control"}, 400)
                    return
                method = spec["method"]
                if method not in camera.api:
                    self._json({"ok": False,
                                "detail": f"not advertised by the camera: {method}"}, 409)
                    return
                try:
                    result = camera.call(method)
                except (ApiError, TransportError) as exc:
                    self._json({"ok": False, "detail": str(exc)}, 409)
                    return
                self._json({"ok": True, "detail": json.dumps(result)})
                return
            if url.path == "/api/set":
                key = query.get("key", [""])[0]
                value = query.get("value", [""])[0]
                spec = SETTINGS.get(key)
                if not spec:
                    self._json({"ok": False, "detail": "unknown setting"}, 400)
                    return
                try:
                    camera.call(spec["setter"], [value])
                except (ApiError, TransportError) as exc:
                    self._json({"ok": False, "detail": str(exc)}, 409)
                    return
                now = camera.current_value(spec["getter"])
                self._json({"ok": True, "detail": f"now {now}"})
                return
            self._json({"ok": False, "detail": "not found"}, 404)

        def _stream(self) -> None:
            """Relay the camera's MJPEG as multipart, so an <img> can show it directly."""
            if camera.api_client is None:
                self._json({"error": "no camera"}, 503)
                return
            try:
                url = camera.api_client.liveview_url()
            except (ApiError, TransportError) as exc:
                self._json({"error": str(exc)}, 503)
                return
            self.send_response(200)
            self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            request = urllib.request.Request(url, headers={"User-Agent": "a6400-panel"})
            buffer = b""
            try:
                with urllib.request.urlopen(request, timeout=20) as response:
                    while True:
                        chunk = response.read(65536)
                        if not chunk:
                            break
                        buffer += chunk
                        frames = jpeg_frames(buffer)
                        if not frames:
                            if len(buffer) > 8 * 1024 * 1024:
                                break
                            continue
                        for frame in frames:
                            self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\n")
                            self.wfile.write(f"Content-Length: {len(frame)}\r\n\r\n".encode())
                            self.wfile.write(frame)
                            self.wfile.write(b"\r\n")
                        self.wfile.flush()
                        # Drop what was sent; keep any partial tail.
                        last = buffer.rfind(b"\xff\xd9")
                        buffer = buffer[last + 2:] if last >= 0 else buffer
            except (BrokenPipeError, ConnectionResetError, TimeoutError):
                return
            except Exception as exc:  # a stream that dies must not kill the panel
                print(f"stream ended: {exc}", file=sys.stderr)

    return Handler


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--listen", type=int, default=8765, help="local port for the panel")
    parser.add_argument("--offline", type=Path, default=None,
                        help="replay a recorded session instead of contacting a camera")
    args = parser.parse_args(argv)

    camera = Camera(args.host, args.port, args.offline)
    server = ThreadingHTTPServer(("127.0.0.1", args.listen), build_handler(camera))
    server.daemon_threads = True
    source = f"recorded {args.offline}" if args.offline else f"camera {args.host}:{args.port}"
    print(f"a6400 remote: {len(camera.api)} methods from {source}")
    print(f"open http://127.0.0.1:{args.listen}/")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
