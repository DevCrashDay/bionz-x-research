# E9101 — Where Picture Profile is gated on an ILCE-6100 (a6100)

- **Date:** 2026-11-21
- **Scope:** offline, read-only analysis of one captured a6100 partition dump plus one
  local QEMU boot of the same dump. No camera, USB, PTP/UVC, `/dev/mem`, service mode,
  SD or flash access. No firmware bytes are published here; only names, sizes, hashes,
  addresses and instruction encodings.
- **Declared stimulus:** none. The QEMU boot below is the stock prepared-input run; no
  probe injection was used.
- **Tool:** [tools/research/a6100_picture_profile.py](../../tools/research/a6100_picture_profile.py)
  (`report`), tests in [tests/test_a6100_picture_profile.py](../../tests/test_a6100_picture_profile.py).

## 1. Inputs

Dump: `a6100-recovery/local-only/dumps/20261120-130512` (read-only; never modified).

`sha256sum -c SHA256SUMS` in the dump directory: **19/19 OK** (all `nflasha*` images).
No mismatch was found and no stored hash was refreshed.

Partition map from the dump's own `partitions.txt`; the user area starts at eMMC offset
`0x800000` after two 4 MiB boot partitions (`tools/research/qemu_boot_inputs.py`).

| Role | Dump file | Bytes | SHA-256 |
| --- | --- | ---: | --- |
| Main firmware FAT (kernel, initrd, av-cam) | `nflasha3.img` | 50,331,648 | `08379a20…a269d8a1df` (stored) |
| Setting FAT (`Backup.bin`, `kemco.txt`) | `nflasha2.img` | 20,971,520 | `c3c5c592…ba64d5b9cb` (stored) |
| Root filesystem (ext2, the camera application) | `nflasha15.img` | 314,572,800 | `63d8f46007637a6dd47ffa1a8de0b3c42842259b6357a5bc8b347d3120b0b1de` (stored, verified) |

Carved from those images for analysis (kept only under the gitignored `artifacts/`):

| Input | Bytes | SHA-256 |
| --- | ---: | --- |
| `av-cam.bin` | 16,803,048 | `58b2118db858f3eb1594ae6cef3f628b7fa3cc6db9173b094565535d48e6ff9e` |
| `Backup.bin` (BK4 preset) | 1,171,224 | `3b4b666418a8753b27c3e7384e74f93f1cb1fd83e4a3aa063f5899ab6ae3a590` |
| `bin/im.elf` | 21,820 | `ab7eed01e7ac204f555298bdfd13c0ffa89b34d1f1cd399bd74ad734ede8d93e` |
| `lib/viewUnified2.so` | 11,535,076 | `7bb0f245c8ccdff8a7d4a6f473357b50cbdeed12d081bde805edffd339c28bce` |
| `lib/CautionConfig.so` | 12,078,652 | `a0ed06e720cc77a11b498bb5bf59db27cc5f943cb99bcdf083225e2eb5fde166` |
| `share/app/string_english.uxc` | 375,160 | `b62c58521eca7b0443c5e570aaf8554ae5b05d94f46940dce7d9bc01a94b2921` |

Comparison baseline (offline, local, from the a6400 package already used by E0515):
`viewUnified2.so` `1e2867b6…b6e7f2` (11,530,552 B), `CautionConfig.so` `bfd1bd7b…150ef7`
(12,070,800 B), `string_english.uxc` `b62c5852…b2921`, a6400 `Backup.bin` from a
prepared a6400 run directory. Both reference hashes match E0515, so the baseline is the
same binary that record analysed.

## 2. What the dump exposes

`Backup.bin` is a BK4 preset; unit `0x01070316` (the menu-mode selector used by
`viewUnified2.so`) reads **67** — the same mode number the a6400 uses.

`im.elf` is only 21,820 B: the application logic lives in the `lib/viewUnified*.so`
modules, and the menu tree is built at runtime from the `CautionConfig.so` node objects.

## 3. Layer-by-layer result

| Layer | Evidence | a6100 | a6400 |
| --- | --- | --- | --- |
| Strings | `string_english.uxc` contains `Picture Profile`, `PP1`–`PP10`, `S-Log2`, `S-Log3`, `S-Gamut3`, `HLG1`–`HLG3`, `Cine1`–`Cine4`, `ITU709` | present; file **byte-identical** to a6400 (same SHA-256) | present |
| Node objects | `CautionConfig.so` `.dynsym` | 13 `cmnViewSettingNodeRootPictureProfile*` exports | same set |
| Root registry | REL `R_ARM_ABS32` array ending `.data` | 416 entries, PP at **index 251**, 13 PP entries | 416 entries, PP at index 251, 13 entries |
| Menu tree | `viewUnified2.so` relocations | **21** references to `cmnViewSettingNodeRootPictureProfile`, **15** to `cmnViewSettingNodeRootSLogDispAssist` | 21 and 15 |
| Settings | BK4 unit table | 30,762 units | 30,746 units |
| Pipeline | `av-cam.bin` | `SET_PICTUREPROFILE[%d…`, `CheckSetPictureProfile`, `PP_GammaType`, `PP_ColorMode`, `S-LOG2`, `ITU709` all present | present (different build) |

Settings differential (a6400 ⊆ a6100): **0 units missing from the a6100, 16 units only in
the a6100** (subsystems `0x0092`, `0x00f9`, `0x010d`).

**Reading.** On this dump, Picture Profile is not hidden by removing anything: the text,
the setting-node classes, the model's own root registry, the menu-tree references, the
setting units and the ISP command handler are all present. The remaining gate can only be
a **runtime availability decision**, not a data omission. That distinction is the answer
to "UI entry hidden vs pipeline/setting missing": neither of those two is the case here;
the data is complete and something must disable the item while the application runs.

The one runtime decision with a name is
`CmnForUseMultipleViewStatus::setPictureProfileValidInfo(bool)` in `viewUnified2.so`:
entry `0x00413940`, a single byte store `strb r0,[r3,#0]` at `0x0041394C` (encoding
`18 70`) reached through GOT slot `0x009494D4`. It is exported by `viewUnified2.so` and
imported by `viewUnified4.so`, but **no caller or producer was located** in either module
(direct `bl` scan of both `.text` sections, and the GOT slot is never materialised as a
literal in code). The stored object's address is only known at runtime, so this record
does not patch it and does not claim it as the gate.

## 4. Provenance anomaly — the dump is not stock-clean in one file

`debugfs stat /lib/CautionConfig.so` on the dumped root filesystem:

```
Mode: 0644   Size: 12078652
mtime: 0x6aff5417 -- Thu Nov 19 19:55:03 2026
```

Every sibling application file carries the firmware build date and the stock mode:

```
lib/viewUnified2.so        0555  Mon Apr  5 20:50:56 2021
share/app/string_english.uxc 0755 Mon Apr  5 18:53:45 2021
```

The dump was taken 2026-11-20 13:15, so `/lib/CautionConfig.so` was written to the camera
about one day earlier. `CautionConfig.so` is exactly the per-item availability/caution
table. Consequences, stated rather than smoothed over:

- This dump cannot establish what a **stock** a6100 hides; the file that decides item
  availability is not a stock file.
- The a6100/a6400 `CautionConfig.so` size difference (7,852 B) therefore cannot be
  attributed to the model.
- Nothing was rewritten, and no hash was "fixed". The compressed `/setting` partition also
  already carries `ulogio.bin` (the emulator refuses to add it a second time), which is
  consistent with earlier on-camera work on this body.

## 5. Emulator receipt (default path unchanged)

Prepared inputs verified by `qemu_boot_inputs verify` (`emmc.img` SHA-256
`cb3388a07134250decf71cda5b6740195c7d5370975ba59fffd785ad7d9b3ec1`), then:

```
A6400_QEMU_DIR=$HOME/sony-a6400-qemu-mt \
A6400_QEMU_DATA_DIR=$PWD/artifacts/a6100-inputs \
RUN_DIR=$PWD/artifacts/a6100-pp/runs/boot1 TIMEOUT=60 ULOGIO= \
SERIAL=file:$PWD/artifacts/a6100-pp/runs/boot1-serial.log \
nice -n 10 bash ./qemu-a6400/run.sh
```

Result: the a6100 kernel boots with its own command line (`console=ttyAM0`, `initrd`,
`nflasha*` layout): `L2x0 series cache controller enabled`, `cxd4115_clocksource_init`,
`sony_usb_init: CXD4132`, eMMC tuning, then VPH scanout programming
(`[VPH] ch53 base=… lines=480`). Run ended by the 60 s timeout at guest time `3.78 s`
(exit 124). No option was added to the boot path and no byte of the firmware or the dump
was changed; this is the baseline the opt-in would have to leave untouched.

The menu is not reached in that window, so no PP screen is claimed from this run.

## 6. Failures and corrections

- A first pass counted Picture Profile relocations with a shell `grep -E` pattern that
  PowerShell had mangled, and reported **0** references in the a6100 `viewUnified2.so`.
  The conclusion drawn from it ("PP compiled out of the model's menu tree") was wrong.
  Re-running with a direct ELF parse gave 21 references, the same count as the a6400, and
  that correction is what the record above uses. Lesson: parse ELF in-process, never
  through a shell quote layer.
- An attempt to resolve the validity byte through the setter's GOT indirection produced an
  addend outside `.bss`, so the object address is treated as unresolved rather than
  guessed.
- The QEMU builds present on this machine predate `work-dma` (E0641), so the boot receipt
  is a plain run; it is a baseline, not a claim of reaching the menu.

## 7. Boundary and next steps

The smallest honest unlock cannot be named from this dump yet, and none was applied:

1. Obtain a **pre-2026-11-19** copy of `/lib/CautionConfig.so` (vendor image or earlier
   dump) and re-run the layer audit against it. If PP disappears in a stock file, the
   gate is in that table and is a data patch; if it does not, the gate is runtime.
2. Locate the producer of the `setPictureProfileValidInfo` byte with a runtime trace.
   That needs the application past the current av-cam step-32 wait, which the emulator
   does not reach in 60 s.
3. Only then is a one-byte opt-in (validity byte = 1, default off) defensible as an
   unlock. Writing it today would be a guess, and a guess can produce the fake menu this
   task explicitly excludes.

A real camera write was not attempted and is not needed for any step above.

## 8. Assumptions

- The a6400 root filesystem copies are the ones E0515 analysed (hashes match), so the
  differential is against the same build the repo already trusts.
- `nflasha15.img` is the root filesystem (ext2 superblock at `+0x438`, `debugfs` lists
  `bin/`, `lib/`, `share/app/`), and `nflasha3`/`nflasha2` are the FAT images the boot
  preparation tool already parses.
- Menu-mode 67 selects the model's main-menu tree; the a6100 and a6400 both store 67, and
  their trees differ in layout, so the tree is per-build even at the same mode number.
