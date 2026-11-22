"""Unit tests for the ILCE-6100 Film Micro-Texture sabin tables and FilmTexture.mod."""
import struct
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SABIN_FILM = ROOT / "artifacts/a6100-film-sims/sabin_film"
SABIN_STOCK = ROOT / "artifacts/a6100-pp/sabin_stock"
MOD_PATH = ROOT / "artifacts/a6100-film-sims/film_texture/FilmTexture.mod"


class TestFilmTexture(unittest.TestCase):
    def test_sabin_tables_present_and_same_size_as_stock(self):
        expected = (
            "dcv_psf_table_still.bin",
            "dcv_psf_table_movie_bayer.bin",
            "dcv_psf_table_movie_4k.bin",
            "spica_musashi_still_c_tap_step1.bin",
            "spica_musashi_still_c_tap_step2.bin",
            "spica_musashi_still_y_tap_step1.bin",
            "spica_musashi_still_y_tap_step2.bin",
            "spica_musashi_mov_4k_c_tap_step1.bin",
            "spica_musashi_mov_4k_c_tap_step2.bin",
            "spica_musashi_mov_4k_y_tap_step1.bin",
            "spica_musashi_mov_4k_y_tap_step2.bin",
        )
        for name in expected:
            mod_b = (SABIN_FILM / name).read_bytes()
            stk_b = (SABIN_STOCK / name).read_bytes()
            self.assertEqual(len(mod_b), len(stk_b), name)
            if name.startswith("dcv_psf_"):
                self.assertEqual(mod_b[:0xEC], stk_b[:0xEC], f"{name} header must match stock")
                n_i16 = (len(mod_b) - 0xEC) // 2
                vals = struct.unpack_from(f"<{n_i16}h", mod_b, 0xEC)
                for b in range(n_i16 // 48):
                    self.assertEqual(sum(vals[b * 48:(b + 1) * 48]), 0, f"{name} block {b} DC sum != 0")
            elif "_c_tap_" in name:
                self.assertEqual(mod_b, b"\x00" * len(stk_b), name)

    def test_film_texture_mod_is_valid_smod_v2(self):
        mod = MOD_PATH.read_bytes()
        magic, ver, flags, name, code_size, _fnv, _bss, on_off, off_off, hooks = struct.unpack_from("<III32s6I", mod)
        self.assertEqual(magic, 0x444F4D53)
        self.assertEqual(ver, 2)
        self.assertEqual(flags, 0)
        self.assertEqual(name.rstrip(b"\0"), b"Film Texture")
        self.assertEqual(hooks, 0)
        self.assertEqual(len(mod), 68 + code_size)
        self.assertEqual(on_off, off_off)
        self.assertGreater(on_off, 0)


if __name__ == "__main__":
    unittest.main()
