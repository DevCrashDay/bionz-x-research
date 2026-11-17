"""Pack a position-independent ARM Thumb ELF (linked at 0) into an SMOD v2 (.mod) file.

SMOD v2 mods need no code cave: Mod Loader v2 allocates memory for them, applies hook
templates with the load address, and calls the optional on()/off() lifecycle entries.

Example:
  python -m tools.research.pack_mod_v2 --elf mod.elf --name "My Mod" --on my_on --off my_off \
      --hook 0x064b54b6:f8b500af0d460446fff7e2ff:my_hook --out MyMod.mod
"""
from __future__ import annotations

import argparse
import hashlib
import json
import struct
from pathlib import Path

from tools.research.pack_mod import extract_elf_text, fnv1a_32

SMOD_MAGIC = 0x444F4D53
NO_FIXUP = 0xFF


def thumb_abs_template(target: int, entry_off: int, length: int) -> tuple[bytes, int]:
    """Absolute Thumb jump into the mod: ldr.w pc, [pc, #k]; .word (base + entry_off).

    Returns (template, fixup_offset). 4-aligned targets use 12 bytes, 2-mod-4 targets 10+2."""
    if target % 4 == 0:
        tmpl = b"\xdf\xf8\x04\xf0\x00\xbf\x00\xbf" + struct.pack("<I", entry_off)   # ldr.w pc,[pc,#4]; nop; nop
        fix = 8
    elif target % 4 == 2:
        tmpl = b"\x00\xbf\xdf\xf8\x00\xf0" + struct.pack("<I", entry_off)           # nop; ldr.w pc,[pc,#0]
        fix = 6
    else:
        raise ValueError("hook target must be halfword aligned")
    if length < len(tmpl):
        raise ValueError(f"hook at {target:#x} needs {len(tmpl)} original bytes, got {length}")
    return tmpl.ljust(length, b"\x00"), fix   # bytes after the jump never execute


def pack_smod_v2(name, code, on_off=0, off_off=0, bss_size=0, hooks=(), flags=0):
    if not code or len(code) % 4:
        raise ValueError("code must be non-empty and 4-byte aligned")
    for off in (on_off, off_off):
        if off and (not off & 1 or off >= len(code)):
            raise ValueError("lifecycle offsets must be Thumb entries inside the code")
    hdr = struct.pack("<III32s6I", SMOD_MAGIC, 2, flags, name.encode("ascii")[:31],
                      len(code), fnv1a_32(code), bss_size, on_off, off_off, len(hooks))
    blob = bytearray(hdr)
    for target, orig, tmpl, fix in hooks:
        if not 1 <= len(orig) <= 16 or len(tmpl) != len(orig):
            raise ValueError(f"invalid hook at {target:#x}")
        blob += struct.pack("<IBB2x16s16s", target, len(orig), fix, orig.ljust(16, b"\0"), tmpl.ljust(16, b"\0"))
    return bytes(blob) + code


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--elf", type=Path, required=True)
    ap.add_argument("--name", required=True)
    ap.add_argument("--on", help="symbol called after ON (receives 1)")
    ap.add_argument("--off", help="symbol called before OFF (receives 0)")
    ap.add_argument("--bss", type=int, default=0, help="zero-initialized bytes after the code")
    ap.add_argument("--hook", action="append", default=[], help="TARGET:ORIGHEX:SYMBOL (absolute Thumb jump)")
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    _, code, syms = extract_elf_text(a.elf.read_bytes(), expected_code_addr=0)

    def sym(n):
        if n not in syms or not syms[n] & 1:
            raise SystemExit(f"symbol {n} missing or not Thumb")
        return syms[n]

    hooks = []
    for spec in a.hook:
        t, orig_hex, s = spec.split(":")
        target, orig = int(t, 0), bytes.fromhex(orig_hex)
        tmpl, fix = thumb_abs_template(target, sym(s), len(orig))
        hooks.append((target, orig, tmpl, fix))
    blob = pack_smod_v2(a.name, code, sym(a.on) if a.on else 0, sym(a.off) if a.off else 0, a.bss, hooks)
    a.out.write_bytes(blob)
    print(json.dumps({"name": a.name, "file": str(a.out), "code_bytes": len(code), "hooks": len(hooks),
                      "size": len(blob), "sha256": hashlib.sha256(blob).hexdigest()}, indent=2))


if __name__ == "__main__":
    main()
