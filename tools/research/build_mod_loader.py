"""Build reversible RAM-only Mod Loader (.req) payloads for Sony A6400."""

from __future__ import annotations

import hashlib
import json
import struct
from pathlib import Path

from tools.research.pack_mod import extract_elf_text, fnv1a_32

ROOT = Path(__file__).resolve().parents[2]

REQ_MAGIC = 0x4D4C4452  # "MLDR"
PROTO_VER = 1
CODE_ADDR = 0x0412F3D0
CODE_CAP = 3120
ANCHOR_ADDR = 0x1615EC40
ANCHOR_SIZE = 8
HOOK_SET_ROOT = 0x15B91654
HOOK_REFRESH = 0x15B91A80
HOOK_OPEN_ITEM = 0x15B92044  # viewUnified2.so + 0x5ac044 enter_selected(menu, leaf)
RECT_TBL1_SLOT6 = 0x15DE8A04
RECT_TBL2_SLOT6 = 0x15DE8A84


def pack_req(
    op: int,
    code: bytes,
    set_root_entry: int,
    refresh_entry: int,
    open_item_entry: int,
    checksum: int,
) -> bytes:
    hdr = struct.pack(
        "<9I",
        REQ_MAGIC,
        PROTO_VER,
        op,
        CODE_ADDR,
        len(code),
        set_root_entry,
        refresh_entry,
        open_item_entry,
        checksum,
    )
    return hdr + code.ljust(CODE_CAP, b"\x00")


def build_artifacts(elf_path: Path | None = None, out_dir: Path | None = None) -> dict:
    elf_path = elf_path or (ROOT / "artifacts/hardware/mod_loader.elf")
    out_dir = out_dir or (ROOT / "artifacts/hardware")

    code_addr, code, syms = extract_elf_text(elf_path.read_bytes(), expected_code_addr=CODE_ADDR)
    if len(code) > CODE_CAP:
        raise ValueError(f"Payload exceeds /libMWF.so RX padding ({len(code)} > {CODE_CAP})")

    set_root_entry = syms.get("ml_hook_set_root", 0)
    refresh_entry = syms.get("ml_hook_refresh", 0)
    open_item_entry = syms.get("ml_hook_open_item", 0)
    for name, val in (
        ("ml_hook_set_root", set_root_entry),
        ("ml_hook_refresh", refresh_entry),
        ("ml_hook_open_item", open_item_entry),
    ):
        if not (val & 1):
            raise ValueError(f"Invalid Thumb entrypoint {name}={hex(val)}")

    checksum = fnv1a_32(code)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "mod-loader-payload.bin").write_bytes(code)
    for op, name in ((0, "check"), (1, "install"), (2, "remove")):
        (out_dir / f"mod-loader-{name}.req").write_bytes(
            pack_req(op, code, set_root_entry, refresh_entry, open_item_entry, checksum)
        )

    manifest = {
        "version": PROTO_VER,
        "code_address": hex(code_addr),
        "code_bytes": len(code),
        "code_capacity": CODE_CAP,
        "anchor_address": hex(ANCHOR_ADDR),
        "anchor_bytes": ANCHOR_SIZE,
        "set_root_entry": hex(set_root_entry),
        "refresh_entry": hex(refresh_entry),
        "open_item_entry": hex(open_item_entry),
        "hooks": {
            "set_root": hex(HOOK_SET_ROOT),
            "refresh": hex(HOOK_REFRESH),
            "open_item": hex(HOOK_OPEN_ITEM),
        },
        "tab6_rect_slots": [hex(RECT_TBL1_SLOT6), hex(RECT_TBL2_SLOT6)],
        "tab6_rect": [533, 0, 91, 66],
        "fnv1a_32": hex(checksum),
        "sha256": hashlib.sha256(code).hexdigest(),
        "deployed": False,
    }
    (out_dir / "mod-loader-build.json").write_text(json.dumps(manifest, indent=2))
    return manifest


if __name__ == "__main__":
    print(json.dumps(build_artifacts(), indent=2))
