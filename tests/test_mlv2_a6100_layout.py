"""The v2 loader's view hooks, retargeted to the ILCE-6100 V1.00 build."""

import unittest
from pathlib import Path

from tools.native_camera.mlv2 import a6100_layout as layout

ROOT = Path(__file__).resolve().parents[1]
IMAGE = ROOT / 'artifacts/a6100-pp/extract/a6100-lib/viewUnified2.so'


class TestA6100Layout(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not IMAGE.is_file():
            raise unittest.SkipTest('a6100 viewUnified2.so is not present in this checkout')
        cls.image = IMAGE.read_bytes()

    def test_three_view_hooks_resolve(self):
        hooks = layout.verify(self.image)
        self.assertEqual(sorted(hooks), ['open_item', 'refresh', 'set_root'])
        self.assertEqual(hooks['set_root']['offset'], 0x5acc0c)
        self.assertEqual(hooks['refresh']['offset'], 0x5ad038)
        self.assertEqual(hooks['open_item']['offset'], 0x5ad5fc)
        # the patch point is the replay length: the trampoline runs the original
        # instructions itself and returns past them
        for hook in hooks.values():
            self.assertEqual(hook['trap_after'], hook['replay'])

    def test_originals_match_the_a6400_trampoline_source(self):
        # If the a6100 bytes drifted from the a6400 replay, the trampolines
        # would execute the wrong instructions and the port must not proceed.
        text = (ROOT / 'tools/native_camera/mlv2/mlv2_trampoline.S').read_text()
        self.assertIn('0x5ab654', text)
        self.assertIn('0x5aba80', text)
        self.assertIn('0x5ac044', text)
        hooks = layout.verify(self.image)
        for name, hook in hooks.items():
            self.assertEqual(self.image.count(hook['signal']), 1, name)

    def test_unknown_build_is_refused(self):
        with self.assertRaises(layout.LayoutMismatch):
            layout.verify(self.image, expect_sha256='0' * 64)

    def test_moved_site_is_refused(self):
        data = bytearray(self.image)
        row = layout.HOOKS['set_root']
        off = row['a6100']
        data[off:off + 2] = b'\x00\x00'
        with self.assertRaises(layout.LayoutMismatch):
            layout.verify(bytes(data), expect_sha256=None)

    def test_a6400_offsets_are_not_silently_used(self):
        for row in layout.HOOKS.values():
            self.assertNotEqual(row['a6100'], row['a6400'])


if __name__ == '__main__':
    unittest.main()
