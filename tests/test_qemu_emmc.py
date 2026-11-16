"""Optional native QEMU eMMC regressions using constructed disks and paused CPUs."""
import os
from pathlib import Path
import select
import struct
import subprocess
import tempfile
import time
import unittest

from project_paths import PROJECT_ROOT

QEMU = Path(os.environ.get('A6400_QEMU_DIR', str(Path.home() / 'sony-a6400-qemu'))) / 'build/qemu-system-arm'
SDHCI = 0xF0304000


@unittest.skipUnless(os.name == 'posix' and QEMU.is_file(), 'Optional WSL/Linux sony-a6400 QEMU build absent')
class QemuEmmcAddressingTests(unittest.TestCase):
    def setUp(self):
        directory = PROJECT_ROOT / 'artifacts/tests'
        directory.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=directory)
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        image = root / 'emmc.img'
        self.marker = (b'Original sector contents, not a synthesized controller response.\n' * 9)[:512]
        self.marker = self.marker.ljust(512, b'\0')
        with image.open('xb') as stream:
            stream.truncate(16 * 1024 ** 2)
            stream.seek(8 * 1024 ** 2 + 1024)
            stream.write(self.marker)
        self.log = (root / 'qemu.log').open('wb')
        self.addCleanup(self.log.close)
        self.process = subprocess.Popen(
            [str(QEMU), '-M', 'sony-a6400', '-accel', 'qtest', '-S', '-display', 'none',
             '-monitor', 'none', '-serial', 'none', '-net', 'none',
             '-drive', f'if=sd,format=raw,file={image}',
             '-qtest', 'stdio', '-qtest-log', str(root / 'qtest.log')],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.log, bufsize=0)
        self.addCleanup(self.stop)
        self.pending = b''
        self.request(f'writeb {SDHCI + 0x29:#x} 0xf')
        self.request(f'writew {SDHCI + 0x2c:#x} 0x7')
        self.request(f'writew {SDHCI + 0x34:#x} 0xffff')
        self.request(f'writew {SDHCI + 0x36:#x} 0xffff')

    def stop(self):
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
                self.fail('Owned QEMU process required a forced kill')
        self.process.stdin.close()
        self.process.stdout.close()

    def request(self, text):
        self.process.stdin.write((text + '\n').encode('ascii'))
        deadline = time.monotonic() + 5
        while b'\n' not in self.pending:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select([self.process.stdout], [], [], remaining)[0]:
                self.fail(f'qtest timed out: {text}')
            chunk = os.read(self.process.stdout.fileno(), 4096)
            if not chunk:
                self.fail(f'qtest exited before replying: {text}')
            self.pending += chunk
        line, self.pending = self.pending.split(b'\n', 1)
        self.assertTrue(line.startswith(b'OK'), f'{text}: {line!r}')
        fields = line.split()
        return int(fields[1], 0) if len(fields) > 1 else None

    def command(self, index, argument, flags, *, read=False):
        self.request(f'writew {SDHCI + 0x30:#x} 0xffff')
        self.request(f'writew {SDHCI + 0x32:#x} 0xffff')
        self.request(f'writew {SDHCI + 0x04:#x} {512 if read else 0}')
        self.request(f'writew {SDHCI + 0x06:#x} {1 if read else 0}')
        self.request(f'writel {SDHCI + 0x08:#x} {argument:#x}')
        self.request(f'writew {SDHCI + 0x0c:#x} {0x10 if read else 0}')
        self.request(f'writew {SDHCI + 0x0e:#x} {(index << 8) | flags:#x}')
        for _ in range(100):
            status = self.request(f'readw {SDHCI + 0x30:#x}')
            self.assertFalse(status & 0x8000, f'SDHCI command {index} error {status:#x}')
            if status & 1:
                break
            self.request('clock_step 100000')
        else:
            self.fail(f'SDHCI command {index} did not complete')
        return self.request(f'readl {SDHCI + 0x10:#x}')

    def initialize_card(self, sector_addressed):
        self.command(0, 0, 0)
        ocr = self.command(1, 0x40000080 if sector_addressed else 0x80, 2)
        self.assertTrue(ocr & 0x80000000, 'eMMC did not power up')
        self.command(2, 0, 9)
        self.command(3, 0x10000, 0x1A)
        self.command(7, 0x10000, 0x1B)
        self.request('clock_step 1000000')
        return ocr

    def read_marker(self, address):
        self.command(17, address, 0x3A, read=True)
        data = bytearray()
        for _ in range(128):
            data.extend(struct.pack('<I', self.request(f'readl {SDHCI + 0x20:#x}')))
        return bytes(data)

    def test_cmd1_sector_mode_reads_the_requested_512_byte_sector(self):
        ocr = self.initialize_card(True)
        self.assertTrue(ocr & 0x40000000, 'Requested eMMC sector mode was not acknowledged')
        self.assertEqual(self.read_marker(2), self.marker)

    def test_cmd1_byte_mode_keeps_byte_addressed_reads(self):
        ocr = self.initialize_card(False)
        self.assertFalse(ocr & 0x40000000)
        self.assertEqual(self.read_marker(1024), self.marker)


if __name__ == '__main__':
    unittest.main()
