"""Stage FilmTexture.mod and /tmp_bt/mods/sabin/* on the connected ILCE-6100 over USB,
and read live CC_OUT LutMode (0x043cc38c) for 5x9x9 3D LUT readiness.
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "artifacts/a6100-pp"))
sys.path.insert(0, str(ROOT))

from a6100_usb_dbg import DirectScsiExchange
from tools.camera_debugger import Debugger, OP_EXEC, OP_INFO

SABIN_DIR = ROOT / "artifacts/a6100-film-sims/sabin_film"
MOD_PATH = ROOT / "artifacts/a6100-film-sims/film_texture/FilmTexture.mod"
SABIN_FILES = (
    "dcv_psf_table_still.bin",
    "dcv_psf_table_movie_bayer.bin",
    "dcv_psf_table_movie_4k.bin",
    "spica_musashi_still_y_tap_step1.bin",
    "spica_musashi_still_y_tap_step2.bin",
    "spica_musashi_mov_4k_y_tap_step1.bin",
    "spica_musashi_mov_4k_y_tap_step2.bin",
)


def connect() -> tuple[Debugger, str]:
    for drive in (r"\\.\F:", r"\\.\E:", r"\\.\G:", r"\\.\PHYSICALDRIVE3", r"\\.\PHYSICALDRIVE4"):
        try:
            dbg = Debugger(DirectScsiExchange(drive, timeout=4.0))
            dbg.request(OP_INFO)
            return dbg, drive
        except Exception:
            continue
    raise RuntimeError("ILCE-6100 not found on USB Mass Storage")


def main() -> None:
    dbg, drive = connect()
    print(f"[+] Connected on {drive}")

    # 1. Read CC_OUT 3D LUT state (PA 0x043cc38c)
    cc = dbg.read("/dev/mem", 0x043CC38C, 0x80)
    lut_mode = cc[4]
    lut_en = cc[0xC]
    lut_type = struct.unpack_from("<H", cc, 0x68)[0]
    conv_sd = cc[0x69]
    print(f"[+] CC_OUT LutMode={lut_mode:#x} en={lut_en} lut_type={lut_type} ConvSD={conv_sd}")

    # 2. Stage /tmp_bt/mods/sabin/* and FilmTexture.mod
    dbg.request(OP_EXEC, b"mkdir -p /tmp_bt/mods/sabin")
    for fname in SABIN_FILES:
        data = (SABIN_DIR / fname).read_bytes()
        dbg.push(f"/tmp_bt/mods/sabin/{fname}", data)
        print(f"  pushed /tmp_bt/mods/sabin/{fname} ({len(data)} B)")

    res = dbg.push("/tmp_bt/mods/FilmTexture.mod", MOD_PATH.read_bytes())
    print(f"[+] Staged /tmp_bt/mods/FilmTexture.mod ({res['bytes']} B, sha256={res['sha256'][:12]}...)")


if __name__ == "__main__":
    main()
