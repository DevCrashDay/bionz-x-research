"""QEMU handover guard (E0638): ARMv7 cache/MMU teardown checks before a kexec-style jump.

Builds qemu-a6400/tools/handover_guard.c (TCG plugin) and the bare-metal fixture
qemu-a6400/tools/handover_payload.S in variants that each drop one teardown step,
runs them on the sony-a6400 machine, and checks that exactly the expected guard
items fail.  Needs the optional WSL/Linux QEMU build and arm-linux-gnueabi-gcc.
"""

import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

from project_paths import PROJECT_ROOT
from tests.test_qemu_emmc import NATIVE_AVAILABLE, QEMU

TOOLS = PROJECT_ROOT / 'qemu-a6400/tools'
CROSS = shutil.which('arm-linux-gnueabi-gcc')
NEW_ENTRY = 0x00600000
DMA_CONTRACTS = TOOLS / 'handover_dma_contracts.txt'

# variant -> guard items expected to FAIL (empty = PASS)
VARIANTS = {
    'GOOD': set(),
    'KEEP_MMU': {'G1'},
    'KEEP_DCACHE': {'G2', 'G4'},
    'KEEP_ICACHE': {'G3'},
    'NO_CLEAN': {'G4', 'G5'},
    'NO_ICINV': {'G5'},
    'NO_DSB': {'G6'},
    'NO_ISB': {'G7'},
    'IRQ_ON': {'G8'},
    'ROGUE_CPU1': {'G9'},
}


@unittest.skipUnless(NATIVE_AVAILABLE and CROSS and shutil.which('pkg-config'),
                     'Optional WSL/Linux QEMU build or ARM cross compiler absent')
class QemuHandoverGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        directory = PROJECT_ROOT / 'artifacts/tests'
        directory.mkdir(parents=True, exist_ok=True)
        cls._tmp = tempfile.TemporaryDirectory(dir=directory)
        cls.root = Path(cls._tmp.name)
        include = QEMU.parent.parent / 'include/plugins'
        glib = subprocess.run(['pkg-config', '--cflags', 'glib-2.0'], check=True,
                              capture_output=True, text=True).stdout.split()
        cls.plugin = cls.root / 'handover_guard.so'
        subprocess.run(['gcc', '-std=gnu11', '-Wall', '-Wextra', '-Werror',
                        '-Wno-unused-parameter', '-fPIC', '-shared', *glib,
                        '-I', str(include), str(TOOLS / 'handover_guard.c'),
                        '-o', str(cls.plugin)], check=True)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def build_payload(self, variant):
        elf = self.root / f'{variant}.elf'
        binary = self.root / f'{variant}.bin'
        defsym = [f'-Wa,--defsym,{name}=1' for name in variant.split('+') if name != 'GOOD']
        subprocess.run([CROSS, '-mcpu=cortex-a5', '-marm', '-nostdlib', '-static',
                        '-Wl,-Ttext=0x108000', '-Wl,--build-id=none', *defsym,
                        str(TOOLS / 'handover_payload.S'), '-o', str(elf)], check=True)
        subprocess.run([CROSS.replace('gcc', 'objcopy'), '-O', 'binary',
                        str(elf), str(binary)], check=True)
        return binary

    def run_guard(self, variant, machine='sony-a6400', dma=False):
        binary = self.build_payload(variant)
        log = self.root / f'{variant}.log'
        plugin = f'{self.plugin},entry={NEW_ENTRY:#x},exit=1'
        if dma:
            plugin += f',dma={DMA_CONTRACTS}'
        proc = subprocess.run(
            [str(QEMU), '-M', machine, '-m', '1G', '-kernel', str(binary),
             '-display', 'none', '-serial', 'none', '-monitor', 'none', '-net', 'none',
             '-plugin', plugin, '-d', 'plugin', '-D', str(log)],
            stdin=subprocess.DEVNULL, capture_output=True, timeout=120)
        text = log.read_text(errors='replace') if log.exists() else ''
        return proc.returncode, text

    def check(self, variant, expected=None, **kwargs):
        code, text = self.run_guard(variant, **kwargs)
        if expected is None:
            expected = VARIANTS[variant]
        self.assertIn('entry 0x00600000 reached on cpu0', text, text[-2000:])
        failed = set(re.findall(r'handover-guard: (G\d+) FAIL', text))
        self.assertEqual(failed, expected, text[-3000:])
        self.assertEqual(code, 3 if expected else 0, text[-2000:])
        return text

    def test_good_mainline_sequence_passes(self):
        text = self.check('GOOD')
        self.assertIn('SCTLR=gdb sysreg', text)
        self.assertIn('DCCISW after C-off=1024', text)
        self.assertIn('RESULT PASS', text)

    def test_dma_contracts(self):
        dwc3 = 'sony-a6400,dwc3-gadget=on'
        cases = {
            # variant: (machine, expected failing items, expected G10 line fragment)
            'GOOD': ('sony-a6400', set(), 'G10 ok   dwc3-run: never programmed'),
            'DWC3': (dwc3, set(), 'G10 ok   dwc3-run: stopped, idle confirmed'),
            'DWC3+DWC3_KEEP_RUNNING': (dwc3, {'G10'}, 'G10 BAD  dwc3-run: STILL RUNNING'),
            'DWC3+DWC3_NO_POLL': (dwc3, {'G10'},
                                  'G10 BAD  dwc3-run: stop NOT confirmed by an idle status read'),
            'DWC3_SOFTRESET': (dwc3, set(),
                               'G10 ok   dwc3-run: never started (stores never set the run bits)'),
            'WORK_DMA': ('sony-a6400', {'G10'},
                         'G10 BAD  work-dma-chx: UNVERIFIED (no stop contract)'),
            'WDMA': ('sony-a6400', set(), 'G10 ok   work-dma-ch6: stopped, idle confirmed'),
            'WDMA+WDMA_NO_POLL': ('sony-a6400', {'G10'},
                                  'G10 BAD  work-dma-ch6: stop NOT confirmed'),
            'WDMA+WDMA_KEEP_ENABLED': ('sony-a6400', {'G10'},
                                       'G10 BAD  work-dma-en: STILL RUNNING'),
        }
        for variant, (machine, expected, fragment) in cases.items():
            with self.subTest(variant=variant):
                text = self.check(variant, expected, machine=machine, dma=True)
                self.assertIn(fragment, text)
                self.assertIn('contracts from', text)

    def test_without_contract_file_g10_is_reported_unconfigured(self):
        text = self.check('GOOD')
        self.assertIn('G10 not configured', text)
        self.assertIn('DMA quiescence', text)

    def test_each_missing_step_is_caught(self):
        for variant in VARIANTS:
            if variant == 'GOOD':
                continue
            with self.subTest(variant=variant):
                self.check(variant)


if __name__ == '__main__':
    unittest.main()
