"""Build Film Micro-Texture /usr/sabin/ RAM tables for the ILCE-6100 (Still + Movie):
1. Symmetric ~3% micro-texture dcv_psf (removes harsh digital edge haloing).
2. Zeroed chroma-edge peaking (spica c_tap = 0.0) & gentle luma roll-off (y_tap = 0.15).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import struct
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
STOCK_DIR = ROOT / "artifacts/a6100-pp/sabin_stock"


def build_symmetric_dcv(src_bytes: bytes, header_size: int = 0xEC, target_gain: float = 0.12) -> tuple[bytes, dict]:
    out = bytearray(src_bytes)
    n_i16 = (len(src_bytes) - header_size) // 2
    vals = np.array(struct.unpack_from(f"<{n_i16}h", src_bytes, header_size), dtype=np.int32)
    n_blk = n_i16 // 48
    mod = vals.copy()
    for b in range(n_blk):
        s = b * 48
        blk = vals[s:s + 48]
        if np.any(blk):
            scaled = np.rint(blk * target_gain).astype(np.int32)
            c = int(np.argmax(np.abs(scaled)))
            scaled[c] -= int(scaled.sum())
            mod[s:s + 48] = np.clip(scaled, -32768, 32767)
    rem_start = n_blk * 48
    if rem_start < n_i16:
        mod[rem_start:] = np.clip(np.rint(vals[rem_start:] * target_gain), -32768, 32767).astype(np.int32)
    struct.pack_into(f"<{n_i16}h", out, header_size, *[int(x) for x in mod])
    return bytes(out), {"size": len(out), "target_gain": target_gain}


def build_spica_15bit(src_bytes: bytes, gain: float) -> tuple[bytes, dict]:
    raw = np.frombuffer(src_bytes, dtype="<u2")
    signed = np.where(raw >= 16384, raw.astype(np.int32) - 32768, raw.astype(np.int32))
    scaled = np.clip(np.rint(signed * gain), -16384, 16383).astype(np.int32)
    encoded = np.where(scaled < 0, scaled + 32768, scaled).astype("<u2")
    return encoded.tobytes(), {"size": len(encoded) * 2, "gain": gain}


def build_preset(out_dir: Path, dcv_gain: float, y_gain: float, c_gain: float = 0.0) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest: dict = {"dcv_gain": dcv_gain, "y_gain": y_gain, "c_gain": c_gain, "tables": []}

    for fname in ("dcv_psf_table_still.bin", "dcv_psf_table_movie_bayer.bin", "dcv_psf_table_movie_4k.bin"):
        src = (STOCK_DIR / fname).read_bytes()
        mod, stats = build_symmetric_dcv(src, header_size=0xEC, target_gain=dcv_gain)
        (out_dir / fname).write_bytes(mod)
        manifest["tables"].append({"name": fname, "sha256": hashlib.sha256(mod).hexdigest(), **stats})

    for fname, gain in (
        ("spica_musashi_still_c_tap_step1.bin", c_gain),
        ("spica_musashi_still_c_tap_step2.bin", c_gain),
        ("spica_musashi_still_y_tap_step1.bin", y_gain),
        ("spica_musashi_still_y_tap_step2.bin", y_gain),
        ("spica_musashi_mov_4k_c_tap_step1.bin", c_gain),
        ("spica_musashi_mov_4k_c_tap_step2.bin", c_gain),
        ("spica_musashi_mov_4k_y_tap_step1.bin", y_gain),
        ("spica_musashi_mov_4k_y_tap_step2.bin", y_gain),
    ):
        src = (STOCK_DIR / fname).read_bytes()
        mod, stats = build_spica_15bit(src, gain=gain)
        (out_dir / fname).write_bytes(mod)
        manifest["tables"].append({"name": fname, "sha256": hashlib.sha256(mod).hexdigest(), **stats})

    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"Built {len(manifest['tables'])} sabin tables (dcv={dcv_gain}, y={y_gain}) in {out_dir}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=("soft", "grain", "both"), default="both")
    args = ap.parse_args()
    if args.mode in ("soft", "both"):
        build_preset(ROOT / "artifacts/a6100-film-sims/sabin_film", dcv_gain=0.12, y_gain=0.15)
    if args.mode in ("grain", "both"):
        build_preset(ROOT / "artifacts/a6100-film-sims/sabin_grain", dcv_gain=1.35, y_gain=1.35)


if __name__ == "__main__":
    main()
