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

## What this milestone adds

- A build table with the three offsets and their replayed bytes.
- A verifier that fails closed on: wrong `viewUnified2.so` SHA-256
  (`7bb0f245…39c28bce`), wrong bytes at a site, a site that occurs more than once, or a
  moved site.
- Five tests covering those cases and asserting the a6400 offsets are not silently reused.

## Remaining work

| Step | Content | Blocker |
| --- | --- | --- |
| M2 | a6100 shim placement: find a cave, or let the guard `mmap` the shim; guard preimages for `viewUnified2.so`, `libObj.so` (`a9e2c016…`) and `im.elf` (`ab7eed01…`) | needs the a6100 module hashes and a cave decision |
| M3 | core's Sony-call addresses for this build (menu node constructor, tab array) and the fake-node vtable | per-site mapping (see above) |
| M4 | rebase `MenuTouchFull` v2 and locate `Arrivederci`'s a6100 tables | per-site mapping + av-cam table discovery |
| M5 | emulator harness: stage `.mod` files, apply the shim through QMP/gdbstub, ON/OFF receipts | no guest shell in the a6100 run yet |

## Boundary

None of this is installable on the camera: the a6100 still has no injection path (E9102).
The emulator is the target, and M5 is the piece that would make a mod actually run there.
