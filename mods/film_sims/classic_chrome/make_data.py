"""Emit hash-checked gamma and color matrix data for a6100 Classic Chrome v2 (offline).

Usage: python3 mods/film_sims/classic_chrome/make_data.py OUT_HEADER

Targets in ILCE-6100 Ver.1.00 av-cam LIRO RAM:
  - EC_GAMMA_PA (0x03e553b0): EC Record 28 variant 1 (96 x uint32_t curve words; 9 exponent words untouched)
  - FC_GAMMA_PA (0x03e8f1d4): FC Table 0 (96 x uint16_t 10-bit curve halfwords)
  - COLOR_A0_PA (0x0444a254): CC Matrix entry A0 (index 0, 9 x int16_t Q10)
  - COLOR_A1_PA (0x0444a266): CC Matrix entry A1 (index 1, 9 x int16_t Q10, active in stock PP=Off)
"""
from __future__ import annotations

import hashlib
import json
import math
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
AVCAM_PATH = ROOT / "artifacts/a6100-inputs/av-cam.bin"
LIVE_JSON = ROOT / "artifacts/a6100-pp/active-tables-live.json"
AVCAM_SHA256 = "58b2118db858f3eb1594ae6cef3f628b7fa3cc6db9173b094565535d48e6ff9e"

BASE_VA = 0x635C6000
EC_REC28_V1_PA = 0x03E553B0
FC_TBL0_PA = 0x03E8F1D4
COLOR_A0_PA = 0x0444A254
COLOR_A1_PA = 0x0444A266


def mode0_10bit(v: int) -> int:
    s = v >> 6
    if s <= 1022 and (v & 0x20):
        s += 1
    return s


def classic_chrome_tone_v1(x_norm: float) -> float:
    """Legacy v1 curve kept so ON/OFF recognizes tables written by v1."""
    if x_norm <= 0.0:
        return 0.0
    if x_norm >= 1.0:
        return 0.985
    toe = math.pow(x_norm, 1.18)
    shoulder = 1.0 - math.exp(-3.15 * toe)
    norm_s = shoulder / (1.0 - math.exp(-3.15))
    return 0.985 * (0.35 * toe + 0.65 * norm_s)


def classic_chrome_tone(x_norm: float) -> float:
    """Fujifilm Classic Chrome v2 S-curve on normalized [0, 1] post-gamma domain:
    - Pulls shadow toe down (x=0.25 -> 0.215, ~ -0.22 EV) for documentary shadow density
    - Anchors mid-gray exposure-neutral (x=0.50 -> 0.488)
    - Increases midtone slope (dy/dx = 1.17x across x in 0.28..0.75) to boost local micro-contrast & detail
    - Smoothly rolls off only the top highlight shoulder (x > 0.78 -> 0.992)
    """
    if x_norm <= 0.0:
        return 0.0
    if x_norm >= 1.0:
        return 0.992
    a = 0.028
    b = 0.008
    y = x_norm - a * math.sin(2.0 * math.pi * x_norm) - b * math.sin(math.pi * x_norm)
    return 0.992 * max(0.0, min(1.0, y))


def transform_ec_curve(stock_words: list[int], tone_fn=classic_chrome_tone) -> list[int]:
    """Transform 96 EC Record 28 v1 words (16-bit curve values shifted by 6)."""
    stock_10 = [mode0_10bit(w & 0xFFFF) for w in stock_words]
    lo, hi = stock_10[0], stock_10[-1]
    span = float(max(1, hi - lo))
    out = []
    prev = 0
    for w, c10 in zip(stock_words, stock_10):
        t = (c10 - lo) / span
        y_t = tone_fn(t)
        if tone_fn is classic_chrome_tone_v1:
            new_c10 = int(round((lo - 4) + y_t * (hi - lo + 12)))
        else:
            new_c10 = int(round(lo + y_t * (hi - lo)))
        new_c10 = max(prev, min(1023, max(0, new_c10)))
        prev = new_c10
        out.append(new_c10 << 6)
    return out


def transform_fc_curve(stock_halfwords: list[int], tone_fn=classic_chrome_tone) -> list[int]:
    """Transform 96 FC Table 0 uint16 halfwords (10-bit 0..1023)."""
    lo, hi = stock_halfwords[0], stock_halfwords[-1]
    span = float(max(1, hi - lo))
    out = []
    prev = 0
    for h in stock_halfwords:
        t = (h - lo) / span
        y_t = tone_fn(t)
        new_h = int(round(lo + y_t * (hi - lo)))
        new_h = max(prev, min(1023, max(0, new_h)))
        prev = new_h
        out.append(new_h)
    return out


M_CHROME_V1 = (
    (0.94, 0.09, -0.03),
    (0.05, 0.91, 0.04),
    (-0.02, 0.11, 0.91),
)

M_CHROME_V2 = (
    (0.91, 0.13, -0.04),
    (0.06, 0.89, 0.05),
    (-0.03, 0.14, 0.89),
)


def transform_cc_matrix(stock9: list[int], m_chrome=M_CHROME_V2) -> list[int]:
    """Apply Fujifilm Classic Chrome primary transform M_chrome * M_stock in Q10,
    preserving each row's exact stock sum so neutral white balance is invariant.
    """
    s = [
        [stock9[0], stock9[1], stock9[2]],
        [stock9[3], stock9[4], stock9[5]],
        [stock9[6], stock9[7], stock9[8]],
    ]
    out9: list[int] = []
    for r in range(3):
        target_sum = sum(s[r])
        row = []
        for c in range(3):
            val = sum(m_chrome[r][k] * s[k][c] for k in range(3))
            row.append(int(round(val)))
        err = sum(row) - target_sum
        row[r] -= err
        out9.extend(row)
    return out9


def fmt(vals: list[int], as_hex: bool = False) -> str:
    return ",".join(hex(v) if as_hex else str(v) for v in vals)


def main() -> None:
    av61 = AVCAM_PATH.read_bytes()
    actual_sha = hashlib.sha256(av61).hexdigest()
    assert actual_sha == AVCAM_SHA256, f"av-cam.bin hash mismatch: {actual_sha}"

    ec_off = EC_REC28_V1_PA - 0x035C6000
    fc_off = FC_TBL0_PA - 0x035C6000
    ec_stock = list(struct.unpack_from("<96I", av61, ec_off))
    fc_stock = list(struct.unpack_from("<96H", av61, fc_off))

    live = json.loads(LIVE_JSON.read_text())
    a0_stock = live["matrix_table"]["A0_idx0"]["coeffs"]
    a1_stock = live["matrix_table"]["A1_idx1"]["coeffs"]
    assert a0_stock == [1241, -205, -12, 62, 1116, -154, 30, -251, 1245]
    assert a1_stock == [1161, -205, 68, 43, 1134, -153, 16, -236, 1243]

    ec_chrome = transform_ec_curve(ec_stock, classic_chrome_tone)
    fc_chrome = transform_fc_curve(fc_stock, classic_chrome_tone)
    a0_chrome = transform_cc_matrix(a0_stock, M_CHROME_V2)
    a1_chrome = transform_cc_matrix(a1_stock, M_CHROME_V2)

    ec_legacy = transform_ec_curve(ec_stock, classic_chrome_tone_v1)
    fc_legacy = transform_fc_curve(fc_stock, classic_chrome_tone_v1)
    a0_legacy = transform_cc_matrix(a0_stock, M_CHROME_V1)
    a1_legacy = transform_cc_matrix(a1_stock, M_CHROME_V1)

    assert len(ec_chrome) == 96 and all(ec_chrome[i] >= ec_chrome[i - 1] for i in range(1, 96))
    assert len(fc_chrome) == 96 and all(fc_chrome[i] >= fc_chrome[i - 1] for i in range(1, 96))
    # Verify shadow toe is pulled down at knot 31 (stock 407) and midtone span (knots 31..60) is expanded vs stock
    assert fc_chrome[31] < fc_stock[31], f"shadow toe knot 31 not pulled down: {fc_chrome[31]} vs {fc_stock[31]}"
    assert (fc_chrome[60] - fc_chrome[31]) > (fc_stock[60] - fc_stock[31]), "midtone contrast span must exceed stock"
    for r in (0, 3, 6):
        assert sum(a0_chrome[r:r + 3]) == sum(a0_stock[r:r + 3])
        assert sum(a1_chrome[r:r + 3]) == sum(a1_stock[r:r + 3])

    hdr = (
        "/* Auto-generated by mods/film_sims/classic_chrome/make_data.py */\n"
        "#include <stdint.h>\n\n"
        f"#define EC_GAMMA_PA 0x{EC_REC28_V1_PA:08x}u\n"
        f"#define FC_GAMMA_PA 0x{FC_TBL0_PA:08x}u\n"
        f"#define COLOR_A0_PA 0x{COLOR_A0_PA:08x}u\n"
        f"#define COLOR_A1_PA 0x{COLOR_A1_PA:08x}u\n\n"
        f"static const uint32_t EC_GAMMA_STOCK[96] = {{{fmt(ec_stock, True)}}};\n"
        f"static const uint32_t EC_GAMMA_CHROME[96] = {{{fmt(ec_chrome, True)}}};\n"
        f"static const uint32_t EC_GAMMA_LEGACY[96] = {{{fmt(ec_legacy, True)}}};\n"
        f"static const uint16_t FC_GAMMA_STOCK[96] = {{{fmt(fc_stock, False)}}};\n"
        f"static const uint16_t FC_GAMMA_CHROME[96] = {{{fmt(fc_chrome, False)}}};\n"
        f"static const uint16_t FC_GAMMA_LEGACY[96] = {{{fmt(fc_legacy, False)}}};\n"
        f"static const int16_t COLOR_A0_STOCK[9] = {{{fmt(a0_stock, False)}}};\n"
        f"static const int16_t COLOR_A0_CHROME[9] = {{{fmt(a0_chrome, False)}}};\n"
        f"static const int16_t COLOR_A0_LEGACY[9] = {{{fmt(a0_legacy, False)}}};\n"
        f"static const int16_t COLOR_A1_STOCK[9] = {{{fmt(a1_stock, False)}}};\n"
        f"static const int16_t COLOR_A1_CHROME[9] = {{{fmt(a1_chrome, False)}}};\n"
        f"static const int16_t COLOR_A1_LEGACY[9] = {{{fmt(a1_legacy, False)}}};\n"
    )
    Path(sys.argv[1]).write_text(hdr, encoding="utf-8")


if __name__ == "__main__":
    main()
