# QEMU `sony-a6400`: findings and next steps

Snapshot: 2026-11-15, after E0521 and E0522. Nothing below has been run on the real camera.
Labels: [C] confirmed by a run receipt or disassembly, [I] inference, [A] assumption.

## Where the boot stands

`./run.sh` (WSL2) boots the main `nflasha3` firmware through these stages:

1. Kernel at `0x00108000` with the kemco block at `0x00103000`; 4 Cortex-A5 cores online [C].
2. `init` mounts every eMMC partition through a fresh qcow2 overlay (`/`, `/usr`, `/setting`, `/system`, `/log`, …) [C].
3. 40 kernel modules load, including `backup`, `dmm`, `upm`, `mcmn_drv`, `liro`, `dma330` and `hdmi` [C].
4. av-cam (LIRO µITRON) runs `entry_pre` / `entry_main`, and its tasks bind to CPUs 0–3 [C].
5. `stella`, `DMP_2D_Initialize : 0`, the `im.elf` monitor buffer, and a BusyBox `ash` on `ttyAM0` [C].

A shell prompt is not a working camera application. No `liro-*` interrupt has fired yet [C].

## Findings

The bootloader hand-off is not executed; it is reproduced. Each item below was recovered from the
a6400 binaries, then cross-checked against `Oein/a5000-emulator` `docs/BOOT.md`.

| # | Finding | Evidence | How it is modelled |
| --- | --- | --- | --- |
| 1 | The kernel runs at TEXT_OFFSET `0x00108000` and reads `"kemco"+cmdline` from `r2 = 0x00103000`, not ATAGS/DTB | `boot/loadaddr.txt`; `cmdline override at 60103005` [C] | `sony_a6400_load_boot_contract()` |
| 2 | `backup.ko Backup_load=0` expects `Backup.bin` to be preloaded at the physical address in its header word 0 (`0x02E80000`). Without it, `-EIO` and ~20 dependent modules fail | `backupfile_init` @`0x4614`, `Data_check` @`0x3e04` [C] | `-device loader` in `run.sh` |
| 3 | The main boot needs `/setting/kemco.txt`. `data/nflasha3-kemco.bin` is the updater cmdline, whose `memrsv` leaves av-cam's RAM unreserved | E0521 §0.3 [C] | `run.sh` reads it from `setting.tar` (console token swapped) |
| 4 | `liro.ko adrs=035c6000` expects `av-cam.bin` preloaded there (`LIRO` magic at +4) | `Magic unmatched` before the fix, `end entry_main` after [C] | `-device loader` in `run.sh` |
| 5 | Secondary cores: `cxd900x0_smp_init` writes the physical `cxd900x0_secondary_entry` to SRAM `0xFE005010 + 4*(cpu-1)` and issues `SEV`, then the standard pen-release handshake follows | `0x6011ed40..0x6011ee98`, `boot_secondary` @`0x603c08ac` [C] | Secondary WFE stub at `0x1200` |
| 6 | The kernel never writes `GICD_ITARGETSR`. Its `irq_select_affinity` only reads the target byte back | `gic_dist_init` @`0x604951c8`, `0x6011feb0` [C] | CPU0 stub at `0x1000` presets the targets |
| 7 | Per-CPU timers: clockevents ch0/2/3/4 → IRQ 153/155/156/157; hwtimer0..3 = ch5..8 → IRQ 158..161 | tables `0x604d8d80`, `0x603c9354` [C]; hwtimerN = CPU N [I] | Target bytes; `cxd4115_timer.c` |
| 8 | The timer block follows the CXD4115 protocol (clocksource ch1 `0x1031`, periodic `0x1102`, one-shot `0x112`) | kernel timer code [C] | `cxd4115_timer.c` adapted from a5000 (GPL-2.0) |
| 9 | Without `-icount`, ~15 µs hwtimer deadlines expire before they are armed (3,254 warnings per run) | E0522 §0.6 [C] | `run.sh` default `-icount shift=0,sleep=on` [A] |
| 10 | The inputs come from two firmware versions: the kernel is a 2021 build, while rootfs and `/usr` are 2019 (`38111_03.2019052701`) | E0521 §1 [C] | Not fixed; modules load because vermagic matches |

## Known gaps and noise (not blocking the shell)

- All SPIs other than the timers target CPU0 [A]. No CXD90045 GICD dump exists yet.
- `osal_ulogio.ko` fails: `/setting/ulogio.bin` is absent [C].
- `sata_drv.ko` and `msen_drv.ko` are not present in `/usr/kmod` [C].
- Compressed `dnflasha*` mounts fail `ENXIO` (`AL_EMMC_BLK_UZ … Information table is invalid`). init falls back to the uncompressed partitions [C].
- The eMMC driver prints `execute_tuning … forbiden for non-HS200 mode` on every access (~900 lines per run) [C].
- `/system` (`nflasha3`) on `emmc.img` is an empty FAT. av-cam and the other boot-set files are only supplied as preloads [C].
- The watchdog at `0xF2402000` is unmodelled, although the main cmdline sets `wdt.mode=1` [C]. Today it is harmless because unmapped accesses are ignored [I].
- `nflasha1: bogus number of reserved sectors`: the updater partition on `emmc.img` is not FAT [C].

## Next possible steps (suggested order)

For each step, check `Oein/a5000-emulator` first for the CXD90014 equivalent.

1. **Find what av-cam waits on.** No `liro-*` IRQ has fired. The a5000 analogue is
   `docs/RE_AVCAM_IRQ_BOOT.md`: the boot checkpoint `prof` words in RAM, plus the "Liro Start"
   task blocked before sub-micro traffic. Locate the a6400 av-cam checkpoint variables and read them
   from the QEMU monitor (`xp`) to find the blocking task and wait object.
2. **Sub-micro link.** On the a5000, this is the SIO 128-byte exchange triggered by a GPIO strobe,
   with frames replayed from a real snapshot (`docs/RE_SUBMICRO_LINK.md`, `a5k_submicro_*`). Recover
   the CXD90045 SIO base/IRQs and the frame format from av-cam.
3. **Bootloader hand-off page.** The a5000 preloads `0x80021000` (boot reason at +0x4C, read by
   `upm.ko`). The a6400 cmdline has `wbimem=0x1000@0x20021000` and `pm.resume=0x20021054`. Check what
   `upm.ko` and av-cam read there [I: likely the same role].
4. **ADC / keys and front-end units.** ADC channels must read as "no key pressed". Front-end and
   sensor-interface IRQs are needed once live view runs (a5000 §1.1 IRQ table).
5. **Watchdog.** Model `0xF2402000` as a CXD4115 watchdog channel (the a5000 model has `watchdog=true`)
   before long runs, so a real expiry is visible instead of silently ignored.
6. **Display.** Sugilite/`dmp_smaph` scan-out to a QEMU console. The a5000 has `dmp_smaph.c`,
   `cxd90014_vph.c` and `a5k_fbview.c`. The OSD format is known from E0298 (640×480 RGBA4444).
7. **Optional real-camera verification (read-only, standing authorization):** one 4 KiB read of
   the GIC distributor page `0xF8001000` would replace the CPU0 target default. It would also confirm
   inference 7.
8. **Housekeeping.** Build `emmc.img` reproducibly from the firmware partition images, with a
   script and hashes. Pick one firmware release for the kernel, rootfs and `/usr`. Silence or rate-limit
   the eMMC tuning warning once its cause is understood. Add `ulogio.bin` if a real copy exists.

## Reproduce

```bash
./setup-qemu.sh && cd "$QEMU_TREE/build" && ninja qemu-system-arm qemu-img   # see README.md
TIMEOUT=110 ./run.sh
```

Records: [E0521](../docs/evidence/0521-qemu-a6400-main-firmware-boots-to-avcam.md),
[E0522](../docs/evidence/0522-qemu-a6400-smp-four-cores-and-cxd4115-timers.md).
Receipts: `artifacts/qemu-a6400/e0521/`, `artifacts/qemu-a6400/e0522/`.
