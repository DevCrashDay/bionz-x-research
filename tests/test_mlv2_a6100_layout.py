"""The v2 loader's view hooks, shim window and PLT constants, retargeted to the
ILCE-6100 V1.00 build."""

import hashlib
import unittest
from pathlib import Path

from tools.native_camera.mlv2 import a6100_layout as layout

ROOT = Path(__file__).resolve().parents[1]
IMAGE = ROOT / 'artifacts/a6100-pp/extract/a6100-lib/viewUnified2.so'
TESTCMD = ROOT / 'artifacts/a6100-pp/extract/a6100-lib/libtestcmd.so'


class TestA6100Layout(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not IMAGE.is_file():
            raise unittest.SkipTest('a6100 viewUnified2.so is not present in this checkout')
        cls.image = IMAGE.read_bytes()

    def test_three_view_hooks_resolve(self):
        hooks = layout.verify(self.image)
        self.assertEqual(hooks['set_root']['offset'], 0x5acc0c)
        self.assertEqual(hooks['refresh']['offset'], 0x5ad038)
        self.assertEqual(hooks['open_item']['offset'], 0x5ad5fc)
        for name in ('set_root', 'refresh', 'open_item'):
            self.assertEqual(hooks[name]['trap_after'], hooks[name]['replay'])

    def test_plt_constants_resolve_to_stubs(self):
        hooks = layout.verify(self.image)
        self.assertEqual(hooks['SET_TEXT']['offset'], 0x150030)
        self.assertEqual(hooks['SET_MODE']['offset'], 0x1563c8)
        self.assertEqual(hooks['SET_TEXT']['symbol'],
                         '_ZN9GEN_Label13setTextDirectEPKc')
        self.assertEqual(hooks['SET_MODE']['symbol'],
                         '_ZN2ux6wgtsys6Widget7setModeENS1_10WidgetModeE')

    def test_originals_match_the_a6400_trampoline_source(self):
        text = (ROOT / 'tools/native_camera/mlv2/mlv2_trampoline.S').read_text()
        for off in ('0x5ab654', '0x5aba80', '0x5ac044'):
            self.assertIn(off, text)
        hooks = layout.verify(self.image)
        for name in ('set_root', 'refresh', 'open_item'):
            self.assertEqual(self.image.count(hooks[name]['signal']), 1, name)

    def test_unknown_build_is_refused(self):
        with self.assertRaises(layout.LayoutMismatch):
            layout.verify(self.image, expect_sha256='0' * 64)

    def test_moved_site_is_refused(self):
        data = bytearray(self.image)
        off = layout.HOOKS['set_root']['a6100']
        data[off:off + 2] = b'\x00\x00'
        with self.assertRaises(layout.LayoutMismatch):
            layout.verify(bytes(data), expect_sha256=None)

    def test_a6400_offsets_are_not_silently_used(self):
        for row in layout.HOOKS.values():
            self.assertNotEqual(row['a6100'], row['a6400'])
        for row in layout.CONSTANTS.values():
            self.assertNotEqual(row['a6100'], row['a6400'])


class TestCave(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not TESTCMD.is_file():
            raise unittest.SkipTest('a6100 libtestcmd.so is not present in this checkout')
        cls.lib = TESTCMD.read_bytes()

    def test_shim_window_is_past_eof_and_fits_the_page(self):
        cave = layout.verify_cave(self.lib)
        self.assertEqual(cave['start'], 0x2800)
        self.assertEqual(cave['size'], 2048)
        self.assertEqual(cave['page_end'], 0x3000)
        self.assertGreater(cave['start'], len(self.lib))

    def test_wrong_libtestcmd_is_refused(self):
        mutated = self.lib[:8] + b'\xff' + self.lib[9:]
        with self.assertRaises(layout.LayoutMismatch):
            layout.verify_cave(mutated)

    def test_cave_hash_matches_the_module_list(self):
        self.assertEqual(hashlib.sha256(self.lib).hexdigest(), layout.TESTCMD_SHA256)


if __name__ == '__main__':
    unittest.main()
