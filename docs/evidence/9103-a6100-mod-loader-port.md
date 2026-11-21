# E9103 — Porting Mod Loader v2 to the ILCE-6100: first milestone

- **Date:** 2026-11-21
- **Scope:** offline. Static comparison of the a6100 V1.00 `viewUnified2.so` with the
  ILCE-6400 2.00 build the loader sources target. No camera. No mod executed yet.
- **Code:** [tools/native_camera/mlv2/a6100_layout.py](../../tools/native_camera/mlv2/a6100_layout.py),
  tests [tests/test_mlv2_a6100_layout.py](../../tests/test_mlv2_a6100_layout.py).

## Question

Can the existing Mod Loader v2 (shim + core + SMOD v2 mods) be retargeted to the a6100
emulator build, and can the existing mods (`MenuTouchFull`, `Arrivederci`) run on it?

## Result: the three view hooks are byte-identical, shifted by +0x15b8

The loader's shim is reached through three trampolines that replay the first bytes of
three `viewUnified2.so` functions (`mlv2_trampoline.S`). Those functions are the same code
in the a6100 build:

| Hook | a6400 offset | a6100 offset | delta | replayed bytes |
| --- | --- | --- | --- | --- |
| `set_root` | `0x5ab654` | **`0x5acc0c`** | +0x15b8 | `044600af51b100f5c23303f52b731960` |
| `refresh` | `0x5aba80` | **`0x5ad038`** | +0x15b8 | `2de9f04f9bb006af044607f12000` |
| `open_item` | `0x5ac044` | **`0x5ad5fc`** | +0x15b8 | `00f5c23303f52c73` |

Each site is located by a unique signature (the 8-byte `open_item` replay alone occurs
twice in the image, so a 10-byte signal is used) and the bytes at the recorded offset must
equal the replay the trampoline performs. A different image, a moved site or a duplicated
site is refused rather than guessed.

## Result: the shift is not uniform across the build

200 sampled 32-byte windows from the a6100 image were searched in the a6400 image: 129
matched, with the most common deltas `+0x15a8` (12), `+0x14a8` (7), `+0x9a8` (5),
`+0x1490` (4), `+0xf20` (3). The two builds are close but not a single-offset pair, so
every *other* address the loader and the mods use must be mapped per site, not by adding a
constant.

Consequence for the existing mods:

- `MenuTouchFull` v2 hooks two menu functions at fixed addresses; those targets need their
  own a6100 offsets before it can be loaded.
- `Arrivederci` v3 writes fixed physical addresses inside `av-cam`'s LIRO RAM (EC record 31
  at `0x03e5363c`, matrix entry B8 at `0x0444b1d4`) for the a6400 build. The a6100 has the
  same stages but different tables; those must be located per build before the mod means
  anything.
- The loader's own pieces beyond the trampolines — the 3120-byte code cave (a6400 uses a
  `libtestcmd.so` cave), the fake-node vtable and the core's calls into Sony menu
  constructors — also need a6100 addresses.

So "test the existing mods on the a6100" is a porting task per mod, not a configuration
flag. What is now established is that the loader's hook layer itself is portable with
known, verified offsets.

## M2 — shim window and guard preimages (done)

The shim needs 2048 bytes of executable, zero, unused memory. The a6400 loader uses
`libtestcmd.so` at `base + 0x2800`; the **same window works on the a6100**:

| Item | Value |
| --- | --- |
| file | `libtestcmd.so`, SHA-256 `66b30d86…55e222a`, 10,080 B (`0x2760`) |
| shim window | `base + 0x2800`, 2048 B, ends exactly at the page boundary `0x3000` |
| why it is safe | the file ends at `0x2760`, so `0x2800` is beyond EOF; it sits in the last page of the `R E` segment (zero fill) and past the used range of the `RW` segment (`0x203c + 0x1fc`) |
| writability | the shim calls `ml_make_rwx` on its slot, as on the a6400 |

Guard preimages this build needs: `viewUnified2.so` `7bb0f245…`, `libObj.so`
`a9e2c016…`, `im.elf` `ab7eed01…`, `libtestcmd.so` `66b30d86…`, plus the three hook
byte sequences above. All four hashes and the hook bytes are now in the layout module and
checked by tests.

## M3 — loader constants (two of seven resolved)

The shim calls two Sony functions through PLT stubs. They are resolvable by **symbol
name**, not by offset arithmetic: the `.plt` is not a constant shift of the a6400 one
(index arithmetic was off by ~0x32c; a 12-byte signature matched hundreds of sites
because it is the generic Thumb-to-ARM veneer plus PIC constant loads).

| Constant | Symbol | a6400 | a6100 |
| --- | --- | --- | --- |
| `SET_TEXT` | `GEN_Label::setTextDirect(char const*)` | `0x14f6d0` | **`0x150030`** |
| `SET_MODE` | `ux::wgtsys::Widget::setMode(WidgetMode)` | `0x155a68` | **`0x1563c8`** |

Still unresolved are the five `libObj`-local addresses the core uses for the
enter/confirm event (`REF_OP_NEW` `+0x0fd974`, `REF_EV_CTOR` `+0x0fce20`,
`REF_EVMGR_PUSH` `+0x100d5d`, `REF_EVMGR_SLOT` `+0x1371984`, and the alcor index hook at
`+0x79d4b6`). Neither build exports a symbol at those offsets (both have the same 1901
exported symbols), so they need structural identification: disassemble the a6400 site,
take its constants and shape, and match inside the a6100 `libObj.so`.

## Toolchain blocker for the actual binaries

`build_wsl.sh` needs `arm-linux-gnueabi-gcc`/`ld`/`objdump`. **That toolchain is not
installed on this machine** (`command -v` finds none, `/opt` is empty), so the shim, the
core and any `.mod` cannot be compiled here as things stand. The layout work above is
static and complete for M2; producing binaries needs the cross toolchain installed first.

## What this milestone adds

- A build table with the three hook offsets and their replayed bytes, the shim window and
  the two PLT constants.
- A verifier that fails closed on: wrong `viewUnified2.so`/`libtestcmd.so` SHA-256, wrong
  bytes at a site, a site that occurs more than once, a moved site, a missing PLT stub
  prefix, or a cave that no longer fits the page.
- Nine tests covering those cases and asserting the a6400 offsets are not silently reused.

## Remaining work — updated

| Step | Content | State |
| --- | --- | --- |
| M2 | shim window + guard preimages | **done** (§M2) |
| M3 | `SET_TEXT`/`SET_MODE` | **done**; five `libObj`-local event addresses remain |
| M3b | structural match of the five `libObj` addresses | next; needs disassembly of the a6400 sites |
| M4 | rebase `MenuTouchFull` v2 and locate `Arrivederci`'s a6100 tables | per-site mapping + av-cam table discovery |
| M5 | emulator harness: stage `.mod` files, apply the shim through QMP/gdbstub, ON/OFF receipts | needs the binaries, i.e. the ARM toolchain |

## Boundary

None of this is installable on the camera: the a6100 still has no injection path (E9102).
The emulator is the target, and M5 is the piece that would make a mod actually run there.
