#!/usr/bin/env python3
"""A command-line remote for the camera over its own Wi-Fi.

Built on :mod:`scalarwebapi`, which is only the transport. This file is the policy: what the
remote offers, what it refuses to pretend, and how it reports a method the camera does not
expose.

The honest part matters more than the feature list. On the owner's ILCE-6400 the running
application registers 47 methods, and the shutter (``actTakePicture``) is **not** among them,
although the packaged ``orb-server`` contains 221 camera methods including it (E0659). So this
remote:

* discovers its own verbs from the camera at start-up instead of assuming a fixed set, and
* refuses a command the camera did not advertise, saying so, rather than sending it and
  reporting a failure that reads like a bug.

Two facts measured on hardware shape the stream handling: ``startLiveview`` must be called
with no parameters, and the stream is MJPEG at 640x424, roughly 38 KB per frame.

Offline mode exists so the formatting is testable and so the tool is useful before a camera is
attached: ``--offline`` prints a recorded session and contacts nothing.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path
from typing import Any, Iterable

sys.path.insert(0, str(Path(__file__).resolve().parent))

from scalarwebapi import (  # noqa: E402
    DEFAULT_HOST,
    DEFAULT_PORT,
    ApiError,
    ScalarWebApi,
    TransportError,
)

#: Commands this remote can perform, and the camera method each one needs. A command is
#: offered only when its method appears in the camera's own advertised list, so the remote
#: cannot claim a capability the camera is not currently registering.
COMMANDS: dict[str, str] = {
    "shoot": "actTakePicture",
    "half-press": "actHalfPressShutter",
    "half-release": "cancelHalfPressShutter",
    "burst-start": "startContShooting",
    "burst-stop": "stopContShooting",
    "live-start": "startLiveview",
    "live-stop": "stopLiveview",
}

#: Settable settings: command name -> (getter, setter, argument name).
SETTINGS: dict[str, tuple[str, str, str]] = {
    "iso": ("getIsoSpeedRate", "setIsoSpeedRate", "iso"),
    "aperture": ("getFNumber", "setFNumber", "fnumber"),
    "shutter": ("getShutterSpeed", "setShutterSpeed", "shutterspeed"),
    "wb": ("getWhiteBalance", "setWhiteBalance", "whitebalance"),
    "ev": ("getExposureCompensation", "setExposureCompensation", "exposurecompensation"),
}


def supported_commands(api_list: Iterable[str]) -> list[str]:
    """The commands whose method the camera actually advertises."""
    advertised = set(api_list)
    return [name for name, method in COMMANDS.items() if method in advertised]


def unsupported_commands(api_list: Iterable[str]) -> list[str]:
    """The commands the camera cannot serve right now, named so the gap is visible."""
    advertised = set(api_list)
    return [name for name, method in COMMANDS.items() if method not in advertised]


def describe_capability(api_list: list[str]) -> str:
    """One sentence stating what this camera exposes and what is therefore refused."""
    have = supported_commands(api_list)
    lack = unsupported_commands(api_list)
    lines = [f"camera exposes {len(api_list)} methods"]
    lines.append("available: " + (", ".join(have) if have else "none"))
    if lack:
        lines.append("refused (method not advertised): " + ", ".join(lack))
    if "shoot" in lack and "half-press" in have:
        lines.append(
            "the camera advertises half-press but not the shutter; sending the shutter anyway "
            "returns error 1 (state refusal), not a success"
        )
    return "\n".join(lines)


def jpeg_frames(stream: bytes) -> list[bytes]:
    """Every complete JPEG in a byte buffer, in order.

    The stream is a concatenation of JPEGs with no length prefix, so a frame is delimited by
    its own markers. An unterminated tail is dropped rather than returned half-written.
    """
    frames: list[bytes] = []
    position = 0
    while True:
        start = stream.find(b"\xff\xd8\xff", position)
        if start < 0:
            break
        end = stream.find(b"\xff\xd9", start)
        if end < 0:
            break
        frames.append(stream[start:end + 2])
        position = end + 2
    return frames


def read_stream(url: str, *, frames: int = 1, timeout: float = 15.0) -> bytes:
    """Read until ``frames`` complete JPEGs have arrived, then stop."""
    request = urllib.request.Request(url, headers={"User-Agent": "a6400-remote"})
    buffer = b""
    with urllib.request.urlopen(request, timeout=timeout) as response:
        while len(jpeg_frames(buffer)) < frames:
            chunk = response.read(65536)
            if not chunk:
                break
            buffer += chunk
            if len(buffer) > 8 * 1024 * 1024:
                break
    return buffer


def run_offline(path: Path, command: str) -> int:
    """Format a recorded session so the report shape is testable without a camera."""
    recorded = json.loads(path.read_text(encoding="utf-8"))
    api_list = recorded.get("api_list", [])
    print(describe_capability(api_list))
    if command:
        answer = recorded.get("answers", {}).get(command)
        print(f"\n{command}: {json.dumps(answer) if answer is not None else 'not recorded'}")
    return 0


def run_live(host: str, port: int, command: str, argument: str | None) -> int:
    api = ScalarWebApi(host=host, port=port)
    try:
        api_list = api.available_api_list()
    except (TransportError, ApiError) as exc:
        print(f"cannot reach the camera at {host}:{port}: {exc}", file=sys.stderr)
        return 2

    print(describe_capability(api_list))

    if not command:
        return 0

    if command in SETTINGS:
        getter, setter, param = SETTINGS[command]
        try:
            if argument is None:
                print(f"{command}: {json.dumps(api.call(getter))}")
                return 0
            api.call(setter, [argument])
            print(f"{command} set to {argument}; now {json.dumps(api.call(getter))}")
            return 0
        except ApiError as exc:
            print(f"{command}: {exc}", file=sys.stderr)
            return 1

    method = COMMANDS.get(command)
    if method is None:
        print(f"unknown command {command!r}; known: {', '.join(sorted(COMMANDS) + sorted(SETTINGS))}",
              file=sys.stderr)
        return 2

    if method not in set(api_list):
        print(
            f"{command}: refused before sending. The camera does not advertise {method} "
            f"among its {len(api_list)} methods, so it cannot be performed right now. "
            "Sending it anyway returns error 1, which reads like a fault in the remote and is not.",
            file=sys.stderr,
        )
        return 1

    try:
        result = api.call(method)
    except (ApiError, TransportError) as exc:
        print(f"{command}: {exc}", file=sys.stderr)
        return 1

    if command == "live-start" and isinstance(result, list) and result:
        url = result[0]
        print(f"live view: {url}")
        frame = read_stream(url)
        found = jpeg_frames(frame)
        if found:
            print(f"received {len(found)} frame(s), first {len(found[0])} bytes")
        else:
            print("stream opened but no complete frame arrived")
        return 0

    print(f"{command}: {json.dumps(result)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", nargs="?", default="",
                        help="one of: " + ", ".join(sorted(COMMANDS) + sorted(SETTINGS)))
    parser.add_argument("argument", nargs="?", default=None,
                        help="new value for a setting, e.g. 400 for iso")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--offline", type=Path, default=None,
                        help="format a recorded session instead of contacting a camera")
    args = parser.parse_args(argv)

    if args.offline:
        return run_offline(args.offline, args.command)
    return run_live(args.host, args.port, args.command, args.argument)


if __name__ == "__main__":
    sys.exit(main())
