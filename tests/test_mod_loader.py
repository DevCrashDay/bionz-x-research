"""Unit tests for the SMOD .mod packer and Mod Loader .req builder."""

from __future__ import annotations

import struct
import tempfile
import unittest
from pathlib import Path

from tools.research import build_mod_loader, pack_mod


class ModLoaderTests(unittest.TestCase):
    def test_pack_smod_roundtrip(self) -> None:
        code = b"\x00\xbf\x00\xbf" * 4
        hooks = [
            (0x06309D48, b"\x01" * 12, pack_mod.make_thumb_trampoline(0x0700D709)),
        ]
        blob = pack_mod.pack_smod(
            name="Test Mod",
            code_addr=0x0700D708,
            code=code,
            hooks=hooks,
            state_addr=0x07143920,
            state_size=64,
        )
        hdr = struct.unpack_from(pack_mod.SMOD_HEADER_FMT, blob, 0)
        self.assertEqual(hdr[0], pack_mod.SMOD_MAGIC)
        self.assertEqual(hdr[1], pack_mod.SMOD_VERSION)
        self.assertEqual(hdr[3].rstrip(b"\x00"), b"Test Mod")
        self.assertEqual(hdr[4], 0x0700D708)
        self.assertEqual(hdr[5], len(code))
        self.assertEqual(hdr[6], 0x07143920)
        self.assertEqual(hdr[7], 64)
        self.assertEqual(hdr[8], 1)
        self.assertEqual(hdr[9], pack_mod.fnv1a_32(code))

        hk = struct.unpack_from(pack_mod.SMOD_HOOK_FMT, blob, pack_mod.SMOD_HEADER_SIZE)
        self.assertEqual(hk[0], 0x06309D48)
        self.assertEqual(hk[1], 12)
        self.assertEqual(hk[2][:12], b"\x01" * 12)
        self.assertEqual(hk[3][:12], pack_mod.make_thumb_trampoline(0x0700D709))

        payload_off = pack_mod.SMOD_HEADER_SIZE + pack_mod.SMOD_HOOK_SIZE
        self.assertEqual(blob[payload_off:], code)

    def test_pack_smod_state_init(self) -> None:
        code = b"\x00\xbf" * 2
        init = b"\x01" * 8
        blob = pack_mod.pack_smod(
            "S", 0x0700D708, code, [], state_addr=0x07143920, state_size=8, state_init=init
        )
        hdr = struct.unpack_from(pack_mod.SMOD_HEADER_FMT, blob, 0)
        self.assertEqual(hdr[2], pack_mod.SMOD_FLAG_STATE_INIT)
        self.assertEqual(blob[-8:], init)
        with self.assertRaises(ValueError):
            pack_mod.pack_smod(
                "S", 0x0700D708, code, [], state_addr=0x07143920, state_size=8, state_init=init[:4]
            )

    def test_build_menu_touch_full_mod_from_elf(self) -> None:
        elf = pack_mod.ROOT / "artifacts/hardware/menu_touch_full.elf"
        if not elf.exists():
            self.skipTest("menu_touch_full.elf not present")
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "MenuTouchFull.mod"
            info = pack_mod.build_menu_touch_full_mod(elf, out)
            blob = out.read_bytes()
            self.assertEqual(info["hooks"], 2)
            self.assertEqual(info["code_addr"], "0x700d708")
            self.assertEqual(
                len(blob),
                pack_mod.SMOD_HEADER_SIZE + 2 * pack_mod.SMOD_HOOK_SIZE + info["code_bytes"] + 632,
            )
            hdr = struct.unpack_from(pack_mod.SMOD_HEADER_FMT, blob, 0)
            self.assertEqual(hdr[2] & pack_mod.SMOD_FLAG_STATE_INIT, pack_mod.SMOD_FLAG_STATE_INIT)
            self.assertEqual(blob[-632:][:24], pack_mod.MTF2_STATE_HEADER)
            _, orig, _ = pack_mod.extract_elf_text(elf.read_bytes(), expected_code_addr=0x0700D708)
            code = blob[-632 - len(orig):-632]
            diff = [i for i in range(len(orig)) if orig[i] != code[i]]
            self.assertEqual(diff, [pack_mod.MTF2_MAX_TABS_OFF + 4])  # cmp r2, #5 -> #6
            self.assertEqual(hdr[9], pack_mod.fnv1a_32(code))

    def test_patch_mtf2_max_tabs_rejects_unknown_payload(self) -> None:
        with self.assertRaises(ValueError):
            pack_mod.patch_mtf2_max_tabs(bytes(2128))

    def test_build_mod_loader_artifacts(self) -> None:
        elf = build_mod_loader.ROOT / "artifacts/hardware/mod_loader.elf"
        if not elf.exists():
            self.skipTest("mod_loader.elf not present")
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp)
            manifest = build_mod_loader.build_artifacts(elf, out_dir)
            self.assertLessEqual(manifest["code_bytes"], build_mod_loader.CODE_CAP)
            self.assertEqual(manifest["code_address"], "0x412f3d0")
            for op, name in ((0, "check"), (1, "install"), (2, "remove")):
                req = (out_dir / f"mod-loader-{name}.req").read_bytes()
                self.assertEqual(len(req), 36 + build_mod_loader.CODE_CAP)
                hdr = struct.unpack_from("<9I", req, 0)
                self.assertEqual(hdr[0], build_mod_loader.REQ_MAGIC)
                self.assertEqual(hdr[1], build_mod_loader.PROTO_VER)
                self.assertEqual(hdr[2], op)
                self.assertEqual(hdr[3], build_mod_loader.CODE_ADDR)
                self.assertEqual(hdr[4], manifest["code_bytes"])


if __name__ == "__main__":
    unittest.main()
