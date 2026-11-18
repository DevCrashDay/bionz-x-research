"""Opt-in QEMU sony-a6400 CamPig/PQD_Ydet/RC0 live-view pipeline tests."""

import os
from pathlib import Path
import select
import subprocess
import tempfile
import time
import unittest

from project_paths import PROJECT_ROOT
from tests.test_qemu_emmc import NATIVE_AVAILABLE, QEMU


CAMPIG0 = 0xF3000000
YDET_STAT = 0xF3015700
YDET_WIN = 0xF3015848
SGC1 = 0xF3120000
GPIO_P0 = 0xF2910010
RC0_AIF = 0xF3881000
DRAM_BUF = 0x14E90000


@unittest.skipUnless(NATIVE_AVAILABLE, 'Optional WSL/Linux sony-a6400 QEMU build absent')
class QemuLiveViewPipelineTests(unittest.TestCase):
    def start_qtest(self, machine='sony-a6400'):
        directory = PROJECT_ROOT / 'artifacts/tests'
        directory.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=directory)
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        log = (root / 'qemu.log').open('wb')
        self.addCleanup(log.close)
        proc = subprocess.Popen(
            [str(QEMU), '-M', machine, '-accel', 'qtest', '-display', 'none',
             '-monitor', 'none', '-serial', 'none', '-net', 'none',
             '-qtest', 'stdio', '-qtest-log', str(root / 'qtest.log')],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log, bufsize=0)
        self.addCleanup(self._stop_proc, proc)
        return proc, root

    def _stop_proc(self, proc):
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
                self.fail('Owned QEMU process required a forced kill')
        proc.stdin.close()
        proc.stdout.close()

    def qtest(self, proc, pending, text):
        proc.stdin.write((text + '\n').encode('ascii'))
        deadline = time.monotonic() + 5
        while b'\n' not in pending[0]:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select([proc.stdout], [], [], remaining)[0]:
                self.fail(f'qtest timed out: {text}')
            chunk = os.read(proc.stdout.fileno(), 4096)
            if not chunk:
                self.fail(f'qtest exited before replying: {text}')
            pending[0] += chunk
        line, pending[0] = pending[0].split(b'\n', 1)
        self.assertTrue(line.startswith(b'OK'), f'{text}: {line!r}')
        fields = line.split()
        return int(fields[1], 0) if len(fields) > 1 else None

    def test_default_machine_keeps_liveview_blocks_unmodelled(self):
        proc, _ = self.start_qtest('sony-a6400')
        pending = [b'']
        self.assertEqual(self.qtest(proc, pending, f'readl {GPIO_P0:#x}') & 4, 0)
        self.qtest(proc, pending, f'writel {YDET_WIN:#x} 14')
        self.qtest(proc, pending, f'writel {YDET_WIN + 4:#x} 1600')
        self.qtest(proc, pending, f'writel {YDET_WIN + 8:#x} 2')
        self.qtest(proc, pending, f'writel {YDET_WIN + 12:#x} 570')
        self.qtest(proc, pending, f'writel {CAMPIG0 + 0x50:#x} 0x0c000000')
        self.qtest(proc, pending, f'writel {CAMPIG0 + 0x10:#x} 0x0c')
        self.qtest(proc, pending, 'clock_step 40000000')
        self.assertEqual(self.qtest(proc, pending, f'readl {CAMPIG0 + 0x48:#x}'), 0)
        self.assertEqual(self.qtest(proc, pending, f'readl {YDET_STAT:#x}'), 0)
        self.assertEqual(self.qtest(proc, pending, f'readl {RC0_AIF + 0x08:#x}'), 0)

    def test_campig_ydet_sgc1_and_rc0_dma_contracts(self):
        proc, _ = self.start_qtest('sony-a6400,liveview-source=bars,rear-controls=on')
        pending = [b'']
        self.assertEqual(self.qtest(proc, pending, f'readl {GPIO_P0:#x}') & 4, 4)

        # Sgc1 stopped latch returns 0x3fff3fff; continuous mode orders Event A before B.
        self.assertEqual(self.qtest(proc, pending, f'readl {SGC1 + 0x218:#x}'), 0x3FFF3FFF)
        self.qtest(proc, pending, f'writel {SGC1 + 0x50:#x} 1')
        self.qtest(proc, pending, f'writel {SGC1 + 0x54:#x} 1')
        self.qtest(proc, pending, f'writel {SGC1 + 0x220:#x} 0x03470595')
        self.qtest(proc, pending, f'writel {SGC1 + 0x224:#x} 0x2e3')
        self.qtest(proc, pending, f'writel {SGC1 + 0x210:#x} 0x101')
        self.qtest(proc, pending, f'writel {SGC1 + 0x214:#x} 1')
        self.assertEqual(self.qtest(proc, pending, f'readl {SGC1 + 0x214:#x}'), 0)
        self.assertEqual(self.qtest(proc, pending, f'readl {SGC1 + 0x218:#x}'), 0x00800080)

        self.qtest(proc, pending, 'clock_step 29354722')
        self.assertEqual(self.qtest(proc, pending, f'readl {SGC1 + 0x48:#x}'), 0)
        self.qtest(proc, pending, 'clock_step 1')
        self.assertEqual(self.qtest(proc, pending, f'readl {SGC1 + 0x48:#x}'), 1)

        # Advance past frame end while Event A is unacked: Event B stays held until A is acked.
        self.qtest(proc, pending, 'clock_step 4012000')
        self.assertEqual(self.qtest(proc, pending, f'readl {SGC1 + 0x4c:#x}'), 0)
        self.qtest(proc, pending, f'writel {SGC1 + 0x48:#x} 1')
        self.assertEqual(self.qtest(proc, pending, f'readl {SGC1 + 0x48:#x}'), 0)
        self.assertEqual(self.qtest(proc, pending, f'readl {SGC1 + 0x4c:#x}'), 1)

        # Sgc1::Stop (0x6376a814) clears +0x200 and polls (+0x210 & 1) == 0.
        self.qtest(proc, pending, f'writel {SGC1 + 0x200:#x} 0')
        self.assertEqual(self.qtest(proc, pending, f'readl {SGC1 + 0x210:#x}') & 1, 0)
        self.assertEqual(self.qtest(proc, pending, f'readl {SGC1 + 0x218:#x}'), 0x3FFF3FFF)

        # Program PQD_Ydet window (14, 1600, 2, 570) and arm CamPig paths 2 & 3.
        self.qtest(proc, pending, f'writel {YDET_WIN:#x} 14')
        self.qtest(proc, pending, f'writel {YDET_WIN + 4:#x} 1600')
        self.qtest(proc, pending, f'writel {YDET_WIN + 8:#x} 2')
        self.qtest(proc, pending, f'writel {YDET_WIN + 12:#x} 570')
        self.qtest(proc, pending, f'writel {CAMPIG0 + 0x50:#x} 0x0c000000')
        self.qtest(proc, pending, f'writel {CAMPIG0 + 0x10:#x} 0x0c')
        self.qtest(proc, pending, 'clock_step 33366667')
        self.assertEqual(self.qtest(proc, pending, f'readl {CAMPIG0 + 0x48:#x}'), 0)
        self.assertEqual(self.qtest(proc, pending, f'readl {YDET_STAT:#x}'), 0)

        # A compare interrupt is not a completed input raster.
        self.qtest(proc, pending, f'writel {SGC1 + 0x210:#x} 1')
        self.qtest(proc, pending, 'clock_step 33366666')
        self.assertEqual(self.qtest(proc, pending, f'readl {SGC1 + 0x48:#x}'), 1)
        self.assertEqual(self.qtest(proc, pending, f'readl {CAMPIG0 + 0x48:#x}'), 0)
        self.assertEqual(self.qtest(proc, pending, f'readl {YDET_STAT:#x}'), 0)
        self.qtest(proc, pending, 'clock_step 1')
        self.assertEqual(self.qtest(proc, pending, f'readl {CAMPIG0 + 0x48:#x}'), 0x0C000000)
        row0 = [self.qtest(proc, pending, f'readl {YDET_STAT + 4 * i:#x}') for i in range(8)]
        self.assertTrue(all(v > 0 for v in row0))
        self.assertEqual(row0, sorted(row0, reverse=True))
        self.assertGreater(row0[0], row0[-1] * 8)

        # An armed writer alone must not modify RAM or post completion.
        self.qtest(proc, pending, f'writel {DRAM_BUF + 0x400:#x} 0xdeadbeef')
        self.qtest(proc, pending, f'writel {RC0_AIF + 0x3a0:#x} {DRAM_BUF:#x}')
        self.qtest(proc, pending, f'writel {RC0_AIF + 0x3a4:#x} 0x400')
        self.qtest(proc, pending, f'writel {RC0_AIF + 0x3a8:#x} 0x80')
        self.qtest(proc, pending, f'writel {RC0_AIF + 0x3ac:#x} 3')
        self.qtest(proc, pending, f'writel {RC0_AIF + 0x380:#x} 0x2')
        self.qtest(proc, pending, f'writel {RC0_AIF + 0x220:#x} 0xff010000')
        self.qtest(proc, pending, f'writel {RC0_AIF + 0x200:#x} 0x10802')
        self.qtest(proc, pending, f'writel {RC0_AIF + 0x08:#x} 0xf00f')
        self.qtest(proc, pending, f'writel {RC0_AIF + 0x380:#x} 0x3')
        self.qtest(proc, pending, 'clock_step 2000000')
        self.assertEqual(self.qtest(proc, pending, f'readl {RC0_AIF:#x}'), 0)
        self.assertEqual(self.qtest(proc, pending, f'readb {DRAM_BUF:#x}'), 0)

        # RC0 AIF ch0 (IIB reader) completes the pair, asynchronously.
        self.qtest(proc, pending, f'writel {RC0_AIF + 0x200:#x} 0x10803')
        self.assertEqual(self.qtest(proc, pending, f'readl {RC0_AIF:#x}'), 0)
        self.assertEqual(self.qtest(proc, pending, f'readl {RC0_AIF + 0x380:#x}') & 1, 1)
        self.assertEqual(self.qtest(proc, pending, f'readl {RC0_AIF + 0x384:#x}'), 0x80000000)

        self.qtest(proc, pending, 'clock_step 2000000')
        self.assertEqual(self.qtest(proc, pending, f'readl {RC0_AIF:#x}'), 0x1001)
        self.assertEqual(self.qtest(proc, pending, f'readl {RC0_AIF + 0x380:#x}') & 1, 0)
        self.assertEqual(self.qtest(proc, pending, f'readl {RC0_AIF + 0x384:#x}'), 0)
        self.assertEqual(self.qtest(proc, pending, f'readb {DRAM_BUF:#x}'), 235)
        self.assertEqual(self.qtest(proc, pending, f'readb {DRAM_BUF + 0x3ff:#x}'), 16)
        # Pitch skip region [0x400..0x47f] must remain untouched; line 1 starts at +0x480.
        self.assertEqual(self.qtest(proc, pending, f'readl {DRAM_BUF + 0x400:#x}'), 0xDEADBEEF)
        self.assertEqual(self.qtest(proc, pending, f'readb {DRAM_BUF + 0x480:#x}'), 235)

    def test_pgm_source_loads_external_image_and_rejects_bad_header(self):
        directory = PROJECT_ROOT / 'artifacts/tests'
        directory.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=directory) as tmp:
            root = Path(tmp)
            bad = root / 'bad.pgm'
            bad.write_bytes(b'NOT_A_PGM')
            bad_run = subprocess.run(
                [str(QEMU), '-M', f'sony-a6400,liveview-source=pgm:{bad}',
                 '-accel', 'qtest', '-display', 'none', '-qtest', 'stdio'],
                stdin=subprocess.DEVNULL, capture_output=True, timeout=5)
            self.assertNotEqual(bad_run.returncode, 0)
            self.assertIn(b'not a binary P5 PGM', bad_run.stderr)

            pgm = root / 'scene.pgm'
            row_vals = bytes([20, 60, 100, 140, 180, 200, 220, 250])
            pgm.write_bytes(b'P5\n# deterministic test chart\n8 5\n255\n' + row_vals * 5)
            proc, _ = self.start_qtest(f'sony-a6400,liveview-source=pgm:{pgm}')
            pending = [b'']
            self.qtest(proc, pending, f'writel {YDET_WIN:#x} 0')
            self.qtest(proc, pending, f'writel {YDET_WIN + 4:#x} 1628')
            self.qtest(proc, pending, f'writel {YDET_WIN + 8:#x} 0')
            self.qtest(proc, pending, f'writel {YDET_WIN + 12:#x} 574')
            self.qtest(proc, pending, f'writel {CAMPIG0 + 0x10:#x} 0x0c')
            self.qtest(proc, pending, f'writel {SGC1 + 0x220:#x} 0x03470595')
            self.qtest(proc, pending, f'writel {SGC1 + 0x210:#x} 1')
            self.qtest(proc, pending, 'clock_step 33366667')
            zones = [self.qtest(proc, pending, f'readl {YDET_STAT + 4 * i:#x}') for i in range(8)]
            self.assertEqual(zones, sorted(zones))
            self.assertGreater(zones[-1], zones[0] * 10)
