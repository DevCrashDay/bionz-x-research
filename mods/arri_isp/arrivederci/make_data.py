"""Emit hash-checked gamma and color data for Arrivederci (offline).

Usage: make_data.py OUT_HEADER [MATRIX_FIT_JSON]
Without a fit, COLOR_ARRI is the camera-tested Rec.709 primary matrix (2026-11-16). With the
output of fit_matrix.py, COLOR_ARRI is its "classic_fit" candidate and the earlier matrix is
emitted as COLOR_LEGACY, so ON and OFF also recognize a B8 that still holds the old values.
"""
import hashlib
import json
import math
import struct
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[3]
g = root / "artifacts/analysis/arri-gamma-test-block-2026-11-16"
SHA = {"record31-stock.bin": "bc1d67e362549d6fda06c48e86fff4648cb099819e38d6e16032b0be99ceed58",
       "record31-arri-classic-rec709.bin": "93d69d065c0547abdf680bc104a80780360111b2e312163e99a74f34659de744"}
words = {}
for name, digest in SHA.items():
    blob = (g / name).read_bytes()
    assert hashlib.sha256(blob).hexdigest() == digest, name
    words[name] = struct.unpack("<105I", blob)
c = json.loads((root / "artifacts/analysis/arri-movie-color-2026-11-16/arri-matrix-candidates.json").read_text())
live = json.loads((root / "artifacts/analysis/arri-movie-color-2026-11-16/colormode-matrix-table.json").read_text())
color_stock, color_arri = sum(c["B8_stock"], []), sum(c["candidates"]["rec709"]["q10"], [])
assert color_stock == live["live"]["B8"], "stock must equal the live table entry B8"
color_legacy = None
if len(sys.argv) > 2:
    fit = json.loads(Path(sys.argv[2]).read_text())
    assert fit["candidates"]["primaries_current"]["q10"] == c["candidates"]["rec709"]["q10"]
    color_legacy, color_arri = color_arri, sum(fit["candidates"]["classic_fit"]["q10"], [])
    assert color_arri not in (color_stock, color_legacy)
assert all(sum(color_arri[i:i + 3]) == 1024 for i in (0, 3, 6))
assert all(-32768 <= v <= 32767 for v in color_arri)


def _filmic_highlight_shoulder(base_curve: tuple[int, ...]) -> list[int]:
    """Extend upper highlights (knots 64..95) with a C1-smooth roll-off to 0xf780."""
    out = list(base_curve[:64])
    y63 = out[63]
    span = 0xF780 - y63
    k = 2.65
    denom = 1.0 - math.exp(-k)
    for i in range(64, 96):
        t = (i - 63) / 32.0
        v = y63 + span * (1.0 - math.exp(-k * t)) / denom
        out.append(int(round(v / 64.0)) * 64)
    return out


gamma_legacy = list(words["record31-arri-classic-rec709.bin"][:96])
gamma_arri = _filmic_highlight_shoulder(words["record31-arri-classic-rec709.bin"][:96])
assert len(gamma_arri) == 96 and all(gamma_arri[i] >= gamma_arri[i - 1] for i in range(1, 96))


def fmt(values, as_hex):
    return ",".join(hex(v) if as_hex else str(v) for v in values)


def _c_bytes(name: str, data: bytes) -> str:
    hex_bytes = ",".join(f"0x{b:02x}" for b in data)
    return f"static const uint8_t {name}[{len(data)}] = {{{hex_bytes}}};\n"


sabin_dir = root / "artifacts/analysis/arri-v4-sabin"
sabin_hdr = ""
sabin_files = {
    "SABIN_DCV_BAYER": "dcv_psf_table_movie_bayer.bin",
    "SABIN_DCV_4K": "dcv_psf_table_movie_4k.bin",
    "SABIN_Y_TAP_1": "spica_musashi_mov_4k_y_tap_step1.bin",
    "SABIN_Y_TAP_2": "spica_musashi_mov_4k_y_tap_step2.bin",
}
if (sabin_dir / "dcv_psf_table_movie_bayer.bin").exists():
    sabin_hdr = "#define SABIN_EMBEDDED 1\n" + "".join(
        _c_bytes(sym, (sabin_dir / fname).read_bytes()) for sym, fname in sabin_files.items()
    )

legacy = ("#define GAMMA_HAS_LEGACY 1\n"
          f"static const uint32_t GAMMA_LEGACY[96] = {{{fmt(gamma_legacy, True)}}};\n")
if color_legacy:
    legacy += ("#define COLOR_HAS_LEGACY 1\n"
               f"static const int16_t COLOR_LEGACY[9] = {{{fmt(color_legacy, False)}}};\n")
Path(sys.argv[1]).write_text(
    "#include <stdint.h>\n"
    f"static const uint32_t GAMMA_STOCK[96] = {{{fmt(words['record31-stock.bin'][:96], True)}}};\n"
    f"static const uint32_t GAMMA_ARRI[96] = {{{fmt(gamma_arri, True)}}};\n"
    f"static const int16_t COLOR_STOCK[9] = {{{fmt(color_stock, False)}}};\n"
    f"static const int16_t COLOR_ARRI[9] = {{{fmt(color_arri, False)}}};\n"
    + legacy + sabin_hdr)
