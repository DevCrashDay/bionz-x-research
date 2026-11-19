"""Run an offline QEMU UVC smoke test using the captured firmware and standalone UVC module."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import time

from tools.research.qemu_boot_probe import Qmp
from tools.research.qemu_usb_host_probe import read_registers
from tools.research.qemu_ep0_host import Ep0Host

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'qemu-a6400/tools'))
import moddev


def enumerate_uvc(client: Ep0Host) -> dict:
    device = client.control('8006000100001200')
    if len(device) != 18 or device[:2] != b'\x12\x01':
        raise ValueError('Invalid DEVICE descriptor: ' + device.hex())
    client.control('0005010000000000')
    header = client.control('8006000200000900')
    if len(header) != 9 or header[:2] != b'\x09\x02':
        raise ValueError('Invalid CONFIGURATION header: ' + header.hex())
    total = int.from_bytes(header[2:4], 'little')
    if not 9 <= total <= 1024:
        raise ValueError(f'Configuration total length out of bounds: {total}')
    configuration = client.control(struct.pack('<BBHHH', 0x80, 6, 0x200, 0, total).hex())
    if len(configuration) != total or configuration[:9] != header:
        raise ValueError('Incomplete or inconsistent configuration')
    value = configuration[5]
    if value == 0:
        raise ValueError('Zero configuration value')
    client.control(struct.pack('<BBHHH', 0, 9, value, 0, 0).hex())
    selected = client.control('8008000000000100')
    if selected != bytes([value]):
        raise ValueError('Original GET_CONFIGURATION disagrees with selection')
    return {
        'device_hex': device.hex(),
        'vid_pid': f'{int.from_bytes(device[8:10], "little"):04x}:{int.from_bytes(device[10:12], "little"):04x}',
        'configuration_hex': configuration.hex(),
        'configuration_len': len(configuration),
        'configuration_value': value,
        'get_configuration_hex': selected.hex(),
    }


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('qemu-dir', 'inputs', 'loader', 'mod', 'uvc-ko', 'feeder', 'report-dir'):
        p.add_argument('--' + name, type=Path, required=True)
    p.add_argument('--strict-bulk', action='store_true',
                   help='exercise UVC VS_PROBE/COMMIT and Bulk IN 0x81 frame transfer')
    args = p.parse_args()
    out = args.report_dir.resolve()
    if not out.is_relative_to(ROOT / 'artifacts'):
        raise ValueError('Output must stay under artifacts')
    out.mkdir(parents=True, exist_ok=False)
    report = {
        'status': 'started',
        'physical_camera_access': False,
        'tool_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'inputs': {},
    }
    for name, path in [
        ('qemu', args.qemu_dir / 'build/qemu-system-arm'),
        ('manifest', args.inputs / 'boot-manifest.json'),
        ('mod', args.mod),
        ('uvc_ko', args.uvc_ko),
        ('feeder', args.feeder),
    ]:
        report['inputs'][name] = {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}

    with tempfile.TemporaryDirectory(prefix='qemu-uvc-smoke-') as temp:
        scratch = Path(temp)
        for name in ('mlv2_guard_arm', 'mlv2.req'):
            shutil.copy(args.loader / name, scratch / name)
        monitor = scratch / 'qmp'
        serial = scratch / 'serial'
        hostpath = scratch / 'host'
        env = dict(
            os.environ,
            A6400_QEMU_DIR=str(args.qemu_dir.resolve()),
            A6400_QEMU_DATA_DIR=str(args.inputs.resolve()),
            RUN_DIR=str(scratch / 'run'),
            TIMEOUT='0',
            ULOGIO='',
            EVSEL_LATE_US='500',
            REAR_CONTROLS='1',
            REAR_LCD_DIAGNOSTIC='1',
            DWC3_GADGET='1',
            PL330_DMA='1' if args.strict_bulk else '0',
            USB_VBUS_TEST='1',
            USB_CID_HIGH='1',
            SERIAL=f'unix:{serial},server=on,wait=off',
            SETTING_FILES=moddev.setting_files(scratch / 'mlv2_guard_arm', scratch / 'mlv2.req'),
            QEMU_EXTRA=(
                f'-icount shift=0,sleep=on -net none -no-reboot '
                f'-qmp unix:{monitor},server=on,wait=off '
                f'-chardev socket,id=usbhost,path={hostpath},server=on,wait=off '
                '-global cxd90045-dwc3.chardev=usbhost '
                '-global cxd90045-dwc3.captured-hwparams=on '
                '-global cxd90045-dwc3.captured-start-gate=on '
                '-global cxd90045-dwc3.strict-ep0=on'
                + (' -global cxd90045-dwc3.strict-bulk=on' if args.strict_bulk else '')
            ),
        )
        qmp = None
        started = time.monotonic()
        with (out / 'console.log').open('wb') as log:
            proc = subprocess.Popen(
                ['bash', str(ROOT / 'qemu-a6400/run.sh')],
                env=env, stdout=log, stderr=log, start_new_session=True,
            )
            try:
                for _ in range(300):
                    if monitor.exists():
                        break
                    if proc.poll() is not None:
                        raise RuntimeError('QEMU exited during startup')
                    time.sleep(0.1)
                qmp = Qmp(monitor, time.monotonic() + 300)
                console = moddev.Console(serial, out / 'serial.log', proc)
                dev = moddev.ModDev(console, args.loader, [args.mod])
                dev.wait_menu(timeout=90)
                dev.install()
                qmp.request('qom-set', {'path': '/machine', 'property': 'usb-vbus', 'value': True})
                time.sleep(2)
                console.run('touch /tmp_bt/qemu-usb-service.allow')
                qmp.request('send-key', {'keys': [{'type': 'qcode', 'data': 'm'}], 'hold-time': 180})
                for _ in range(15):
                    time.sleep(1)
                    probe_log = console.run('cat /tmp_bt/usb-service-probe.log 2>/dev/null') or ''
                    if 'gadget_created 00000001' in probe_log:
                        break
                report['stimulus_log'] = probe_log

                # Stage UVC module + device node + feeder into /tmp_bt
                dev.push('/tmp_bt/usbg_video_a6400.ko', args.uvc_ko.read_bytes())
                dev.push('/tmp_bt/uvc_feeder', args.feeder.read_bytes())
                report['mem_debug'] = console.run('free; cat /proc/buddyinfo')
                report['insmod'] = console.run(
                    'insmod /tmp_bt/usbg_video_a6400.ko; echo INSMOD=$?; '
                    'mknod /tmp_bt/video c 248 33; ls -l /tmp_bt/video; '
                    'chmod 755 /tmp_bt/uvc_feeder'
                )
                console.send('/tmp_bt/uvc_feeder &')
                time.sleep(3)
                report['feeder_early'] = console.run('cat /tmp_bt/feeder.log 2>/dev/null')

                with socket.socket(socket.AF_UNIX) as host:
                    host.connect(str(hostpath))
                    host.settimeout(0.2)
                    host.sendall(b'CONN\n')
                    time.sleep(2)
                    client = Ep0Host(host)
                    try:
                        report['enumeration'] = enumerate_uvc(client)
                        report['udc_state_configured'] = console.run('cat /sys/class/udc/dwc3.0/state')
                        if args.strict_bulk:
                            probe = client.control('a181000101001a00')
                            client.control('2101000101001a00', data_out=probe)
                            client.control('2101000201001a00', data_out=probe)
                            frames = []
                            for idx in range(3):
                                pkt = client.bulk_in(3)
                                hdr_len = pkt[0] if pkt else 0
                                payload = pkt[hdr_len:] if hdr_len >= 2 and len(pkt) > hdr_len else b''
                                valid_jpeg = len(payload) > 4 and payload[:2] == b'\xff\xd8' and payload[-2:] == b'\xff\xd9'
                                frames.append({
                                    'index': idx,
                                    'total_bytes': len(pkt),
                                    'header_hex': pkt[:min(hdr_len, 12)].hex(),
                                    'jpeg_bytes': len(payload),
                                    'valid_jpeg': valid_jpeg,
                                    'sha256': hashlib.sha256(payload).hexdigest() if payload else '',
                                })
                                if valid_jpeg and idx == 0:
                                    (out / 'uvc-frame.jpg').write_bytes(payload)
                            report['uvc_stream'] = {
                                'probe_hex': probe.hex(),
                                'frames': frames,
                            }
                    finally:
                        report['host_events'] = client.events
                        report['host_lines'] = [e['rx'] for e in client.events if 'rx' in e]
                    report['attached'] = read_registers(qmp)
                    console.run('touch /tmp_bt/stop_feeder')
                    time.sleep(2)
                    host.sendall(b'DISC\n')
                    qmp.request('qom-set', {'path': '/machine', 'property': 'usb-vbus', 'value': False})

                report['feeder_log'] = console.run('cat /tmp_bt/feeder.log 2>/dev/null')
                report['dmesg_tail'] = console.run('dmesg | grep -v AL_EMMC_SEQ')
                report['udc_state_after'] = console.run('cat /sys/class/udc/dwc3.0/state')
                report['status'] = 'observed'
                qmp.request('quit')
                report['qemu_exit'] = proc.wait(10)
            except Exception as error:
                report.update(status='stopped', stop_reason=f'{type(error).__name__}: {error}')
                try:
                    report['feeder_log'] = console.run('cat /tmp_bt/feeder.log 2>/dev/null')
                    report['dmesg_tail'] = console.run('dmesg | grep -v AL_EMMC_SEQ')
                except Exception:
                    pass
            finally:
                if qmp:
                    qmp.close()
                if proc.poll() is None:
                    proc.terminate()
                    try:
                        proc.wait(10)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait(5)
                report['owned_exit'] = proc.poll()
                report['elapsed_seconds'] = round(time.monotonic() - started, 1)
                (out / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({
        'status': report.get('status'),
        'stop_reason': report.get('stop_reason'),
        'enumeration': report.get('enumeration'),
        'elapsed_seconds': report.get('elapsed_seconds'),
    }, indent=2))
    return 0 if report['status'] == 'observed' else 2


if __name__ == '__main__':
    raise SystemExit(main())
