"""Bounded virtual-USB-host observation of the QEMU sony-a6400 DWC3 gadget model.

Boots the captured inputs with the opt-in `cxd90045-dwc3` model, the virtual VBUS cable and the
CID-high option, attaches a host socket to the model's chardev, and records what the original
guest kernel does when the cable is attached and a USB host speaks to it:

1. guest DWC3/UDC state before attach (serial shell commands);
2. QMP `usb-vbus=true`, then the model's `CONN` line (USB reset + connect done events);
3. optionally one standard SETUP packet (default GET_DESCRIPTOR(device, 18 bytes));
4. every line the model sends back (`IN <ep> <hex>`), guest state after attach, `DISC`, VBUS off.

Nothing is answered on the guest's behalf: an absent IN reply is reported as absent.
Run inside WSL from the repository root:

    python3 -m tools.research.qemu_usb_host_probe --qemu-dir ~/sony-a6400-qemu-11.1.3 \
        --inputs ~/a6400-inputs/inputs-moddev-1 --report-dir artifacts/qemu-a6400/NEW_DIR
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import socket
import subprocess
import tempfile
import time
from pathlib import Path

from tools.research.qemu_boot_probe import Qmp

PROJECT_ROOT = Path(__file__).resolve().parents[2]
FIRMWARE_MARKER = b"Created HeapAllocator:"
GET_DEVICE_DESCRIPTOR = "8006000100001200"
STATE_COMMAND = ("echo __USB_STATE_{tag}__; grep -i -E 'dwc|usb|fusb' /proc/interrupts; "
                 "ls /sys/class/udc 2>&1; for f in /sys/class/udc/*/state /sys/class/udc/*/current_speed; "
                 "do echo $f=$(cat $f 2>&1); done; dmesg | grep -v AL_EMMC | grep -i -E 'dwc|udc|gadget|ep0|otg'; "
                 "echo __USB_STATE_{tag}_END__")


# DWC3 model registers read through the guest physical address space (the model's own read handler).
DWC3_REGISTERS = {"GCTL": 0xF020C110, "GEVNTADRLO0": 0xF020C400, "GEVNTSIZ0": 0xF020C408,
                  "GEVNTCOUNT0": 0xF020C40C, "DCFG": 0xF020C700, "DCTL": 0xF020C704,
                  "DEVTEN": 0xF020C708, "DSTS": 0xF020C70C, "DALEPENA": 0xF020C720}


def read_registers(qmp) -> dict:
    values = {}
    for name, address in DWC3_REGISTERS.items():
        reply = qmp.request("human-monitor-command", {"command-line": f"xp /1wx {address:#x}"})
        text = str(reply)
        match = re.search(r":\s*(0x[0-9a-fA-F]+)", text)
        values[name] = match.group(1) if match else text.strip()
    return values


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def parse_setup(text: str) -> str:
    value = text.replace(" ", "").lower()
    if not re.fullmatch(r"[0-9a-f]{16}", value):
        raise argparse.ArgumentTypeError("SETUP must be exactly 8 bytes of hex")
    return value


def section(console: bytes, tag: str) -> str:
    begin, end = f"\n__USB_STATE_{tag}__".encode(), f"\n__USB_STATE_{tag}_END__".encode()
    start = console.rfind(begin)
    stop = console.find(end, start + 1) if start >= 0 else -1
    if start < 0 or stop < 0:
        return ""
    return console[start + len(begin):stop].decode("utf-8", "replace").replace("\r", "")


def run(args) -> dict:
    report_dir = args.report_dir.resolve()
    if not report_dir.is_relative_to((PROJECT_ROOT / "artifacts").resolve()):
        raise SystemExit("reports must stay under artifacts/")
    report_dir.mkdir(parents=True, exist_ok=False)
    binary = args.qemu_dir / "build/qemu-system-arm"
    report = dict(status="running", tool_sha256=digest(Path(__file__)), qemu_binary=str(binary),
                  qemu_binary_sha256=digest(binary), inputs=str(args.inputs),
                  inputs_manifest_sha256=digest(args.inputs / "boot-manifest.json"),
                  setup=args.setup,
                  plugin=str(args.plugin) if args.plugin else None,
                  plugin_sha256=digest(args.plugin) if args.plugin else None, machine_options=["dwc3-gadget=on", "usb-vbus-test=on", "usb-cid-high=on"],
                  host_lines=[], events=[],
                  captured_hwparams=args.captured_hwparams,
                  limitations=["QEMU model observation; the DWC3 model is a contract model, not hardware.",
                               "The host side sends only the listed lines; nothing is answered for the guest."])
    with tempfile.TemporaryDirectory(prefix="a6400-usbhost-") as temp:
        scratch = Path(temp)
        monitor, host_path, console = scratch / "qmp", scratch / "usbhost", scratch / "console"
        plugin = None
        if args.plugin:   # run.sh splits QEMU_EXTRA on spaces: use a copy at a space-free path
            plugin = scratch / args.plugin.name
            plugin.write_bytes(args.plugin.read_bytes())
        helper = None
        if args.otg_mode_probe:
            helper = scratch / "otg-mode-probe"
            helper.write_bytes(args.otg_mode_probe.read_bytes())
            report["otg_mode_probe_sha256"] = digest(helper)
        env = dict(os.environ, ULOGIO="", TIMEOUT="0", DWC3_GADGET="1", USB_VBUS_TEST="1", USB_CID_HIGH="1",
                   EVSEL_LATE_US="500", A6400_QEMU_DIR=str(args.qemu_dir), A6400_QEMU_DATA_DIR=str(args.inputs),
                   RUN_DIR=str(scratch / "run"),
                   QEMU_EXTRA=(f"-icount shift=0,sleep=on -net none -no-reboot "
                               f"-qmp unix:{monitor},server=on,wait=off "
                               f"-chardev socket,id=usbhost,path={host_path},server=on,wait=off "
                               f"-global cxd90045-dwc3.chardev=usbhost"
                               + (" -global cxd90045-dwc3.captured-hwparams=on"
                                  if args.captured_hwparams else "")
                               + (f" -plugin {plugin}" if plugin else "")))
        if helper:
            env["SETTING_FILES"] = f"{helper}:OTGMODE.ELF"
        started = time.monotonic()
        deadline = started + args.seconds

        def note(kind, **data):
            report["events"].append(dict(t=round(time.monotonic() - started, 3), kind=kind, **data))

        with console.open("wb") as log:
            proc = subprocess.Popen(["bash", str(PROJECT_ROOT / "qemu-a6400/run.sh")], cwd=PROJECT_ROOT,
                                    env=env, stdin=subprocess.PIPE, stdout=log, stderr=subprocess.STDOUT,
                                    start_new_session=True)
            qmp, host = None, None
            rx = b""

            def pump(seconds):
                nonlocal rx
                end = min(deadline, time.monotonic() + seconds)
                while time.monotonic() < end:
                    if proc.poll() is not None:
                        raise RuntimeError(f"QEMU exited: {proc.returncode}")
                    if host is not None:
                        try:
                            chunk = host.recv(65536)
                            if chunk:
                                rx += chunk
                                while b"\n" in rx:
                                    line, rx = rx.split(b"\n", 1)
                                    text = line.decode("ascii", "replace")
                                    report["host_lines"].append(text)
                                    note("host_rx", line=text[:200])
                        except socket.timeout:
                            pass
                    else:
                        time.sleep(0.2)

            def wait_until(condition, timeout, label):
                end = min(deadline, time.monotonic() + timeout)
                while not condition():
                    if time.monotonic() >= end:
                        raise TimeoutError(f"{label} not observed")
                    pump(0.2)

            def guest(command, tag, timeout=40):
                for byte in (command.format(tag=tag) + "\n").encode():
                    proc.stdin.write(bytes([byte]))
                    proc.stdin.flush()
                    time.sleep(0.01)
                wait_until(lambda: f"\n__USB_STATE_{tag}_END__".encode() in console.read_bytes(), timeout, tag)
                report[f"guest_{tag.lower()}"] = section(console.read_bytes(), tag)

            def host_send(line):
                host.sendall((line + "\n").encode("ascii"))
                note("host_tx", line=line)

            try:
                wait_until(monitor.exists, 30, "QMP socket")
                qmp = Qmp(monitor, deadline + 10)
                wait_until(lambda: FIRMWARE_MARKER in console.read_bytes(), 120, "firmware startup marker")
                note("firmware_marker")
                wait_until(lambda: b"/ # " in console.read_bytes(), 30, "guest shell")
                guest(STATE_COMMAND, "BEFORE")
                report["registers_before"] = read_registers(qmp)
                if helper:
                    guest("echo __USB_STATE_MODE__; /setting/OTGMODE.ELF --offline-qemu-only; "
                          "echo helper_exit=$?; "
                          "echo __USB_STATE_MODE_END__", "MODE")
                    report["registers_mode"] = read_registers(qmp)
                host = socket.socket(socket.AF_UNIX)
                host.connect(str(host_path))
                host.settimeout(0.2)
                note("host_connected")
                qmp.request("qom-set", {"path": "/machine", "property": "usb-vbus", "value": True})
                note("vbus", present=True)
                pump(args.settle)
                report["registers_vbus"] = read_registers(qmp)
                host_send("CONN")
                pump(args.settle)
                report["registers_conn"] = read_registers(qmp)
                if args.setup:
                    host_send("SETUP " + args.setup)
                    pump(args.reply_wait)
                guest(STATE_COMMAND, "ATTACHED")
                host_send("DISC")
                qmp.request("qom-set", {"path": "/machine", "property": "usb-vbus", "value": False})
                note("vbus", present=False)
                pump(5)
                guest(STATE_COMMAND, "AFTER")
                report["dwc3_trace"] = [line for line in console.read_bytes().decode("utf-8", "replace").splitlines()
                                        if line.startswith("[DWC3 trace]")]
                report["in_replies"] = [line for line in report["host_lines"] if line.startswith("IN ")]
                report["status"] = "observed"
                qmp.request("quit")
                report["qemu_exit"] = proc.wait(10)
            except Exception as error:  # report every stop with its reason
                report["status"] = "stopped"
                report["stop_reason"] = f"{type(error).__name__}: {error}"
            finally:
                if host:
                    host.close()
                if qmp:
                    qmp.close()
                if proc.poll() is None:
                    proc.terminate()
                    try:
                        proc.wait(10)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                report["owned_exit"] = proc.poll()
                report["elapsed_seconds"] = round(time.monotonic() - started, 1)
                (report_dir / "console.log").write_bytes(console.read_bytes())
                (report_dir / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--qemu-dir", type=Path, required=True)
    parser.add_argument("--inputs", type=Path, required=True)
    parser.add_argument("--report-dir", type=Path, required=True)
    parser.add_argument("--seconds", type=int, default=240)
    parser.add_argument("--settle", type=float, default=8.0, help="seconds after VBUS and after CONN")
    parser.add_argument("--reply-wait", type=float, default=10.0, help="seconds to collect IN lines after SETUP")
    parser.add_argument("--setup", type=parse_setup, default=GET_DEVICE_DESCRIPTOR,
                        help="8-byte SETUP packet in hex, or empty to send none")
    parser.add_argument("--plugin", type=Path, help="optional passive QEMU TCG plugin (for example dwc3_trace.so)")
    parser.add_argument("--otg-mode-probe", type=Path,
                        help="offline-only ARM helper: issue original OTG SET_MODE(2) before attach")
    parser.add_argument("--captured-hwparams", action="store_true",
                        help="use E0595 physical ILCE-6400 DWC3 hardware-parameter registers")
    args = parser.parse_args(argv)
    report = run(args)
    print(json.dumps({k: report.get(k) for k in ("status", "stop_reason", "in_replies", "elapsed_seconds")}, indent=2))
    return 0 if report["status"] == "observed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
