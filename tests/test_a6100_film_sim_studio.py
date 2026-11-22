"""Unit tests for the ILCE-6100 Film Sim Studio core engine and SMOD v2 builder."""
import struct
import tempfile
import unittest
from pathlib import Path

try:
    import numpy as np
except ImportError:
    np = None

from mods.film_sims import studio_core as sc


class TestFilmSimStudioCore(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.stock = sc.load_stock_tables()

    def test_all_presets_monotone_and_row_sum_invariant(self):
        if np is None:
            self.skipTest("numpy required for matrix tests")
        for name, p in sc.PRESETS.items():
            ec, fc = sc.compute_curves(self.stock, p)
            self.assertEqual(len(ec), 96, name)
            self.assertEqual(len(fc), 96, name)
            self.assertTrue(all(ec[i] >= ec[i - 1] for i in range(1, 96)), name)
            self.assertTrue(all(fc[i] >= fc[i - 1] for i in range(1, 96)), name)
            self.assertTrue(all(0 <= v <= 1023 for v in fc), name)
            self.assertTrue(all((v & 0x3F) == 0 and 0 <= (v >> 6) <= 1023 for v in ec), name)

            a0 = sc.compute_q10_matrix(self.stock["a0_stock"], p)
            a1 = sc.compute_q10_matrix(self.stock["a1_stock"], p)
            for r in (0, 3, 6):
                self.assertEqual(sum(a0[r:r + 3]), sum(self.stock["a0_stock"][r:r + 3]), name)
                self.assertEqual(sum(a1[r:r + 3]), sum(self.stock["a1_stock"][r:r + 3]), name)

    def test_stock_reset_is_exact_identity(self):
        if np is None:
            self.skipTest("numpy required for image simulation test")
        p = sc.PRESETS["Stock (Reset)"]
        ec, fc = sc.compute_curves(self.stock, p)
        self.assertEqual(ec, self.stock["ec_stock"])
        self.assertEqual(fc, self.stock["fc_stock"])
        self.assertEqual(sc.compute_q10_matrix(self.stock["a1_stock"], p), self.stock["a1_stock"])

        rng = np.random.default_rng(42)
        img = rng.integers(0, 256, size=(64, 64, 3), dtype=np.uint8)
        out = sc.simulate_image(img, self.stock, p)
        self.assertTrue(np.array_equal(img, out))

    def test_build_mod_produces_valid_smod_v2(self):
        if np is None:
            self.skipTest("numpy required for matrix build")
        with tempfile.TemporaryDirectory() as tmp:
            res = sc.build_mod(sc.PRESETS["Classic Neg"], out_dir=Path(tmp))
            mod = Path(res["local_path"]).read_bytes()
            magic, ver, flags, name, code_size, _fnv, _bss, on_off, off_off, hooks = struct.unpack_from("<III32s6I", mod)
            self.assertEqual(magic, 0x444F4D53)
            self.assertEqual(ver, 2)
            self.assertEqual(flags, 16)
            self.assertEqual(name.rstrip(b"\0"), b"Classic Neg")
            self.assertEqual(hooks, 0)
            self.assertEqual(len(mod), 68 + code_size)
            self.assertEqual(on_off, off_off)
            self.assertGreater(on_off, 0)
            lut = sc.build_3dlut_1215(sc.PRESETS["Classic Neg"])
            self.assertEqual(len(lut), 1215)

    def test_emit_live_picker_header(self):
        if np is None:
            self.skipTest("numpy required for 3D LUT header generation")
        with tempfile.TemporaryDirectory() as tmp:
            out = sc.emit_live_picker_header(Path(tmp) / "film_sim_live_data.h", stock=self.stock)
            text = out.read_text(encoding="utf-8")
            self.assertIn("#define LIVE_N_PRESETS 7u", text)
            self.assertIn("const char LIVE_NAMES[7][16]", text)
            self.assertIn("const uint8_t LIVE_REFADR[2][1215]", text)


if __name__ == "__main__":
    unittest.main()
