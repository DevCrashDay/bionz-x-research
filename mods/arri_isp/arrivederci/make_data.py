"""Emit hash-checked gamma and color data for Arrivederci (offline)."""
import hashlib
import json
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
assert all(sum(color_arri[i:i + 3]) == 1024 for i in (0, 3, 6))


def fmt(values, as_hex):
    return ",".join(hex(v) if as_hex else str(v) for v in values)


Path(sys.argv[1]).write_text(
    "#include <stdint.h>\n"
    f"static const uint32_t GAMMA_STOCK[96] = {{{fmt(words['record31-stock.bin'][:96], True)}}};\n"
    f"static const uint32_t GAMMA_ARRI[96] = {{{fmt(words['record31-arri-classic-rec709.bin'][:96], True)}}};\n"
    f"static const int16_t COLOR_STOCK[9] = {{{fmt(color_stock, False)}}};\n"
    f"static const int16_t COLOR_ARRI[9] = {{{fmt(color_arri, False)}}};\n")
