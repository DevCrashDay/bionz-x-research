"""Offline Unicorn test of Mod Loader v2 (real shim + core ARM code, emulated syscalls and menu).

Usage: python tools/native_camera/mlv2/test_mlv2_unicorn.py BUILD_DIR [ARRIVEDERCI_V2_MOD]
Scenarios: fresh install, rescan, SMOD v2 load + hook fixup + lifecycle, SMOD v1 compatibility,
core upgrade in place, bad core rejection, hook mismatch, v1 takeover with an enabled v1 mod.
"""
import struct
import sys
from pathlib import Path

import unicorn as uc
from unicorn import arm_const as A

root = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(root))
from tools.research.pack_mod import extract_elf_text, pack_smod, fnv1a_32  # noqa: E402

B = Path(sys.argv[1])
SHIM = 0x05662760
SET_TEXT, SET_MODE = 0x157356D0, 0x1573BA68
ANCHOR = 0x1615EC40
MENU, NODES, STUBS, TARGETS, V1CODE = 0x30000000, 0x31000000, 0x32000000, 0x33000000, 0x34000000
MMAP0, STACK, STOP = 0x40000000, 0x50000000, 0x5F000000
ORIG12 = bytes.fromhex("2de9f04788b04ff014080122")

_, shim_code, shim_syms = extract_elf_text((B / "shim.elf").read_bytes(), expected_code_addr=SHIM)
core_bin = (B / "core.bin").read_bytes()
testmod = (B / "TestMod.mod").read_bytes()


def v1_mod(name, hook_target, lifecycle=True):
    code = b"\x70\x47\x00\xbf"                      # bx lr; nop
    hook = (hook_target, ORIG12, b"\x00\xbf" * 6)
    return pack_smod(name, V1CODE, code, [hook], flags=2 if lifecycle else 0)


class Camera:
    def __init__(self, files, v1_state=None):
        self.files = dict(files)
        self.fds, self.next_fd, self.mm_next = {}, 3, MMAP0
        self.texts, self.log, self.calls = [], [], []
        m = self.m = uc.Uc(uc.UC_ARCH_ARM, uc.UC_MODE_THUMB)
        m.mem_map(SHIM & ~0xfff, 0x2000, uc.UC_PROT_READ | uc.UC_PROT_EXEC)   # RX like the real cave
        m.mem_write(SHIM, shim_code)
        for base, size in ((0x15700000, 0x100000), (ANCHOR & ~0xfff, 0x1000), (MENU, 0x20000), (NODES, 0x10000),
                           (STUBS, 0x1000), (TARGETS, 0x1000), (V1CODE, 0x1000), (STACK - 0x10000, 0x10000), (STOP, 0x1000)):
            m.mem_map(base, size)
        m.mem_write(TARGETS + 0x100, ORIG12)
        m.mem_write(TARGETS + 0x200, ORIG12)
        # Firmware root node: 6 tabs; vtable entries are stubs returning 0; tab5 props answer 1.
        vt = NODES + 0x1000
        m.mem_write(vt, struct.pack("<72I", *[(STUBS + 4 * i) | 1 for i in range(72)]))
        kids = NODES + 0x2000
        tabs = [NODES + 0x3000 + 0x40 * i for i in range(6)]
        for t in tabs:
            m.mem_write(t, struct.pack("<10I", vt, 0, 0, 0, 0, 2, 1, 0, 0, 0))
        m.mem_write(kids, struct.pack("<6I", *tabs))
        self.root = NODES
        m.mem_write(NODES, struct.pack("<10I", vt, kids, 6, 0, 0, 1, 1, 0, 0, 0))
        self.tabs = tabs
        m.mem_write(MENU + 0x1F0, struct.pack("<I", 7))       # Mods tab current
        m.mem_write(MENU + 0x1F4, struct.pack("<I", 1))
        if v1_state:
            st = self.mmap(8192)
            m.mem_write(st, v1_state)
            m.mem_write(ANCHOR, struct.pack("<2I", 0x444F4D53, st))
        m.hook_add(uc.UC_HOOK_INTR, self.syscall)
        m.hook_add(uc.UC_HOOK_CODE, self.code)

    def mmap(self, n):
        a = self.mm_next
        n = (n + 0xFFF) & ~0xFFF
        self.m.mem_map(a, n)
        self.mm_next += n + 0x1000
        return a

    def ret(self, val=0):
        self.m.reg_write(A.UC_ARM_REG_R0, val & 0xFFFFFFFF)
        self.m.reg_write(A.UC_ARM_REG_PC, self.m.reg_read(A.UC_ARM_REG_LR))

    def code(self, m, pc, size, _):
        r = [m.reg_read(x) for x in (A.UC_ARM_REG_R0, A.UC_ARM_REG_R1, A.UC_ARM_REG_R2)]
        if pc == STOP:
            m.emu_stop()
        elif pc == SET_TEXT:
            self.texts.append((r[0] - MENU, bytes(m.mem_read(r[1], 40)).split(b"\0")[0].decode()))
            self.ret()
        elif pc == SET_MODE:
            self.ret()
        elif STUBS <= pc < STUBS + 0x1000:
            i = (pc - STUBS) // 4
            if i in (23, 32):                               # tab5 int/resid props
                m.mem_write(r[2], struct.pack("<I", 7))
                self.ret(1)
            else:
                self.ret(0)

    def syscall(self, m, intno, _):
        n = m.reg_read(A.UC_ARM_REG_R7)
        a = [m.reg_read(x) for x in (A.UC_ARM_REG_R0, A.UC_ARM_REG_R1, A.UC_ARM_REG_R2, A.UC_ARM_REG_R3,
                                     A.UC_ARM_REG_R4, A.UC_ARM_REG_R5)]
        self.calls.append(n)
        res = 0
        if n == 5:
            path = bytes(m.mem_read(a[0], 80)).split(b"\0")[0].decode()
            if path.endswith("mod_loader.log"):
                res = 99
            elif path == "/tmp_bt/mlv2/sd-stopped" and a[1] == 0x441:
                self.files[path] = b""
                res = self.next_fd; self.next_fd += 1; self.fds[res] = ["file", b"", 0]
            elif a[1] & 0o40000:
                names = sorted({p.rsplit("/", 1)[1] for p in self.files if p.rsplit("/", 1)[0] == path})
                if names or path == "/tmp_bt/mods":
                    res = self.next_fd; self.next_fd += 1; self.fds[res] = ["dir", names, False]
                else:
                    res = -2
            elif path in self.files:
                res = self.next_fd; self.next_fd += 1; self.fds[res] = ["file", self.files[path], 0]
            else:
                res = -2
        elif n == 3:
            f = self.fds[a[0]]
            data = f[1][f[2]:f[2] + a[2]]
            m.mem_write(a[1], data)
            f[2] += len(data)
            res = len(data)
        elif n == 19:
            self.fds[a[0]][2] = a[1]; res = a[1]
        elif n == 6:
            self.fds.pop(a[0], None)
        elif n == 4:
            data = bytes(m.mem_read(a[1], a[2]))
            if a[0] == 99:
                self.log.append(data.decode().strip())
            res = a[2]
        elif n == 217:
            f = self.fds[a[0]]
            if f[2]:
                res = 0
            else:
                buf = b""
                for name in f[1]:
                    nm = name.encode() + b"\0"
                    rl = (19 + len(nm) + 7) & ~7
                    buf += struct.pack("<QqHB", 1, 0, rl, 8) + nm.ljust(rl - 19, b"\0")
                m.mem_write(a[1], buf); f[2] = True; res = len(buf)
        elif n == 192:
            res = self.mmap(a[1])
        elif n == 125:
            m.mem_protect(a[0], a[1], uc.UC_PROT_ALL); res = 0
        elif n == 242:
            m.mem_write(a[2], struct.pack("<I", 1))
        elif n in (241, 0x0F0002):
            res = 0
        else:
            raise AssertionError(f"unexpected syscall {n}")
        m.reg_write(A.UC_ARM_REG_R0, res & 0xFFFFFFFF)

    def call(self, sym, *args):
        m = self.m
        for reg, v in zip((A.UC_ARM_REG_R0, A.UC_ARM_REG_R1), args):
            m.reg_write(reg, v)
        m.reg_write(A.UC_ARM_REG_SP, STACK - 0x100)
        m.reg_write(A.UC_ARM_REG_LR, STOP | 1)
        m.emu_start(shim_syms[sym] | 1, STOP, count=5_000_000)
        return m.reg_read(A.UC_ARM_REG_R0)

    def u32(self, a):
        return struct.unpack("<I", self.m.mem_read(a, 4))[0]

    def open_menu(self):
        self.texts.clear()
        self.call("ml_before_set_root", MENU, self.root)
        self.call("ml_after_refresh", MENU)
        return [t for off, t in self.texts if off != 0x5470]

    def items(self):
        kids = self.u32(self.root + 4)
        tab = self.u32(kids + 24)
        pages = self.u32(tab + 4)
        page0 = self.u32(pages)
        items = self.u32(page0 + 4)
        return [self.u32(items + 4 * i) for i in range(self.u32(page0 + 8))]

    def press(self, index):
        self.call("ml_before_enter", MENU, self.items()[index])


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print("  ok:", msg)


def with_fnv_variant(core, pad=4):
    """Same core code plus trailing zero bytes: a different, valid core image."""
    code = core[20:] + b"\0" * pad
    return struct.pack("<5I", 0x52434C4D, 2, len(code), fnv1a_32(code), 1) + code


print("Scenario 1: fresh install, no mods, then rescan picks up a new file")
cam = Camera({"/tmp_bt/mlv2/core.bin": core_bin})
texts = cam.open_menu()
check(cam.u32(cam.root + 8) == 7, "root now has 7 children (Mods tab added)")
check(texts == ["No mods found", ""], f"empty list shown ({texts})")
check(cam.u32(ANCHOR) == 0x444F4D53 and cam.u32(ANCHOR + 4) != 0, "fresh install created the tab-artwork anchor block")
cam.files["/tmp_bt/mods/TestMod.mod"] = testmod
texts = cam.open_menu()
check(texts == ["Test Mod", "OFF"], f"rescan shows the new mod without restart ({texts})")

print("Scenario 2: SMOD v2 ON/OFF with relocation, hook fixup and lifecycle")
cam.press(0)
_, tcode, tsyms = extract_elf_text((B / "test_mod.elf").read_bytes(), expected_code_addr=0)
hook = bytes(cam.m.mem_read(TARGETS + 0x100, 12))
entry = struct.unpack("<I", hook[8:12])[0]
base = entry - tsyms["t_hook"]
check(hook[:8] == bytes.fromhex("dff804f000bf00bf") and base >= MMAP0 and base % 0x1000 == 0,
      f"hook jumps to t_hook at load base {base:#x} + {tsyms['t_hook']:#x}")
check(cam.u32(base + tsyms["t_counter"]) == 1, "on() ran once (counter = 1)")
texts = cam.open_menu()
check(texts == ["Test Mod", "ON"], f"state survives a rescan ({texts})")
cam.press(0)
check(bytes(cam.m.mem_read(TARGETS + 0x100, 12)) == ORIG12, "OFF restored the original hook bytes")
check(cam.u32(base + tsyms["t_counter"]) == 0x101, "off() ran (counter = 0x101)")
cam.press(0)
check(cam.u32(base + tsyms["t_counter"]) == 1 and struct.unpack("<I", bytes(cam.m.mem_read(TARGETS + 0x108, 4)))[0] == entry,
      "second ON reloads into the same mapping (counter reset by reload, on() ran)")

print("Scenario 3: core upgrade in place keeps the enabled mod")
cam.files["/tmp_bt/mlv2/core.bin"] = with_fnv_variant(core_bin)
texts = cam.open_menu()
check(sum(l.startswith("core_load") for l in cam.log) == 2, "second core loaded")
check(texts == ["Test Mod", "ON"], f"enabled mod kept across the upgrade ({texts})")
cam.press(0)
check(bytes(cam.m.mem_read(TARGETS + 0x100, 12)) == ORIG12, "new core can switch the mod OFF")

print("Scenario 4: bad core is rejected, previous core stays active")
bad = bytearray(with_fnv_variant(core_bin, pad=8)); bad[-1] ^= 0xFF   # new header hash, corrupt code
cam.files["/tmp_bt/mlv2/core.bin"] = bytes(bad)
texts = cam.open_menu()
check(any(l.startswith("core_bad") for l in cam.log) and texts == ["Test Mod", "OFF"], "corrupt core ignored, menu still works")

print("Scenario 5: SMOD v1 compatibility, hook mismatch")
cam.files["/tmp_bt/mods/OldStyle.mod"] = v1_mod("Old Style", TARGETS + 0x200)
texts = cam.open_menu()
check(texts == ["Old Style", "OFF", "Test Mod", "OFF"], f"v1 and v2 listed, sorted ({texts})")
cam.press(0)
check(bytes(cam.m.mem_read(TARGETS + 0x200, 12)) == b"\x00\xbf" * 6, "v1 hook applied at its fixed target")
cam.press(0)
check(bytes(cam.m.mem_read(TARGETS + 0x200, 12)) == ORIG12, "v1 hook reverted")
cam.m.mem_write(TARGETS + 0x100, b"\xff" * 12)
cam.press(1)
check(bytes(cam.m.mem_read(TARGETS + 0x100, 12)) == b"\xff" * 12 and cam.open_menu()[3] == "ERR",
      "foreign bytes at the hook target: no write, shown as ERR")

print("Scenario 6: takeover of a running v1 loader with an enabled v1 mod")
v1 = bytearray(8192)
struct.pack_into("<3I", v1, 0, 0x444F4D53, 1, 1)
mod_off = 0x418
name = b"Legacy On".ljust(32, b"\0")
path = b"/tmp_bt/mods/Legacy.mod".ljust(64, b"\0")
hook = struct.pack("<II16s16s", TARGETS + 0x200, 12, ORIG12, b"\x00\xbf" * 6)
v1[mod_off:mod_off + 440] = name + path + bytes([1, 1, 0, 2]) + struct.pack("<5I", V1CODE, 4, 0, 0, fnv1a_32(bytes.fromhex("704700bf"))) + hook + bytes(280)  # as a real v1 loader stores it
cam = Camera({"/tmp_bt/mlv2/core.bin": core_bin, "/tmp_bt/mods/Legacy.mod": v1_mod("Legacy On", TARGETS + 0x200)},
             v1_state=bytes(v1))
cam.m.mem_write(TARGETS + 0x200, b"\x00\xbf" * 6)        # v1 had applied the hook
cam.m.mem_write(V1CODE, bytes.fromhex("704700bf"))          # and its code is resident
texts = cam.open_menu()
check(texts == ["Legacy On", "ON"], f"enabled v1 mod imported as ON ({texts})")
cam.press(0)
check(bytes(cam.m.mem_read(TARGETS + 0x200, 12)) == ORIG12, "v2 switches the imported v1 mod OFF (hook reverted)")

if len(sys.argv) > 2:
    print("Scenario 7: Arrivederci v2 is accepted and listed")
    cam = Camera({"/tmp_bt/mlv2/core.bin": core_bin, "/tmp_bt/mods/Arrivederci.mod": Path(sys.argv[2]).read_bytes()})
    check(cam.open_menu() == ["Arrivederci", "OFF"], "Arrivederci v2 parsed and listed")

print("Scenario 8: SD disable restores the mod hook and stock menu root")
cam = Camera({"/tmp_bt/mlv2/core.bin": core_bin, "/tmp_bt/mlv2/sd-managed": b"",
              "/tmp_bt/mods/TestMod.mod": testmod, "/mnt/sd/mods/Legacy.mod": v1_mod("Unstaged", TARGETS + 0x200)})
stock_children = cam.u32(cam.root + 4)
check(cam.open_menu() == ["Test Mod", "OFF"], "managed mode lists only RAM-staged mods")
item = cam.items()[0]
cam.press(0)
cam.files["/tmp_bt/mlv2/sd-disabled"] = b""
check(cam.call("ml_before_enter", MENU, item) == 0, "stale Mods entry is consumed without reactivation")
check(bytes(cam.m.mem_read(TARGETS + 0x100, 12)) == ORIG12, "SD disable restores the original hook")
cam.open_menu()
check(cam.u32(cam.root + 8) == 6 and cam.u32(cam.root + 4) == stock_children,
      "the next root setup restores the original six-tab tree")
check("/tmp_bt/mlv2/sd-stopped" in cam.files, "OFF and root restoration publish the stop receipt")
del cam.files["/tmp_bt/mlv2/sd-stopped"], cam.files["/tmp_bt/mlv2/sd-disabled"]
check(cam.open_menu() == ["Test Mod", "OFF"], "revalidated card restores Mods with the mod OFF")
cam.press(0)
check(bytes(cam.m.mem_read(TARGETS + 0x100, 12)) != ORIG12, "the mod can be enabled again in the same session")

print("Scenario 9: SD disable preserves foreign bytes at a mod hook")
cam = Camera({"/tmp_bt/mlv2/core.bin": core_bin, "/tmp_bt/mods/TestMod.mod": testmod})
cam.open_menu()
cam.press(0)
foreign = b"\xaa" * 12
cam.m.mem_write(TARGETS + 0x100, foreign)
cam.files["/tmp_bt/mlv2/sd-disabled"] = b""
cam.open_menu()
check(bytes(cam.m.mem_read(TARGETS + 0x100, 12)) == foreign, "foreign hook bytes are preserved")
check(any(line.startswith("mod_off_failed") for line in cam.log), "incomplete rollback is reported")
check("/tmp_bt/mlv2/sd-stopped" not in cam.files, "failed rollback blocks reactivation")

print("ALL SCENARIOS PASSED")
