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

## Toolchain (now installed, root-free)

`scripts/setup_arm_toolchain.sh` installs the cross compiler without root by downloading the
Ubuntu `*arm-linux-gnueabi` packages and extracting them under `artifacts/toolchain/`, with
wrapper scripts that supply the sysroot, the gcc-cross directory and the host library path
the extracted compiler cannot know. Re-running it is a no-op.

Verified end to end on this machine:

```
bash scripts/setup_arm_toolchain.sh            # 33 packages, ~51 MB, no root
export PATH="$PWD/artifacts/toolchain/arm-linux-gnueabi/bin:$PATH"
bash tools/native_camera/mlv2/build_wsl.sh /tmp/mlv2-repro
```

The a6400 loader builds reproducibly with it:

| Output | Bytes | SHA-256 |
| --- | ---: | --- |
| `shim.bin` | 1008 | `bdd1ae95fd7fbfd0a95190d41250055808f2675b03ee28f1e8d026998c28a2a6` |
| `core.bin` | 8136 (8116 code) | `cd8971b344b66e3560936177d4047be5bf96b0e4f6572cf2682daba40cc9f8ca` |
| `mlv2_guard_arm` | 692564 | (built) |
| `mlv2.req` | 1136 | `02dd9b7d2319674d8d4874c067b73dde0ecaf6614830215361c1ae69f91e2e10` |

One step still fails: the SD-boot companion `.so` links against
`tools/sony_sysroot/lib/libc.so`, which is not present in this checkout. That file is only
needed for the SD-card boot package, not for the RAM install through the guard.

## What this milestone adds

- A build table with the three hook offsets and their replayed bytes, the shim window and
  the two PLT constants.
- A verifier that fails closed on: wrong `viewUnified2.so`/`libtestcmd.so` SHA-256, wrong
  bytes at a site, a site that occurs more than once, a moved site, a missing PLT stub
  prefix, or a cave that no longer fits the page.
- Nine tests covering those cases and asserting the a6400 offsets are not silently reused.

## M5 path: the emulated guest has a root shell

The a6100 run's serial console is a root shell, not just kernel log. A second QEMU instance
started with `SERIAL=unix:/tmp/a6100-serial.sock,server=on,wait=off` answered:

```
/ # echo MARK-$$
MARK-141
/ # ls /tmp_bt
ble_local_keys  bsa_s_pid  bt-avk-fifo
```

So the emulator has an injection path after all: stage the loader files through
`SETTING_FILES` (they land in `/setting` on the disposable overlay), copy them to
`/tmp_bt`, and run the guard from the shell. No host-side memory poking is needed.

## What the guard needs, and what is still a6400-specific

The guard is better factored than expected: it finds the `im.elf` process, takes each
library base from `/proc/<pid>/maps` by leaf name, and computes `view_bias`/`testcmd_bias`
against its reference bases. It is already built with `-DMLV2_VIEW_BASE`/`-DMLV2_SHIM_ADDR`
overrides, so a per-build port is a matter of constants:

| Constant | Where | a6400 | a6100 |
| --- | --- | --- | --- |
| three hook VAs | `mlv2_guard.c`, `mlv2_trampoline.S` | `+0x5ab654`, `+0x5aba80`, `+0x5ac044` | `+0x5acc0c`, `+0x5ad038`, `+0x5ad5fc` (**resolved**) |
| `SET_TEXT`, `SET_MODE` | `mlv2.h` | `+0x14f6d0`, `+0x155a68` | `+0x150030`, `+0x1563c8` (**resolved**) |
| tab rectangle slots | `mlv2_guard.c` | `+0x807a04`, `+0x807a84`, `+0x807a64`, `+0x807ae4` | **ambiguous**: the 48–96 byte table signature before those slots occurs **6 times** in the a6100 image (a6400: once), so the live layout table has to be identified at runtime |
| v1 anchor | `mlv2.h` (`+0xb78c40` in viewUnified2 `.bss`) | one free 8-byte slot | needs a free a6100 `.bss` slot |
| event manager (5) | `core.c` | `libObj +0xfd974` (new), `+0xfce20` (ctor), `+0x100d5d` (push), RW slot file `+0x1369984` | **unresolved**: both builds are stripped (1,901 dynsyms, no symbol at those offsets), so these need structural matching |

The three functions and the data slot were located precisely: they fall inside libObj's RX
mapping (`0x05d18000 + offset`) and, for the slot, inside its RW mapping (file offset
`0x1369984`). That is where the remaining RE work is.

## Arrivederci on the a6100: what the data says (no port claimed)

Reconnaissance for the first real mod, done read-only:

- `Arrivederci.mod` embeds both blocks: the stock EC record at file `0x7d4` (192 B) and the
  ARRI payload at `0x894` (192 B, different).
- The a6100 `av-cam.bin` contains that stock 192-byte block **verbatim** at file
  `0x88d6bc` → VA `0x63e536bc`, **PA `0x03e536bc`** (the a6400 table is at `0x03e5363c`,
  i.e. +0x80 in this build).
- A first `/dev/mem` read from the guest at that PA returned exactly the stock block. A
  later read returned different contents (`0000 0071 …`), so the EC record is rewritten at
  runtime depending on the active gamma/PP. A static address alone is therefore not enough
  for a reliable mod.
- The CC matrix at the a6400 address is **all zeros in the image** (it is computed at run
  time), so it cannot be found by pattern. The relative-delta candidate
  (`0x03e536bc + 0x5F7B98 = 0x0444b254`) read back a descending table
  (`10, 9, 8, 7, 6, 5, …`), not a Q10 matrix. The a6100 matrix address is still unknown.
- A `printf … | dd of=/dev/mem` write of the ARRI block did not take effect (the readback
  was unchanged). Cause not established (busybox `printf` escapes or a strict `/devmem`
  are both possible), so no write is claimed.

Consequence: Arrivederci's a6100 port needs a runtime method, not static guessing — e.g.
`pmemsave` the av-cam region through QMP and locate the matrix by its Q10 row sums
(≈1024 per row), then confirm the EC record's live location the same way.

## Remaining work — updated

| Step | Content | State |
| --- | --- | --- |
| M2 | shim window + guard preimages | **done** (§M2) |
| M3 | `SET_TEXT`/`SET_MODE` | **done**; five `libObj`-local event addresses remain |
| M3b | structural match of the five `libObj` addresses | next; needs disassembly of the a6400 sites |
| M4 | rebase `MenuTouchFull` v2 and locate `Arrivederci`'s a6100 tables | EC record address known (PA `0x03e536bc`), but it is rewritten at runtime; matrix address unknown (needs a Q10 row-sum scan of guest RAM); `MenuTouchFull` hooks still need their libObj sites |
| M5 | emulator harness: stage `.mod` files, install the guard from the guest shell, ON/OFF receipts | toolchain **available**; guest shell **available**; needs the a6100 guard build (M3b + tab table + anchor) |
| M3c | tab rectangle table: identify the live layout table (6 candidates) instead of guessing | runtime observation in the emulator |
| M4 | rebase `MenuTouchFull` v2 and locate `Arrivederci`'s a6100 tables | EC record address known (PA `0x03e536bc`), but it is rewritten at runtime; matrix address unknown (needs a Q10 row-sum scan of guest RAM); `MenuTouchFull` hooks still need their libObj sites |

## Boundary

None of this is installable on the camera: the a6100 still has no injection path (E9102).
The emulator is the target, and M5 is the piece that would make a mod actually run there.
