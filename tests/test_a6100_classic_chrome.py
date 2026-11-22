"""Offline tests for the ILCE-6100 Classic Chrome SMOD v2 film simulation mod."""
import struct
import tempfile
import unittest
from pathlib import Path

from mods.film_sims.classic_chrome import make_data as cc


class TestA6100ClassicChrome(unittest.TestCase):
    def test_tone_curve_monotonicity_and_bounds(self):
        stock_ec = [((38 + i * 9) << 6) for i in range(96)]
        chrome_ec = cc.transform_ec_curve(stock_ec)
        self.assertEqual(len(chrome_ec), 96)
        self.assertTrue(all(chrome_ec[i] >= chrome_ec[i - 1] for i in range(1, 96)))
        self.assertTrue(all((v & 0x3F) == 0 and 0 <= (v >> 6) <= 1023 for v in chrome_ec))

        stock_fc = [min(1023, 9 + i * 11) for i in range(96)]
        chrome_fc = cc.transform_fc_curve(stock_fc)
        self.assertEqual(len(chrome_fc), 96)
        self.assertTrue(all(chrome_fc[i] >= chrome_fc[i - 1] for i in range(1, 96)))
        self.assertTrue(all(0 <= v <= 1023 for v in chrome_fc))

    def test_q10_matrix_preserves_exact_row_sums(self):
        for stock in (
            [1241, -205, -12, 62, 1116, -154, 30, -251, 1245],
            [1161, -205, 68, 43, 1134, -153, 16, -236, 1243],
        ):
            out = cc.transform_cc_matrix(stock)
            self.assertEqual(len(out), 9)
            self.assertNotEqual(out, stock)
            for r in (0, 3, 6):
                self.assertEqual(sum(out[r:r + 3]), sum(stock[r:r + 3]))

    def test_target_pages_do_not_cross_4k_boundaries(self):
        for pa, size in (
            (cc.EC_REC28_V1_PA, 384),
            (cc.FC_TBL0_PA, 192),
            (cc.COLOR_A0_PA, 18),
            (cc.COLOR_A1_PA, 18),
        ):
            self.assertTrue(0x035C6000 <= pa and pa + size <= 0x045CC4E8)
            self.assertEqual(pa >> 12, (pa + size - 1) >> 12)


if __name__ == "__main__":
    unittest.main()
