"""Offline Unicorn test of the SMOD v2 Arrivederci build with a fitted matrix (no camera).

Usage: python mods/arri_isp/arrivederci/test_mod_v2_unicorn.py BUILD_DIR

Loads Arrivederci.mod the way Mod Loader v2 does (relocatable code at an arbitrary base) and calls
its lifecycle entry against emulated /dev/mem pages. Checks: ON/OFF from stock and ARRI, the
previous (legacy) matrix recognized in both directions, foreign content left alone, repeated ON,
and every byte around the two targets unchanged.
"""
import re
import struct
import sys
from pathlib import Path

import unicorn as uc
from unicorn import arm_const as A

B = Path(sys.argv[1])
mod = (B / "Arrivederci.mod").read_bytes()
magic, ver, flags, name, code_size, fnv, bss, on_off, off_off, hooks = struct.unpack_from("<III32s6I", mod)
assert magic == 0x444F4D53 and ver == 2 and name.rstrip(b"\0") == b"Arrivederci" and hooks == 0
code = mod[68:68 + code_size]
hdr = (B / "arrivederci_data.h").read_text()


def arr(n):
    return [int(v, 0) for v in re.search(rf"{n}\[\d+\] = \{{([^}}]*)\}}", hdr).group(1).split(",")]


g_stock, g_arri = (struct.pack("<96I", *arr(n)) for n in ("GAMMA_STOCK", "GAMMA_ARRI"))
g_old = struct.pack("<96I", *arr("GAMMA_LEGACY")) if "GAMMA_LEGACY" in hdr else g_arri
c_stock, c_arri, c_old = (struct.pack("<9h", *arr(n)) for n in ("COLOR_STOCK", "COLOR_ARRI", "COLOR_LEGACY"))
GPAGE, CPAGE, STOP, BASE = 0x30000000, 0x30100000, 0x31000000, 0x40ab3000
GOFF, COFF = 0x63C, 0x1D4


def run(on, grec, centry, sabin_present=False):
    m = uc.Uc(uc.UC_ARCH_ARM, uc.UC_MODE_THUMB)
    map_sz = ((len(code) + 0x2000) + 0xfff) & ~0xfff
    m.mem_map(BASE, map_sz)
    m.mem_write(BASE, code)
    for page, off, data in ((GPAGE, GOFF, grec), (CPAGE, COFF, centry)):
        m.mem_map(page, 0x1000)
        m.mem_write(page, b"\x5a" * 0x1000)
        m.mem_write(page + off, data)
    m.mem_map(0x20000000, 0x10000)
    m.mem_map(STOP, 0x1000)
    log, mounts, umounts, files, open_fds = [], [], [], {}, {}
    next_fd = [10]

    def intr(mu, n, _):
        nr = mu.reg_read(A.UC_ARM_REG_R7)
        a = [mu.reg_read(r) for r in (A.UC_ARM_REG_R0, A.UC_ARM_REG_R1, A.UC_ARM_REG_R2,
                                      A.UC_ARM_REG_R3, A.UC_ARM_REG_R4, A.UC_ARM_REG_R5)]
        ret = 0
        if nr == 5:
            path = bytes(mu.mem_read(a[0], 80)).split(b"\0")[0].decode(errors="replace")
            if path.startswith("/tmp_bt/mods/sabin/"):
                if (a[1] & 3) == 0:
                    ret = 5 if sabin_present else -2
                else:
                    fd = next_fd[0]
                    next_fd[0] += 1
                    open_fds[fd] = path
                    files[path] = bytearray()
                    ret = fd
            else:
                ret = {"/dev/mem": 3, "/tmp_bt/mods/arrivederci.log": 4}[path]
        elif nr == 192:
            assert a[1:5] == [4096, 3, 1, 3]
            ret = {0x03E5363C >> 12: GPAGE, 0x0444B1D4 >> 12: CPAGE}[a[5]]
        elif nr == 4:
            data = bytes(mu.mem_read(a[1], a[2]))
            if a[0] == 4:
                log.append(data.decode())
            elif a[0] in open_fds:
                files[open_fds[a[0]]].extend(data)
            ret = len(data)
        elif nr == 39:
            ret = 0
        elif nr == 21:
            assert a[3] == 4096
            mounts.append((bytes(mu.mem_read(a[0], 80)).split(b"\0")[0].decode(),
                           bytes(mu.mem_read(a[1], 80)).split(b"\0")[0].decode()))
        elif nr == 52:
            assert a[1] == 2
            umounts.append(bytes(mu.mem_read(a[0], 80)).split(b"\0")[0].decode())
        elif nr not in (6, 91):
            raise AssertionError(f"unexpected syscall {nr}")
        mu.reg_write(A.UC_ARM_REG_R0, ret)

    m.hook_add(uc.UC_HOOK_INTR, intr)
    m.reg_write(A.UC_ARM_REG_R0, on)
    m.reg_write(A.UC_ARM_REG_SP, 0x2000F000)
    m.reg_write(A.UC_ARM_REG_LR, STOP | 1)
    m.emu_start((BASE + (on_off & ~1)) | 1, STOP, count=2000000)
    gp, cp = bytes(m.mem_read(GPAGE, 0x1000)), bytes(m.mem_read(CPAGE, 0x1000))
    assert gp[:GOFF] == b"\x5a" * GOFF and gp[GOFF + 384:] == b"\x5a" * (0x1000 - GOFF - 384)
    assert cp[:COFF] == b"\x5a" * COFF and cp[COFF + 18:] == b"\x5a" * (0x1000 - COFF - 18)
    return gp[GOFF:GOFF + 384], cp[COFF:COFF + 18], "".join(log).strip(), mounts, umounts, files


def case(label, on, grec, centry, want_g, want_c, want_log, sabin_present=False, want_m=0, want_u=0):
    g, c, line, mounts, umounts, files = run(on, grec, centry, sabin_present=sabin_present)
    assert (g, c, line, len(mounts), len(umounts)) == (want_g, want_c, want_log, want_m, want_u), (
        label, line, len(mounts), len(umounts))
    print("  ok:", label, "->", line)
    return files


assert on_off == off_off and c_arri not in (c_stock, c_old)
odd = bytearray(c_stock)
odd[0] ^= 1
embedded = "SABIN_EMBEDDED" in hdr
on_sabin_m = 6 if embedded else 0
on_log_sabin = "m" if embedded else "n"
written = case("ON from stock", 1, g_stock, c_stock, g_arri, c_arri,
               f"arrivederci on=1 gamma=w color=w sabin={on_log_sabin}", want_m=on_sabin_m)
if embedded:
    assert len(written["/tmp_bt/mods/sabin/dcv_psf_table_movie_bayer.bin"]) == 18288
    assert len(written["/tmp_bt/mods/sabin/dcv_psf_table_movie_4k.bin"]) == 10728
    assert len(written["/tmp_bt/mods/sabin/spica_c_zero.bin"]) == 40000
    assert len(written["/tmp_bt/mods/sabin/spica_musashi_mov_4k_y_tap_step1.bin"]) == 48000
    assert len(written["/tmp_bt/mods/sabin/spica_musashi_mov_4k_y_tap_step2.bin"]) == 48000
case("OFF from ARRI", 0, g_arri, c_arri, g_stock, c_stock,
     "arrivederci on=0 gamma=w color=w sabin=u" if embedded else "arrivederci on=0 gamma=w color=w sabin=n",
     want_u=6 if embedded else 0)
case("ON over legacy gamma+matrix", 1, g_old, c_old, g_arri, c_arri,
     f"arrivederci on=1 gamma=l color=l sabin={on_log_sabin}", want_m=on_sabin_m)
case("OFF from legacy gamma+matrix", 0, g_old, c_old, g_stock, c_stock,
     "arrivederci on=0 gamma=l color=l sabin=u" if embedded else "arrivederci on=0 gamma=l color=l sabin=n",
     want_u=6 if embedded else 0)
case("repeated ON", 1, g_arri, c_arri, g_arri, c_arri,
     f"arrivederci on=1 gamma=a color=a sabin={on_log_sabin}", want_m=on_sabin_m)
case("foreign matrix untouched", 1, g_stock, bytes(odd), g_arri, bytes(odd),
     f"arrivederci on=1 gamma=w color=x sabin={on_log_sabin}", want_m=on_sabin_m)
case("ON with sabin staged", 1, g_stock, c_stock, g_arri, c_arri,
     "arrivederci on=1 gamma=w color=w sabin=m", sabin_present=True, want_m=6)
case("OFF with sabin staged", 0, g_arri, c_arri, g_stock, c_stock,
     "arrivederci on=0 gamma=w color=w sabin=u", sabin_present=True, want_u=6)
case("OFF leaves foreign gamma", 0, g_arri[:-4] + b"\0\0\0\0", c_arri, g_arri[:-4] + b"\0\0\0\0", c_stock,
     "arrivederci on=0 gamma=x color=w sabin=u" if embedded else "arrivederci on=0 gamma=x color=w sabin=n",
     want_u=6 if embedded else 0)
print("ALL ARRIVEDERCI v4 CHECKS PASSED (base %#x)" % BASE)
